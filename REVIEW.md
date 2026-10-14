Listed every defect I found in the starter `app/main.py` (helpers and endpoints). For each one:

| # | Where (function / line) | What is wrong | How you'd notice it (test, input, or symptom) | How you fixed it |
|---|---|---|---|---|
| 1 | `health` | Returned 200 without checking MongoDB. | Stop MongoDB; readiness still reported success. | Added `ping` and returns 503 on driver errors. |
| 2 | `create_employee` | Used check-then-insert semantics and a naive server datetime. | Concurrent creates could both pass the check; output was not epoch milliseconds. | Added a unique index, catches duplicate-key errors, and stores UTC time. |
| 3 | `list_employees` | Applied the wrong offset, counted all employees even when filtered, and did not sort. | A filtered second page skipped too many records and had the wrong total. | Uses `(page - 1) * page_size`, filtered count, and `emp_code` sort. |
| 4 | timestamp handling | Used local machine time and accepted seconds-looking timestamps. | A host outside IST produces the wrong date; `1783312500` was accepted. | Validates strict epoch milliseconds, stores UTC BSON datetimes, and converts to IST only for business rules. |
| 5 | `compute_late_minutes` | Calculated from a UTC/naive date and did not model the strict ten-minute grace boundary. | 09:40:01 IST and overnight shifts give incorrect values. | Uses IST shift instants and applies R2 exactly. |
| 6 | work/overtime helpers | Used Python banker's rounding, did not enforce the 30-minute overtime threshold, and ignored overnight ends. | A half-cent hour value or an overnight punch-out fails the stated rules. | Uses decimal half-up, thresholding, and next-day shift ends. |
| 7 | `punch_in` | Did not check for an unknown employee, accepted invalid statuses, used a race-prone pre-check, and exposed Mongo `_id` as `id`. | Parallel punch-ins could both be accepted; API returned an undocumented field. | Validates inputs, uses the unique natural-key index, maps duplicate keys to 409, and never exposes `_id`. |
| 8 | `list_attendance` | Loaded and sorted the complete collection in Python. | Slow/memory-heavy on 100k records; sort order was incomplete. | Performs indexed Mongo sort, count, skip, and limit with the required compound ordering. |
| 9 | missing endpoints | Punch-out, regularization, analytics, and explain endpoints were absent. | Contract routes returned 404. | Implemented all contract routes, including atomic updates and aggregation pipelines. |
| 10 | correction history | No correction behavior existed. | Regularizations could not preserve before/after audit values. | Appends one atomic history entry containing only actual changes. |
| 11 | startup | No indexes were created. | Duplicate attendance records and unindexed report/list queries. | Creates idempotent unique and query-supporting indexes at startup. |

I also checked that `load_dotenv()` does not override pre-existing environment variables, which is correct for the required injected configuration.
