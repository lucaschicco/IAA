-- Datos inventados para probar. No usar en producción.
INSERT INTO consorcios (nombre, direccion) VALUES ('Mercedes 274', 'Mercedes 274, CABA');

INSERT INTO unidades (consorcio_id, piso, depto, porcentual) VALUES
    (1, '5', 'D', 1.250),
    (1, '3', 'A', 1.100);

-- Los hashes son de mentira: los reales los genera el backend.
INSERT INTO usuarios (email, nombre, hash_contrasena) VALUES
    ('admin@administracion.test', 'Administración', 'hash-de-prueba'),
    ('dueno5d@test.test',         'Propietario 5D', 'hash-de-prueba'),
    ('inquilino3a@test.test',     'Inquilino 3A',   'hash-de-prueba');

INSERT INTO membresias (usuario_id, consorcio_id, unidad_id, rol) VALUES
    (1, 1, NULL, 'administracion'),
    (2, 1, 1,    'propietario'),
    (2, 1, NULL, 'consejo'),        -- el dueño del 5D también es del consejo
    (3, 1, 2,    'inquilino');
