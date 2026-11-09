# Shared settings for the scripts in this folder (sourced, not executed).
# Not secret: passwords and the JWT key are read from SSM Parameter Store at run time.
# shellcheck disable=SC2034  # the variables are used by the scripts that source this file

REGION="eu-north-1"
IMAGE="ghcr.io/szegenytamas/project-management-dashboard:${IMAGE_TAG:-latest}"
DB_HOST="project-dashboard-db.c5w42quwc2cw.eu-north-1.rds.amazonaws.com"
DB_NAME="dashboard"
DB_ADMIN_USER="dbadmin"
DB_APP_USER="dashboard"
DB_LAMBDA_USER="size_lambda"
S3_BUCKET="project-dashboard-documents-192274357234"

APP_DIR="/opt/project-dashboard"
ENV_FILE="/etc/project-dashboard.env"   # root-only (600): contains secrets
CA_BUNDLE="$APP_DIR/rds-global-bundle.pem"

# Read one SecureString from Parameter Store (decrypted in memory, never echoed).
param() {
  aws ssm get-parameter --region "$REGION" --with-decryption \
    --name "/project-dashboard/$1" --query Parameter.Value --output text
}

# psql in a throwaway container, TLS verified against the RDS CA bundle.
# Usage: psql_as <user> <password-env-var-name> [psql args...] < script.sql
psql_as() {
  local user=$1 pw_var=$2; shift 2
  docker run --rm -i \
    -v "$CA_BUNDLE:/ca.pem:ro" \
    -e PGPASSWORD="${!pw_var}" \
    -e APP_PASSWORD="${APP_PASSWORD:-}" -e LAMBDA_PASSWORD="${LAMBDA_PASSWORD:-}" \
    postgres:17-alpine \
    psql "host=$DB_HOST port=5432 dbname=$DB_NAME user=$user sslmode=verify-full sslrootcert=/ca.pem" \
         -v ON_ERROR_STOP=1 "$@"
}
