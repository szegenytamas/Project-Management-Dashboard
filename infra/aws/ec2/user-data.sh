#!/bin/bash
# EC2 user data: runs once, as root, at the first boot of the API server.
# Installs Docker; everything else (the API, migrations, the psql client)
# runs in containers. The AWS CLI and the SSM agent (for Session Manager)
# are preinstalled on Amazon Linux 2023.
set -euo pipefail

dnf install -y docker
systemctl enable --now docker

# Marker so `cat /var/log/project-dashboard-bootstrap.log` shows it finished.
echo "bootstrap finished $(date --iso-8601=seconds)" >> /var/log/project-dashboard-bootstrap.log
