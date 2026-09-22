-- Least-privilege roles. Run as a superuser, once, after schema.sql.
--
-- Set the password out of band:
--   psql -c "ALTER ROLE agentstack_app PASSWORD '...'"
-- and store it in the Keychain, never in this file.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agentstack_app') THEN
        CREATE ROLE agentstack_app LOGIN;
    END IF;
END
$$;

REVOKE ALL ON ALL TABLES IN SCHEMA public FROM agentstack_app;

-- The event log is append-only for the application. No UPDATE, no DELETE,
-- no TRUNCATE. Correcting the record means writing a new event, which is
-- what an audit trail is for.
GRANT SELECT, INSERT ON events TO agentstack_app;

-- Decisions accept the outcome column being filled in later.
GRANT SELECT, INSERT, UPDATE ON decisions TO agentstack_app;

-- Escalations are resolved and marked notified in place.
GRANT SELECT, INSERT, UPDATE ON escalations TO agentstack_app;

GRANT SELECT, INSERT ON restore_tests TO agentstack_app;
GRANT USAGE, SELECT ON SEQUENCE restore_tests_id_seq TO agentstack_app;

-- No schema changes from the app role. Migrations run as the owner.
REVOKE CREATE ON SCHEMA public FROM agentstack_app;
