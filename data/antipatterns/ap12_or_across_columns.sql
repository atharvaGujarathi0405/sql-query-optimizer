-- dataset: employees
-- anti-pattern: OR across dept_no and from_date (different columns)
-- tags: antipattern, or-across-columns
SELECT emp_no, dept_no
FROM dept_emp
WHERE dept_no = 'd009' OR from_date = '1990-01-01'
