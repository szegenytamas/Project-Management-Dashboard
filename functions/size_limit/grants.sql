-- Database user for the size-limit Lambda: least privilege, column by column.
--
-- Run once as the database owner, with psql variables, e.g. on the EC2 host:
--   psql "host=<rds-endpoint> dbname=dashboard user=dashboard sslmode=verify-full sslrootcert=..." \
--        -v role=size_lambda -v password='<from SSM>' -v database=dashboard -f grants.sql
--
-- The Lambda can therefore:
--   * lock a project row (SELECT ... FOR UPDATE needs UPDATE on a column),
--   * read which S3 keys belong to registered documents,
--   * write projects.total_size_bytes,
-- and NOTHING else: no user data, no password hashes, no deletes, no DDL.

CREATE ROLE :"role" LOGIN PASSWORD :'password';
GRANT CONNECT ON DATABASE :"database" TO :"role";
GRANT USAGE ON SCHEMA public TO :"role";
GRANT SELECT (id), UPDATE (total_size_bytes) ON projects TO :"role";
GRANT SELECT (project_id, storage_key) ON documents TO :"role";
