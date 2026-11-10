#!/bin/bash
# One-time setup of a fresh RDS database plus the first deployment.
# Run once, as root, on the API server (the only machine that can reach RDS):
#   sudo /opt/project-dashboard/first-setup.sh
#
#   1. database user "dashboard" owns the database        (as dbadmin)
#   2. deploy.sh: migrations create the tables, API starts (as dashboard)
#   3. least-privilege user "size_lambda" for the Lambda   (as dbadmin)
#
# It reads the dbadmin and size_lambda passwords from SSM. After the setup those
# two were removed from the instance role (least privilege): to run this again,
# add their parameter ARNs back to ec2-role-policy.json for the duration.
set -euo pipefail
cd "$(dirname "$0")"
source ./config.sh

# The RDS CA bundle, taken from the application image itself - the same file
# the API uses to verify the database's certificate.
docker pull --quiet "$IMAGE"
docker run --rm --entrypoint cat "$IMAGE" /app/certs/rds-global-bundle.pem > "$CA_BUNDLE"
echo "CA bundle: $(grep -c 'BEGIN CERTIFICATE' "$CA_BUNDLE") certificates"

ADMIN_PASSWORD=$(param db/admin-password)
APP_PASSWORD=$(param db/app-password)
LAMBDA_PASSWORD=$(param db/lambda-password)
export ADMIN_PASSWORD APP_PASSWORD LAMBDA_PASSWORD

echo "== 1. application user and database owner"
psql_as "$DB_ADMIN_USER" ADMIN_PASSWORD < db-setup.sql

echo "== 2. migrations and first start of the API"
./deploy.sh

echo "== 3. least-privilege user for the size-limit Lambda"
{ echo '\getenv password LAMBDA_PASSWORD'; cat grants.sql; } |
  psql_as "$DB_ADMIN_USER" ADMIN_PASSWORD -v role="$DB_LAMBDA_USER" -v database="$DB_NAME"

echo "== done: roles in the database"
psql_as "$DB_ADMIN_USER" ADMIN_PASSWORD -c "\du dashboard|size_lambda|dbadmin"
