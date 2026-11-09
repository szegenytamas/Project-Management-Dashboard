#!/bin/bash
# (Re)deploy the API on this server. Safe to run again for every new image:
#   sudo IMAGE_TAG=sha-<commit> /opt/project-dashboard/deploy.sh     # a specific build
#   sudo /opt/project-dashboard/deploy.sh                            # :latest
#
# 1. writes the environment file from SSM Parameter Store (root-only),
# 2. pulls the image from GHCR,
# 3. runs the database migrations (a one-off container),
# 4. replaces the running API container and waits until it is ready.
set -euo pipefail
source "$(dirname "$0")/config.sh"

umask 077
{
  echo "ENVIRONMENT=production"
  echo "POSTGRES_HOST=$DB_HOST"
  echo "POSTGRES_PORT=5432"
  echo "POSTGRES_DB=$DB_NAME"
  echo "POSTGRES_USER=$DB_APP_USER"
  echo "POSTGRES_PASSWORD=$(param db/app-password)"
  echo "POSTGRES_SSL_MODE=verify-full"
  echo "JWT_SECRET_KEY=$(param jwt-secret-key)"
  echo "S3_BUCKET=$S3_BUCKET"
  echo "AWS_REGION=$REGION"
} > "$ENV_FILE"
echo "Environment written to $ENV_FILE ($(stat -c %a "$ENV_FILE"), owner $(stat -c %U "$ENV_FILE"))"

docker pull --quiet "$IMAGE"
echo "Image: $(docker image inspect -f '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$IMAGE" | cut -c1-7)"

docker run --rm --env-file "$ENV_FILE" "$IMAGE" python -m project_dashboard.db.migrate

docker rm -f api >/dev/null 2>&1 || true
docker run -d --name api --restart unless-stopped --env-file "$ENV_FILE" -p 80:8000 "$IMAGE" >/dev/null

for _ in $(seq 1 30); do
  if curl -fs http://localhost/health/ready >/dev/null; then
    echo "API ready:"; curl -s http://localhost/health/ready; echo
    exit 0
  fi
  sleep 1
done
echo "API did not become ready - logs:"; docker logs --tail 50 api; exit 1
