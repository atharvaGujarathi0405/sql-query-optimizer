-- dataset: employees
-- anti-pattern: LIKE with a leading wildcard on last_name
-- tags: antipattern, leading-wildcard-like, non-sargable
SELECT emp_no, last_name
FROM employees
WHERE last_name LIKE '%ston'
