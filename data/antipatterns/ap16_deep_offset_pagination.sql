-- dataset: employees
-- anti-pattern: deep OFFSET pagination reads and discards 2,000,000 rows; keyset pagination is the rewrite
-- tags: antipattern, deep-offset
SELECT emp_no, from_date, salary
FROM salaries
ORDER BY emp_no, from_date
LIMIT 20 OFFSET 2000000
