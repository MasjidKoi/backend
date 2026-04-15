-- Runs once when the PostgreSQL container is first created.

-- Create the "auth" schema for GoTrue tables
CREATE SCHEMA IF NOT EXISTS auth;
GRANT ALL ON SCHEMA auth TO masjidkoi;

-- Set default search_path for the masjidkoi role so GoTrue can find
-- its tables without a schema prefix. FastAPI uses schema-qualified
-- names or the public schema, so this is safe.
ALTER ROLE masjidkoi SET search_path = auth, public;
