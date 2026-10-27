-- dataset: employees
-- tags: natural, aggregate, join
SELECT d.dept_name, AVG(s.salary) AS avg_salary
FROM departments d
JOIN dept_emp de ON de.dept_no = d.dept_no
JOIN salaries s ON s.emp_no = de.emp_no
WHERE de.to_date = '9999-01-01' AND s.to_date = '9999-01-01'
GROUP BY d.dept_name
ORDER BY d.dept_name
