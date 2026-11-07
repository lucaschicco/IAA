-- =============================================================
-- Tablas de usuarios, roles y consorcios
-- Se corre una vez sobre la base Postgres del sistema.
-- =============================================================

-- Los cuatro roles posibles de una persona dentro de un consorcio.
CREATE TYPE rol AS ENUM ('administracion', 'consejo', 'propietario', 'inquilino');

-- Cada edificio.
CREATE TABLE consorcios (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    nombre      TEXT NOT NULL,
    direccion   TEXT NOT NULL,
    creado_en   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Cada departamento o cochera de un edificio.
CREATE TABLE unidades (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    consorcio_id BIGINT NOT NULL REFERENCES consorcios(id),
    piso         TEXT NOT NULL,
    depto        TEXT NOT NULL,
    porcentual   NUMERIC(6,3),               -- % de expensas que paga
    UNIQUE (consorcio_id, piso, depto)
);

-- Cada persona que puede entrar al sistema.
-- La contraseña nunca se guarda: solo su hash.
CREATE TABLE usuarios (
    id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    email            TEXT NOT NULL,
    nombre           TEXT NOT NULL,
    hash_contrasena  TEXT NOT NULL,
    activo           BOOLEAN NOT NULL DEFAULT true,
    creado_en        TIMESTAMPTZ NOT NULL DEFAULT now(),
    ultimo_login     TIMESTAMPTZ
);
-- El mail es único sin importar mayúsculas.
CREATE UNIQUE INDEX usuarios_email_unico ON usuarios (lower(email));

-- Qué es cada persona en cada consorcio.
-- Una persona puede tener varias filas: por ejemplo, propietaria
-- de una unidad y además miembro del consejo.
CREATE TABLE membresias (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    usuario_id   BIGINT NOT NULL REFERENCES usuarios(id),
    consorcio_id BIGINT NOT NULL REFERENCES consorcios(id),
    unidad_id    BIGINT REFERENCES unidades(id),
    rol          rol NOT NULL,
    activo       BOOLEAN NOT NULL DEFAULT true,
    creado_en    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE NULLS NOT DISTINCT (usuario_id, consorcio_id, unidad_id, rol),
    -- Propietarios e inquilinos siempre tienen unidad; la administración nunca.
    CHECK (
        (rol IN ('propietario', 'inquilino') AND unidad_id IS NOT NULL)
        OR (rol = 'administracion' AND unidad_id IS NULL)
        OR (rol = 'consejo')
    )
);

-- Invitaciones que manda la administración por mail.
-- Se guarda el hash del token, no el token: si alguien lee la base,
-- no puede usar los links.
CREATE TABLE invitaciones (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    consorcio_id  BIGINT NOT NULL REFERENCES consorcios(id),
    unidad_id     BIGINT REFERENCES unidades(id),
    email         TEXT NOT NULL,
    rol           rol NOT NULL,
    token_hash    TEXT NOT NULL UNIQUE,
    creada_por    BIGINT NOT NULL REFERENCES usuarios(id),
    creada_en     TIMESTAMPTZ NOT NULL DEFAULT now(),
    vence_en      TIMESTAMPTZ NOT NULL,
    usada_en      TIMESTAMPTZ                -- NULL mientras no se use
);

-- Registro de cada intento de login, exitoso o no.
-- Sirve para bloquear intentos repetidos y para las métricas de uso.
CREATE TABLE accesos (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    usuario_id  BIGINT REFERENCES usuarios(id),  -- NULL si el mail no existe
    email       TEXT NOT NULL,
    exitoso     BOOLEAN NOT NULL,
    ip          TEXT,
    momento     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX accesos_por_email ON accesos (lower(email), momento);
