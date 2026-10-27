-- dataset: employees
-- tags: natural, top-n, join
SELECT e.emp_no, e.last_name, s.salary
FROM employees e
JOIN salaries s ON s.emp_no = e.emp_no
WHERE s.to_date = '9999-01-01'
ORDER BY s.salary DESC, e.emp_no
LIMIT 10
