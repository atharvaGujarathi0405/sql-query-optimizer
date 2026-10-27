-- dataset: employees
-- tags: natural, join
SELECT d.dept_name, e.first_name, e.last_name, dm.from_date
FROM dept_manager dm
JOIN employees e ON e.emp_no = dm.emp_no
JOIN departments d ON d.dept_no = dm.dept_no
WHERE dm.to_date = '9999-01-01'
ORDER BY d.dept_name
