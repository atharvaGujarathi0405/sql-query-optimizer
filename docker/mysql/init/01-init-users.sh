#!/bin/bash
# Runs once, on first start with an empty data volume. Credentials come from
# docker-compose's environment section (sourced from .env).
set -euo pipefail

# Escape single quotes for use inside SQL string literals.
esc() { printf '%s' "${1//\'/\'\'}"; }

ADMIN_USER=$(esc "$OPTIMIZER_ADMIN_USER")
ADMIN_PASS=$(esc "$OPTIMIZER_ADMIN_PASSWORD")
RO_USER=$(esc "$OPTIMIZER_READONLY_USER")
RO_PASS=$(esc "$OPTIMIZER_READONLY_PASSWORD")

{
  echo "CREATE USER IF NOT EXISTS '${ADMIN_USER}'@'%' IDENTIFIED BY '${ADMIN_PASS}';"
  echo "CREATE USER IF NOT EXISTS '${RO_USER}'@'%' IDENTIFIED BY '${RO_PASS}';"
  # reader reads performance_schema for rows-examined metrics; SELECT only.
  echo "GRANT SELECT ON performance_schema.* TO '${RO_USER}'@'%';"
  IFS=',' read -ra SCHEMAS <<< "${MYSQL_DATABASE},${DATASET_SCHEMAS}"
  for raw in "${SCHEMAS[@]}"; do
    db="${raw//[^A-Za-z0-9_]/}"
    echo "CREATE DATABASE IF NOT EXISTS \`${db}\`;"
    echo "GRANT ALL PRIVILEGES ON \`${db}\`.* TO '${ADMIN_USER}'@'%';"
    echo "GRANT SELECT ON \`${db}\`.* TO '${RO_USER}'@'%';"
  done
  echo "FLUSH PRIVILEGES;"
} | mysql --protocol=socket -uroot -p"$MYSQL_ROOT_PASSWORD"

echo "01-init-users: ${OPTIMIZER_ADMIN_USER} (ALL) and ${OPTIMIZER_READONLY_USER} (SELECT) granted on ${MYSQL_DATABASE},${DATASET_SCHEMAS}"
