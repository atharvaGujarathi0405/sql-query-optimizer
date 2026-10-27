-- dataset: sakila
-- anti-pattern: VARCHAR column compared with a numeric literal: the column is cast per row, so no index could be used
-- tags: antipattern, implicit-cast, non-sargable
SELECT address_id, address, postal_code
FROM address
WHERE postal_code = 35200
