-- dataset: employees
-- anti-pattern: DISTINCT on a join that cannot produce duplicates ((emp_no, dept_no) is the dept_emp primary key) - pays for a dedup sort/temp table
-- tags: antipattern, redundant-distinct
SELECT DISTINCT e.emp_no, e.last_name
FROM employees e
JOIN dept_emp de ON de.emp_no = e.emp_no
WHERE de.dept_no = 'd009'
