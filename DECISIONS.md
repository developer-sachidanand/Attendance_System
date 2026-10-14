# DECISIONS.md

1. **Indexes.** `employees.emp_code` and `attendance_logs (emp_code, date)` are unique natural-key guarantees. `attendance_logs (date desc, emp_code)` supports the attendance list sort; `(emp_code, date desc)` supports employee/month queries. Employee `department, joined_on` and `joined_on` support report eligibility and department counts. I did not add a separate status index because it is less selective and the list's required sort is more valuable.
2. **Punch-in race.** Both requests calculate the same natural key, then attempt an insert. MongoDB accepts one unique-index write. The other receives `DuplicateKeyError`, which the API maps to 409. There is no check-then-insert window.
3. **Ties.** The leaderboard uses MongoDB `$rank`, so equal late totals receive the same competition rank. It filters on rank after ranking; therefore every tied employee at a rank no greater than `limit` is returned.
4. **Headcount.** The department summary starts at eligible employee documents, then left-joins monthly logs. An employee with no logs still has one preserved row and contributes one headcount.
5. **At 100x scale** I would maintain a partitioned, pre-aggregated daily attendance collection for reports while retaining the source logs as the audit record.
