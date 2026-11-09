-- One-time setup on RDS, run as the master user (dbadmin).
--
-- Creates the application's own database user and makes it the owner of the
-- database, so the API and the migrations never need the master credentials.
-- The password is taken from the environment (\getenv), not from the command
-- line, so it does not appear in process lists or shell history.

\getenv app_password APP_PASSWORD

CREATE ROLE dashboard LOGIN PASSWORD :'app_password';

-- RDS's master user is not a real superuser. To hand over ownership it must be
-- a member of the new owner role (PostgreSQL 16+ gives the creator only the
-- right to grant that membership, not the membership itself).
GRANT dashboard TO dbadmin;
ALTER DATABASE dashboard OWNER TO dashboard;

-- Since PostgreSQL 15 the "public" schema belongs to pg_database_owner, i.e. to
-- whoever owns the database - now dashboard. Nobody else may create tables there.
