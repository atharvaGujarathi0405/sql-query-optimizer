-- dataset: employees
-- anti-pattern: HAVING on a grouping column: filters after aggregating every group instead of before
-- tags: antipattern, having-instead-of-where
SELECT dept_no, COUNT(*) AS n
FROM dept_emp
GROUP BY dept_no
HAVING dept_no = 'd005'
