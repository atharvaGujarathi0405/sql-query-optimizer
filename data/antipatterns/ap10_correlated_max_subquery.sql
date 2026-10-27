-- dataset: employees
-- anti-pattern: correlated MAX() subquery per row to find each employee's top salary; window function or grouped join is the rewrite
-- tags: antipattern, correlated-subquery
SELECT e.emp_no, s.salary
FROM employees e
JOIN salaries s ON s.emp_no = e.emp_no
WHERE e.hire_date >= '1999-01-01'
  AND s.salary = (SELECT MAX(s2.salary) FROM salaries s2 WHERE s2.emp_no = e.emp_no)
