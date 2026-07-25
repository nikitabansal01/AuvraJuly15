-- Supabase-only post-bootstrap hardening for AUVRA's backend-only database.
--
-- Run as the owner of the newly created public-schema objects, after
-- `alembic upgrade head` and before importing user data.  This file is kept
-- separate from the portable Alembic baseline because ordinary PostgreSQL
-- installations do not have Supabase's anon/authenticated roles.

\set ON_ERROR_STOP on

DO $check_roles$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon')
       OR NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        RAISE EXCEPTION
            'Expected Supabase roles anon and authenticated were not found';
    END IF;
END
$check_roles$;

BEGIN;

-- Remove current Data API access.  PUBLIC is included because PostgreSQL
-- privileges are additive; revoking only a direct role grant is not a deny.
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public
    FROM PUBLIC, anon, authenticated;
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public
    FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA public
    FROM PUBLIC, anon, authenticated;
REVOKE CREATE ON SCHEMA public
    FROM PUBLIC, anon, authenticated;

-- Keep later migrations backend-only when they are run by this same owner.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    REVOKE ALL PRIVILEGES ON TABLES FROM PUBLIC, anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    REVOKE ALL PRIVILEGES ON SEQUENCES FROM PUBLIC, anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC, anon, authenticated;

COMMIT;

-- Both result sets must be empty.  These checks do not reveal row data.
SELECT grantee, table_name, privilege_type
FROM information_schema.table_privileges
WHERE table_schema = 'public'
  AND grantee IN ('PUBLIC', 'anon', 'authenticated')
ORDER BY grantee, table_name, privilege_type;

SELECT grantee, object_name, privilege_type
FROM information_schema.usage_privileges
WHERE object_schema = 'public'
  AND object_type = 'SEQUENCE'
  AND grantee IN ('PUBLIC', 'anon', 'authenticated')
ORDER BY grantee, object_name, privilege_type;
