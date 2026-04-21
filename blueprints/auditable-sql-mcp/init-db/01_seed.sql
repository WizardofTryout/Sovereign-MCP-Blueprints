-- =============================================================================
-- Sovereign MCP Blueprints — auditable-sql-mcp
-- Database Seed Script
--
-- This script runs automatically when the postgres-demo container starts.
-- It creates a read-only role and populates demo tables with anonymised
-- sample data for testing the MCP server.
--
-- In production: replace this with your actual schema migrations.
-- =============================================================================

-- Create a read-only role (defence-in-depth: even if the MCP server
-- accidentally attempts a write, the role has no write privileges)
CREATE ROLE mcp_readonly_role NOLOGIN;

-- Grant minimal privileges
GRANT CONNECT ON DATABASE enterprise_db TO mcp_readonly_role;
GRANT USAGE ON SCHEMA public TO mcp_readonly_role;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO mcp_readonly_role;

-- Apply the read-only role to the application user
GRANT mcp_readonly_role TO readonly_user;

-- Default privileges for future tables
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT ON TABLES TO mcp_readonly_role;

-- =============================================================================
-- Demo: customers table (PII-free schema — names/emails never stored here)
-- =============================================================================
CREATE TABLE IF NOT EXISTS customers (
    customer_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    region               CHAR(2)          NOT NULL,  -- ISO 3166-1 alpha-2
    segment              VARCHAR(20)      NOT NULL CHECK (segment IN ('premium', 'standard', 'trial')),
    account_created_at   TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    last_activity_at     TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    total_orders         INTEGER          NOT NULL DEFAULT 0,
    lifetime_value_eur   NUMERIC(12, 2)   NOT NULL DEFAULT 0.00
);

CREATE INDEX IF NOT EXISTS idx_customers_region ON customers (region);
CREATE INDEX IF NOT EXISTS idx_customers_segment ON customers (segment);

-- Seed with anonymised demo data
INSERT INTO customers (region, segment, account_created_at, last_activity_at, total_orders, lifetime_value_eur)
SELECT
    region,
    segment,
    NOW() - (RANDOM() * INTERVAL '730 days'),
    NOW() - (RANDOM() * INTERVAL '30 days'),
    FLOOR(RANDOM() * 50)::INTEGER,
    ROUND((RANDOM() * 5000)::NUMERIC, 2)
FROM
    UNNEST(ARRAY['DE','DE','DE','DE','FR','FR','NL','NL','PL','ES']) AS region,
    UNNEST(ARRAY['premium','standard','standard','trial','standard','premium','trial','standard','standard','premium']) AS segment;

-- =============================================================================
-- Demo: orders table (references customer_id, no PII)
-- =============================================================================
CREATE TABLE IF NOT EXISTS orders (
    order_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_id     UUID NOT NULL REFERENCES customers (customer_id),
    order_date      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    status          VARCHAR(20) NOT NULL CHECK (status IN ('pending', 'completed', 'refunded')),
    amount_eur      NUMERIC(10, 2) NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_orders_customer ON orders (customer_id);
CREATE INDEX IF NOT EXISTS idx_orders_date ON orders (order_date DESC);

GRANT SELECT ON orders TO mcp_readonly_role;
