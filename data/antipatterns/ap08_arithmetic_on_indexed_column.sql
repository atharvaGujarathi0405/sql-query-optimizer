-- dataset: employees
-- anti-pattern: arithmetic on the indexed key (emp_no - 1 = 10000) forces a full scan of salaries
-- tags: antipattern, function-on-column, non-sargable
SELECT emp_no, salary, from_date
FROM salaries
WHERE emp_no - 1 = 10000
