"""Employee Attendance & Analytics API."""
import os, re
from datetime import date as D, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator
from pymongo import MongoClient, ASCENDING, DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

load_dotenv(); UTC=timezone.utc; IST=timezone(timedelta(hours=5,minutes=30)); PRESENT={"PRESENT","WFH","ON_DUTY"}; ALL=PRESENT|{"ABSENT","LEAVE"}; MINMS,MAXMS=100000000000,4102444800000
db=MongoClient(os.getenv("MONGO_URI","mongodb://localhost:27017"),tz_aware=True)[os.getenv("MONGO_DB","attendance_db")]
app=FastAPI(title="Employee Attendance & Analytics API",version="2.0.0")
def err(s): raise HTTPException(422,s)
def day(s):
 try:return D.fromisoformat(s)
 except (ValueError,TypeError):err("invalid date; expected YYYY-MM-DD")
def month(s):
 if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])",s):err("invalid month; expected YYYY-MM")
 a=D.fromisoformat(s+'-01'); return a,D(a.year+(a.month==12),1 if a.month==12 else a.month+1,1)
def now():return datetime.now(UTC).replace(microsecond=0)
def dt(ms):return datetime.fromtimestamp(ms//1000,UTC).replace(microsecond=0)
def ms(x):
 if x is None:return None
 return int((x if x.tzinfo else x.replace(tzinfo=UTC)).timestamp())*1000
def rnd(x,n):return float(Decimal(str(x)).quantize(Decimal('1').scaleb(-n),rounding=ROUND_HALF_UP))
def shift(ds,t,tomorrow=False):
 x=day(ds)+timedelta(days=tomorrow); h,m=map(int,t.split(':')); return datetime(x.year,x.month,x.day,h,m,tzinfo=IST)
def attendance_day(x,e):
 local=x.astimezone(IST); return (local.date()-timedelta(days=1) if e['shift_end']<=e['shift_start'] and local.strftime('%H:%M')<e['shift_end'] else local.date()).isoformat()
def calc(e,ds,pin,pout,status):
 if status not in PRESENT:return {'punch_in':None,'punch_out':None,'work_hours':None,'late_minutes':0,'overtime_minutes':0,'half_day':False}
 if pin is None:err('a presence status requires punch_in')
 late=(pin.astimezone(IST)-shift(ds,e['shift_start'])).total_seconds(); r={'punch_in':pin,'punch_out':pout,'late_minutes':int(late//60) if late>600 else 0,'work_hours':None,'overtime_minutes':0,'half_day':False}
 if pout is None:return r
 sec=(pout-pin).total_seconds()
 if sec<=0 or sec>86400:err('punch_out must be after punch_in and within 24 hours')
 wh=rnd(sec/3600,2); over=int((pout.astimezone(IST)-shift(ds,e['shift_end'],e['shift_end']<=e['shift_start'])).total_seconds()//60)
 return {**r,'work_hours':wh,'overtime_minutes':over if over>=30 else 0,'half_day':wh<4.5}
def apiv(x):
 if isinstance(x,datetime):return ms(x)
 if isinstance(x,list):return [apiv(i) for i in x]
 if isinstance(x,dict):return {k:apiv(v) for k,v in x.items() if k!='_id'}
 return x
def record(x):
 x=apiv(x); x.setdefault('history',[]); x.setdefault('half_day',False); x.setdefault('late_minutes',0); x.setdefault('overtime_minutes',0); return x
def roundexpr(x,n):
 f=10**n; return {'$divide':[{'$trunc':{'$add':[{'$multiply':[x,f]},.5]}},f]}

class M(BaseModel):model_config=ConfigDict(extra='ignore')
class Employee(M):
 emp_code:str=Field(pattern=r'^EMP\d{4,6}$'); name:str=Field(min_length=1,max_length=100); email:str=Field(max_length=120,pattern=r'^[^@\s]+@[^@\s]+\.[^@\s]+$'); department:str=Field(min_length=1,max_length=50); shift_start:str=Field(default='09:30',pattern=r'^([01]\d|2[0-3]):[0-5]\d$'); shift_end:str=Field(default='18:30',pattern=r'^([01]\d|2[0-3]):[0-5]\d$'); joined_on:str
 @field_validator('joined_on')
 @classmethod
 def vd(cls,v):day(v);return v
 @model_validator(mode='after')
 def shifts(self):
  if self.shift_start==self.shift_end:raise ValueError('shift_start must differ from shift_end')
  return self
def epoch(v):
 if v is not None and (type(v) is not int or not MINMS<=v<=MAXMS):raise ValueError('must be an epoch millisecond integer in the allowed range')
 return v
class In(M):
 emp_code:str; punched_at:Optional[StrictInt]=None
 _v=field_validator('punched_at')(epoch)
 @model_validator(mode='after')
 def required_value(self):
  if 'punched_at' in self.model_fields_set and self.punched_at is None:raise ValueError('punched_at cannot be null')
  return self
class Pin(In):
 status:str='PRESENT'
 @field_validator('status')
 @classmethod
 def st(cls,v):
  if v not in PRESENT:raise ValueError('status must be PRESENT, WFH, or ON_DUTY')
  return v
class Reg(M):
 status:Optional[str]=None; punch_in:Optional[StrictInt]=None; punch_out:Optional[StrictInt]=None; reason:str=Field(min_length=5,max_length=200); regularized_by:str=Field(min_length=1,max_length=50)
 _i=field_validator('punch_in')(epoch); _o=field_validator('punch_out')(epoch)
 @field_validator('status')
 @classmethod
 def sts(cls,v):
  if v is not None and v not in ALL:raise ValueError('invalid status')
  return v
 @model_validator(mode='after')
 def nn(self):
  if any(k in self.model_fields_set and getattr(self,k) is None for k in ('punch_in','punch_out')):raise ValueError('punch times cannot be null')
  return self

@app.on_event('startup')
def indexes():
 db.employees.create_index('emp_code',unique=True,name='employee_code_unique'); db.employees.create_index([('department',1),('joined_on',1)],name='department_joined'); db.employees.create_index('joined_on',name='joined_on'); db.attendance_logs.create_index([('emp_code',1),('date',1)],unique=True,name='attendance_key_unique'); db.attendance_logs.create_index([('date',-1),('emp_code',1)],name='attendance_list'); db.attendance_logs.create_index([('emp_code',1),('date',-1)],name='employee_date')
@app.get('/health')
def health():
 try:db.command('ping')
 except PyMongoError:raise HTTPException(503,'MongoDB unavailable')
 return {'status':'ok'}
@app.post('/employees',status_code=201)
def create_employee(b:Employee):
 x=b.model_dump();x['created_at']=now()
 try:db.employees.insert_one(x)
 except DuplicateKeyError:raise HTTPException(409,'emp_code already exists')
 return apiv(x)
@app.get('/employees')
def employees(department:Optional[str]=None,page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100)):
 q={'department':department} if department is not None else {}; return {'items':[apiv(x) for x in db.employees.find(q,{'_id':0}).sort('emp_code',1).skip((page-1)*page_size).limit(page_size)],'total':db.employees.count_documents(q),'page':page,'page_size':page_size}
@app.post('/attendance/punch-in',status_code=201)
def punchin(b:Pin):
 e=db.employees.find_one({'emp_code':b.emp_code})
 if not e:raise HTTPException(404,'employee not found')
 x=dt(b.punched_at) if b.punched_at is not None else now(); ds=attendance_day(x,e); doc={'emp_code':b.emp_code,'date':ds,'status':b.status,'history':[],**calc(e,ds,x,None,b.status)}
 try:db.attendance_logs.insert_one(doc)
 except DuplicateKeyError:raise HTTPException(409,'already punched in for this date')
 return record(doc)
@app.post('/attendance/punch-out')
def punchout(b:In):
 e=db.employees.find_one({'emp_code':b.emp_code})
 if not e:raise HTTPException(404,'employee not found')
 x=dt(b.punched_at) if b.punched_at is not None else now(); old=db.attendance_logs.find_one({'emp_code':b.emp_code,'punch_in':{'$ne':None,'$lte':x}},sort=[('punch_in',-1)])
 if not old:raise HTTPException(404,'no punch-in found')
 if old.get('punch_out') is not None:raise HTTPException(409,'record already punched out')
 r=db.attendance_logs.find_one_and_update({'_id':old['_id'],'punch_out':None},{'$set':calc(e,old['date'],old['punch_in'],x,old['status'])},return_document=ReturnDocument.AFTER)
 if not r:raise HTTPException(409,'record already punched out')
 return record(r)
def aq(emp,lo,hi,status):
 if lo:day(lo)
 if hi:day(hi)
 if lo and hi and lo>hi:err('date_from must not exceed date_to')
 q={};
 if emp is not None:q['emp_code']=emp
 if status is not None:q['status']=status
 if lo or hi:q['date']={**({'$gte':lo} if lo else {}),**({'$lte':hi} if hi else {})}
 return q
@app.get('/attendance')
def attendance(emp_code:Optional[str]=None,date_from:Optional[str]=None,date_to:Optional[str]=None,status:Optional[str]=None,page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100)):
 if status is not None and status not in ALL:err('invalid status')
 q=aq(emp_code,date_from,date_to,status);return {'items':[record(x) for x in db.attendance_logs.find(q).sort([('date',-1),('emp_code',1)]).skip((page-1)*page_size).limit(page_size)],'total':db.attendance_logs.count_documents(q),'page':page,'page_size':page_size}
@app.patch('/attendance/{emp_code}/{date}')
def regularize(emp_code:str,date:str,b:Reg):
 day(date);e=db.employees.find_one({'emp_code':emp_code})
 if not e:raise HTTPException(404,'employee not found')
 old=db.attendance_logs.find_one({'emp_code':emp_code,'date':date})
 if not old:raise HTTPException(404,'attendance record not found')
 st=b.status if 'status' in b.model_fields_set else old['status']; pin=dt(b.punch_in) if 'punch_in' in b.model_fields_set else old.get('punch_in'); pout=dt(b.punch_out) if 'punch_out' in b.model_fields_set else old.get('punch_out')
 if st in {'ABSENT','LEAVE'} and {'punch_in','punch_out'}&b.model_fields_set:err('absence statuses cannot include punch times')
 if st in PRESENT and (pin is None or attendance_day(pin,e)!=date):err('punch_in must belong to this attendance date')
 vals={'status':st,**calc(e,date,pin,pout,st)};ch={k:{'from':old.get(k),'to':vals[k]} for k in vals if old.get(k)!=vals[k]}
 if not ch:err('request changes nothing')
 r=db.attendance_logs.find_one_and_update({'_id':old['_id'],**{k:old.get(k) for k in vals}},{'$set':vals,'$push':{'history':{'at':now(),'by':b.regularized_by,'reason':b.reason,'changes':ch}}},return_document=ReturnDocument.AFTER)
 if not r:raise HTTPException(409,'record changed concurrently')
 return record(r)

def monthly_pipe(code,m):
 a,z=month(m); wd={'$and':[{'$gte':[{'$dayOfWeek':{'$dateFromString':{'dateString':'$date'}}},2]},{'$lte':[{'$dayOfWeek':{'$dateFromString':{'dateString':'$date'}}},6]}]}
 return [{'$match':{'emp_code':code,'date':{'$gte':a.isoformat(),'$lt':z.isoformat()}}},{'$group':{'_id':None,'present_days':{'$sum':{'$cond':[{'$and':[{'$in':['$status',list(PRESENT)]},wd]},{'$cond':[{'$ifNull':['$half_day',False]},.5,1]},0]}},'leave_days':{'$sum':{'$cond':[{'$eq':['$status','LEAVE']},1,0]}},'late_count':{'$sum':{'$cond':[{'$gt':[{'$ifNull':['$late_minutes',0]},0]},1,0]}},'total_late_minutes':{'$sum':{'$ifNull':['$late_minutes',0]}},'total_overtime_minutes':{'$sum':{'$ifNull':['$overtime_minutes',0]}}}}]
@app.get('/analytics/employees/{emp_code}/monthly')
def monthly(emp_code:str,month_:str=Query(alias='month')):
 a,z=month(month_); e=db.employees.find_one({'emp_code':emp_code})
 if not e:raise HTTPException(404,'employee not found')
 r=next(iter(db.attendance_logs.aggregate(monthly_pipe(emp_code,month_))),{}); joined=max(a,day(e['joined_on'])); work=sum(1 for n in range(max(0,(z-joined).days)) if (joined+timedelta(days=n)).weekday()<5); present=r.get('present_days',0)
 return {'emp_code':emp_code,'month':month_,'working_days':work,'present_days':present,'leave_days':r.get('leave_days',0),'late_count':r.get('late_count',0),'total_late_minutes':r.get('total_late_minutes',0),'total_overtime_minutes':r.get('total_overtime_minutes',0),'attendance_pct':rnd(present/work*100,4) if work else None}
def dept_pipe(m,dep=None):
 a,z=month(m); q={'joined_on':{'$lt':z.isoformat()}}; q.update({'department':dep} if dep is not None else {})
 return [{'$match':q},{'$lookup':{'from':'attendance_logs','let':{'c':'$emp_code'},'pipeline':[{'$match':{'$expr':{'$and':[{'$eq':['$emp_code','$$c']},{'$gte':['$date',a.isoformat()]},{'$lt':['$date',z.isoformat()]}]}}}],'as':'logs'}},{'$unwind':{'path':'$logs','preserveNullAndEmptyArrays':True}},{'$group':{'_id':'$emp_code','department':{'$first':'$department'},'present':{'$sum':{'$cond':[{'$in':['$logs.status',list(PRESENT)]},{'$cond':[{'$ifNull':['$logs.half_day',False]},.5,1]},0]}},'hours':{'$push':{'$cond':[{'$and':[{'$in':['$logs.status',list(PRESENT)]},{'$ne':['$logs.work_hours',None]}]},'$logs.work_hours','$$REMOVE']}},'late_count':{'$sum':{'$cond':[{'$gt':[{'$ifNull':['$logs.late_minutes',0]},0]},1,0]}},'late':{'$sum':{'$ifNull':['$logs.late_minutes',0]}},'leave':{'$sum':{'$cond':[{'$eq':['$logs.status','LEAVE']},1,0]}},'duty':{'$sum':{'$cond':[{'$eq':['$logs.status','ON_DUTY']},1,0]}}}},{'$group':{'_id':'$department','headcount':{'$sum':1},'present_days':{'$sum':'$present'},'arrays':{'$push':'$hours'},'late_count':{'$sum':'$late_count'},'total_late_minutes':{'$sum':'$late'},'leave_count':{'$sum':'$leave'},'on_duty_count':{'$sum':'$duty'}}},{'$set':{'flat':{'$reduce':{'input':'$arrays','initialValue':[],'in':{'$concatArrays':['$$value','$$this']}}}}},{'$project':{'_id':0,'department':'$_id','headcount':1,'present_days':1,'late_count':1,'total_late_minutes':1,'leave_count':1,'on_duty_count':1,'avg_work_hours':{'$cond':[{'$gt':[{'$size':'$flat'},0]},roundexpr({'$avg':'$flat'},2),None]}}},{'$sort':{'department':1}}]
@app.get('/analytics/departments/summary')
def deptsummary(month_:str=Query(alias='month'),department:Optional[str]=None):
 month(month_);return {'month':month_,'items':list(db.employees.aggregate(dept_pipe(month_,department)))}
def board_pipe(m,limit,dep=None):
 a,z=month(m); p=[{'$match':{'date':{'$gte':a.isoformat(),'$lt':z.isoformat()}}},{'$group':{'_id':'$emp_code','total_late_minutes':{'$sum':{'$ifNull':['$late_minutes',0]}},'late_count':{'$sum':{'$cond':[{'$gt':[{'$ifNull':['$late_minutes',0]},0]},1,0]}}}},{'$match':{'total_late_minutes':{'$gt':0}}},{'$lookup':{'from':'employees','localField':'_id','foreignField':'emp_code','as':'e'}},{'$unwind':'$e'}]
 if dep is not None:p.append({'$match':{'e.department':dep}})
 return p+[{'$setWindowFields':{'sortBy':{'total_late_minutes':-1},'output':{'rank':{'$rank':{}}}}},{'$match':{'rank':{'$lte':limit}}},{'$project':{'_id':0,'rank':1,'emp_code':'$_id','name':'$e.name','department':'$e.department','total_late_minutes':1,'late_count':1}},{'$sort':{'total_late_minutes':-1,'emp_code':1}}]
@app.get('/analytics/leaderboard/late')
def leaderboard(month_:str=Query(alias='month'),limit:int=Query(10,ge=1,le=50),department:Optional[str]=None):
 month(month_);return {'month':month_,'items':list(db.attendance_logs.aggregate(board_pipe(month_,limit,department)))}
def trend_pipe(dep,a,z):
 hi=z+timedelta(days=1); lo_dt=datetime(a.year,a.month,a.day,tzinfo=UTC); hi_dt=datetime(hi.year,hi.month,hi.day,tzinfo=UTC)
 return [{'$match':{'date':{'$gte':a.isoformat(),'$lte':z.isoformat()}}},{'$unionWith':{'coll':'employees','pipeline':[{'$match':{'department':dep}},{'$limit':1},{'$project':{'_id':0,'emp_code':1,'date':{'$literal':a.isoformat()},'status':{'$literal':'GAP'}}}]}},{'$lookup':{'from':'employees','localField':'emp_code','foreignField':'emp_code','as':'e'}},{'$unwind':'$e'},{'$match':{'e.department':dep}},{'$group':{'_id':'$date','present_count':{'$sum':{'$cond':[{'$in':['$status',list(PRESENT)]},{'$cond':[{'$ifNull':['$half_day',False]},.5,1]},0]}},'late_count':{'$sum':{'$cond':[{'$gt':[{'$ifNull':['$late_minutes',0]},0]},1,0]}}}},{'$project':{'_id':0,'day':{'$dateFromString':{'dateString':'$_id'}},'present_count':1,'late_count':1}},{'$densify':{'field':'day','range':{'step':1,'unit':'day','bounds':[lo_dt,hi_dt]}}},{'$set':{'date':{'$dateToString':{'date':'$day','format':'%Y-%m-%d'}},'present_count':{'$ifNull':['$present_count',0]},'late_count':{'$ifNull':['$late_count',0]}}},{'$lookup':{'from':'employees','let':{'d':'$date'},'pipeline':[{'$match':{'$expr':{'$and':[{'$eq':['$department',dep]},{'$lte':['$joined_on','$$d']}]}}},{'$count':'n'}],'as':'h'}},{'$set':{'headcount':{'$ifNull':[{'$arrayElemAt':['$h.n',0]},0]},'is_working_day':{'$and':[{'$gte':[{'$dayOfWeek':'$day'},2]},{'$lte':[{'$dayOfWeek':'$day'},6]}]}}},{'$set':{'attendance_rate':{'$cond':[{'$and':['$is_working_day',{'$gt':['$headcount',0]}]},{'$divide':['$present_count','$headcount']},None]}}},{'$setWindowFields':{'sortBy':{'day':1},'output':{'moving':{'$avg':'$attendance_rate','window':{'documents':[-6,0]}}}}},{'$project':{'_id':0,'day':0,'h':0,'date':1,'is_working_day':1,'headcount':1,'present_count':1,'late_count':1,'attendance_rate':roundexpr('$attendance_rate',4),'moving_avg_7d':roundexpr('$moving',4)}}]
@app.get('/analytics/departments/{department}/trend')
def trend(department:str,from_:str=Query(alias='from'),to:str=Query(...)):
 a,z=day(from_),day(to)
 if z<a:err('to must not precede from')
 if (z-a).days+1>92:err('range must not exceed 92 days')
 if not db.employees.find_one({'department':department}):raise HTTPException(404,'department not found')
 return {'department':department,'items':list(db.attendance_logs.aggregate(trend_pipe(department,a,z)))}
@app.get('/admin/explain/{endpoint}')
def explain(endpoint:str,emp_code:Optional[str]=None,month_:Optional[str]=Query(None,alias='month'),department:Optional[str]=None,limit:int=Query(10,ge=1,le=50),date_from:Optional[str]=None,date_to:Optional[str]=None,status:Optional[str]=None,from_:Optional[str]=Query(None,alias='from'),to:Optional[str]=None,page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100)):
 if endpoint=='attendance_list': c='attendance_logs';cmd={'find':c,'filter':aq(emp_code,date_from,date_to,status),'sort':{'date':-1,'emp_code':1},'skip':(page-1)*page_size,'limit':page_size}
 elif endpoint=='employee_monthly':
  if not emp_code or not month_:err('emp_code and month are required')
  c='attendance_logs';cmd={'aggregate':c,'pipeline':monthly_pipe(emp_code,month_),'cursor':{}}
 elif endpoint=='department_summary':
  if not month_:err('month is required')
  c='employees';cmd={'aggregate':c,'pipeline':dept_pipe(month_,department),'cursor':{}}
 elif endpoint=='late_leaderboard':
  if not month_:err('month is required')
  c='attendance_logs';cmd={'aggregate':c,'pipeline':board_pipe(month_,limit,department),'cursor':{}}
 elif endpoint=='department_trend':
  if not department or not from_ or not to:err('department, from and to are required')
  c='attendance_logs';cmd={'aggregate':c,'pipeline':trend_pipe(department,day(from_),day(to)),'cursor':{}}
 else:err('unknown endpoint')
 return {'endpoint':endpoint,'collection':c,'explain':db.command('explain',cmd,verbosity='executionStats')}
