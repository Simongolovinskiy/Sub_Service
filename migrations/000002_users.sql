-- depends: 000001_products
CREATE TABLE users (
    guid UUID PRIMARY KEY,
    username TEXT NOT NULL UNIQUE CHECK (btrim(username) <> ''),
    password TEXT NOT NULL,
    email TEXT CHECK (email IS NULL OR btrim(email) <> ''),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    is_superadmin BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL
);
CREATE UNIQUE INDEX idx_users_email ON users (lower(email)) WHERE email IS NOT NULL;
