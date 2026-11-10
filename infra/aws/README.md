# AWS configuration files

JSON documents used by the AWS CLI commands in [`docs/aws.md`](../../docs/aws.md)
(`--… file://infra/aws/<file>`). Keeping them in the repository makes every policy
reviewable and versioned together with the code that relies on it.

| File | Used for |
|---|---|
| `s3-bucket-policy.json` | Bucket policy: deny any request that is not encrypted in transit (HTTPS only) |
| `s3-lifecycle.json` | Lifecycle rule: clean up incomplete multipart uploads after 1 day |
| `ec2-role-policy.json` | Inline policy of `project-dashboard-ec2-role`: document objects in the bucket, read the API's two SSM parameters (app DB password, JWT key) |
| `lambda-role-policy.json` | Inline policy of `project-dashboard-lambda-role`: list `projects/*`, delete objects there |
| `github-oidc-trust-policy.json` | Trust policy of `project-dashboard-github-deploy`: only GitHub OIDC tokens for this repository's `production` environment |
| `github-deploy-policy.json` | Permission policy of that role: run the deploy document on the API server, read the result |
| `ssm-deploy-document.json` | SSM document `ProjectDashboard-Deploy`: runs `deploy.sh` with a validated image tag |
| `ec2/user-data.sh` | First-boot script of the API server: installs Docker |
| `ec2/config.sh` | Shared settings for the server scripts (no secrets; those come from SSM) |
| `ec2/db-setup.sql` | One-time, as `dbadmin`: create the `dashboard` user and make it the database owner |
| `ec2/first-setup.sh` | One-time: `db-setup.sql` → `deploy.sh` → the Lambda's user from `functions/size_limit/grants.sql` |
| `ec2/deploy.sh` | Every deployment: env file from SSM, pull image, migrate, restart the API, wait until ready |

The server scripts were dry-run against a local PostgreSQL 17 configured like RDS (a master
user without superuser rights owning the initial database) and are shellcheck-clean.

Account `192274357234`, region `eu-north-1`. No secrets belong in this folder.
