# Autenticación y roles

Login, invitaciones y permisos. Lo armó Lucas; desde el 09/11 vive dentro del paquete `consorcio` y usa la
misma base que el resto del sistema.

## Qué hay

| Archivo | Qué hace |
|---|---|
| `cuentas.py` | La lógica, sin HTTP: `autenticar` (bloqueo tras 5 intentos fallidos en 15 minutos, registro en `accesos`), `crear_invitacion`, `aceptar_invitacion`. La usan la API y la web |
| `seguridad.py` | Hash de contraseñas (Argon2) y tokens de la API (JWT) |
| `permisos.py` | Quién es el usuario de la API y qué puede hacer |
| `rutas.py` | API JSON: invitar, registrarse, login, quién soy |
| `rutas_unidades.py` | Cargar y listar las unidades de un consorcio |
| `alta.py` | Comando de terminal para dar de alta un consorcio y cargar unidades desde un CSV |
| `app.py` | La API JSON aparte (`make api`, http://127.0.0.1:8001/docs) |

Las tablas (`usuarios`, `unidades_funcionales`, `membresias`, `invitaciones`, `accesos`) están en
`almacen/postgres.py` y se crean con Alembic (`migraciones/versions/0008_login.py`). Ya no hay SQL suelto.

## Cómo se conecta con el resto

El resto del sistema no sabe de contraseñas: solo usa `usuarios.sujeto_externo`. Al registrarse, una cuenta
nueva recibe `login:<uuid>`. La web (`web/autenticacion.py`, `ProveedorLogin`) llama a `autenticar` y guarda ese
`sujeto_externo` en su cookie; `identidad.resolver_identidad` junta las membresías de esa persona en el consorcio
elegido en una sola `Identidad` (rol principal y números de UF).

- Roles: `administracion`, `consejo`, `propietario`, `inquilino`. Una fila de `membresias` por usuario,
  consorcio, unidad y rol. Si una persona tiene varios, manda el de más arriba en esa lista.
- La bandeja (aprobar reclamos, resolver derivaciones) y la carga de PDF son solo de `administracion`.
- El id del consorcio es el slug de texto (`mercedes-274`), el mismo que usan documentos y liquidaciones.
- `unidades_funcionales.uf` es el número de la unidad en la liquidación: con eso el agente busca las expensas
  propias. Una unidad sin UF cargada no le da expensas propias a nadie.

## Cómo se da de alta un consorcio

1. Se crea el consorcio (o se completa uno que ya existe, como `mercedes-274`). El comando imprime una
   invitación para su administrador:
   ```bash
   make alta-consorcio ID=mercedes-274 NOMBRE="Mercedes 274" DIRECCION="Mercedes 274, CABA" ADMIN=admin@estudio.com
   ```
2. El administrador abre `http://127.0.0.1:8000/registro?token=<invitación>` y elige su contraseña.
3. Se cargan las unidades desde un CSV con columnas `piso,depto,uf,porcentual` (`uf` y `porcentual` son
   opcionales), o con `POST /auth/consorcios/{id}/unidades`:
   ```bash
   make alta-unidades CONSORCIO=mercedes-274 CSV=datos/unidades/unidades.csv
   ```
   Volver a cargar el mismo archivo no duplica: actualiza la UF y el porcentual.
4. Se invita a cada vecino a su unidad (`POST /auth/invitaciones`, desde `/docs` de la API).

## Usuarios demo

`make sembrar-demo` les pone mail `<sujeto>@demo.test` y la contraseña `demo-consorcio-2026` a los usuarios
demo, salvo al de plataforma, que solo entra con el selector de desarrollo.

## Pendiente

- Mandar el link de invitación por mail (hoy la función lo devuelve).
- Pantalla web para invitar y cargar unidades (hoy, la API o la terminal).
- Recuperar contraseña.
