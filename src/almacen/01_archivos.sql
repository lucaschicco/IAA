-- Registro de los PDF subidos. El archivo en sí vive en el bucket;
-- acá queda de quién es, quién lo subió y su huella (sha256).
CREATE TABLE archivos (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    consorcio_id  BIGINT NOT NULL REFERENCES consorcios(id),
    nombre        TEXT NOT NULL,              -- nombre original del archivo
    sha256        TEXT NOT NULL,              -- huella del contenido
    tamano_bytes  BIGINT NOT NULL,
    clave_bucket  TEXT NOT NULL,              -- dónde está en el bucket
    subido_por    BIGINT REFERENCES usuarios(id),
    subido_en     TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- El mismo contenido no se guarda dos veces en un consorcio,
    -- aunque cambie el nombre del archivo.
    UNIQUE (consorcio_id, sha256)
);
