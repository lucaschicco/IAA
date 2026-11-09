# Autenticación y roles

Login, invitaciones y permisos de la webapp.

## Qué hay

| Archivo | Qué hace |
|---|---|
| `01_tablas.sql` | Crea las tablas: usuarios, consorcios, unidades, membresías, invitaciones, accesos |
| `02_datos_prueba.sql` | Datos inventados para probar a mano |
| `seguridad.py` | Hash de contraseñas (Argon2) y tokens de sesión (JWT) |
| `permisos.py` | Quién es el usuario y qué puede hacer. Lo usan las demás partes del sistema |
| `rutas.py` | Las funciones HTTP: invitar, registrarse, login, quién soy |
| `rutas_unidades.py` | Cargar y listar las unidades de un consorcio |
| `alta.py` | Comando de terminal para dar de alta un consorcio y cargar unidades desde un CSV |
| `app.py` | Arranca el servidor |

## Cómo se da de alta un consorcio

1. Desde la terminal se crea el consorcio. El comando devuelve una invitación para su administrador:
   ```bash
   PYTHONPATH=src python -m auth.alta consorcio --nombre "Mercedes 274" \
       --direccion "Mercedes 274, CABA" --admin-email admin@estudio.com
   ```
2. El administrador usa esa invitación en `/auth/registro` y elige su contraseña.
3. Carga las unidades, desde la webapp (`POST /auth/consorcios/{id}/unidades`) o desde un CSV con columnas `piso,depto,porcentual`:
   ```bash
   PYTHONPATH=src python -m auth.alta unidades --consorcio-id 1 --csv datos/unidades/unidades.csv
   ```
   Volver a cargar el mismo archivo no duplica: actualiza el porcentual.
4. Invita a cada vecino a su unidad (`POST /auth/invitaciones`).

## Cómo se usa desde otras partes del sistema

```python
from auth.permisos import usuario_actual, exigir_rol, unidades_propias

@router.get("/expensas")
def expensas(consorcio_id: int, usuario=Depends(usuario_actual)):
    unidades = unidades_propias(usuario, consorcio_id)   # nunca la que escriba el vecino
    ...
```

## Cómo correrlo

```bash
pip install -r src/auth/requirements.txt
export DATABASE_URL=postgresql://postgres:postgres@localhost/consorcio
export JWT_SECRETO=<una clave larga y secreta, mínimo 32 caracteres>
psql "$DATABASE_URL" -f src/auth/01_tablas.sql
PYTHONPATH=src uvicorn auth.app:app --reload
```

Después abrir http://localhost:8000/docs para probar cada función desde el navegador.

## Tests

```bash
createdb auth_test
PYTHONPATH=src pytest tests/
```

## Pendiente

- Mandar el link de invitación por mail (hoy la función lo devuelve).
- Recuperar contraseña.
