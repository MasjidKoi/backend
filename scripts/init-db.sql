-- Runs once when the PostgreSQL container is first created.

-- Create the "auth" schema for GoTrue tables
CREATE SCHEMA IF NOT EXISTS auth;
GRANT ALL ON SCHEMA auth TO masjidkoi;

-- public first: our app tables land in public by default.
-- auth second: GoTrue sets search_path in its own connection string.
ALTER ROLE masjidkoi SET search_path = public, auth;
