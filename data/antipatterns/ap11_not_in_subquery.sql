-- dataset: employees
-- anti-pattern: NOT IN (subquery) used as an anti-join
-- tags: antipattern, not-in, anti-join
SELECT e.emp_no, e.first_name, e.last_name
FROM employees e
WHERE e.emp_no NOT IN (SELECT dm.emp_no FROM dept_manager dm)
  AND e.hire_date >= '1999-06-01'
