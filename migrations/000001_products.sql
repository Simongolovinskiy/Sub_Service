-- Table: products
-- Description: Stores catalog items (goods, services, etc.)
CREATE TABLE IF NOT EXISTS products (
    guid UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Unique public identifier (UUID v4)

    name VARCHAR(255) NOT NULL,
    -- Human-readable product name (displayed to users)

    slug VARCHAR(255) NOT NULL UNIQUE,
    -- URL-friendly unique identifier (e.g. "iphone-16-pro")

    price_cents BIGINT NOT NULL CHECK (price_cents >= 0),
    -- Price in the smallest currency unit (cents/kopecks)
    -- Example: 149990 = 1499.90 ₽ / $14.99
    -- Stored as integer to avoid floating-point rounding issues

    description TEXT,

    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_products_slug 
    ON products(slug);
COMMENT ON INDEX idx_products_slug IS 'Ensures slug uniqueness and enables O(log n) lookup by slug';

CREATE INDEX IF NOT EXISTS idx_products_created_at_desc 
    ON products(created_at DESC);
COMMENT ON INDEX idx_products_created_at_desc IS 'Used for efficient "newer than" pagination (keyset/seek method)';


COMMENT ON COLUMN products.guid IS 
    'Primary key. Universally unique identifier (UUID v4). Generated automatically.';

COMMENT ON COLUMN products.name IS 
    'Product display name. Required. Shown to users in UI.';

COMMENT ON COLUMN products.slug IS 
    'Unique, URL-safe identifier. Used in permalinks (e.g. /products/iphone-16). Must be unique.';

COMMENT ON COLUMN products.price_cents IS 
    'Price in the smallest currency unit (e.g. RUB kopecks, USD cents). '
    'Use integer to avoid floating-point precision issues. '
    'Example: 99990 = 999.90 ₽';

COMMENT ON COLUMN products.description IS 
    'Optional rich text description. Can contain Markdown or HTML if needed.';

COMMENT ON COLUMN products.created_at IS 
    'UTC timestamp of record creation. '
    'Stored as TIMESTAMP (without time zone). Always represents UTC. '
    'Default: current time in UTC.';

COMMENT ON COLUMN products.updated_at IS 
    'UTC timestamp of the last modification. '
    'NULL if the record has never been updated.';
