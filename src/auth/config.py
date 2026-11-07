"""Configuración del módulo de autenticación. Todo sale de variables de entorno."""
import os

# Conexión a la base Postgres del sistema.
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://postgres@localhost/consorcio")

# Clave para firmar los tokens de sesión. Tiene que ser larga y secreta;
# va en el .env, nunca en el repo.
JWT_SECRETO = os.environ.get("JWT_SECRETO", "")

HORAS_TOKEN_SESION = 8          # cuánto dura un login
DIAS_INVITACION = 7             # cuánto dura un link de invitación
MAX_INTENTOS_FALLIDOS = 5       # intentos de login fallidos permitidos...
MINUTOS_BLOQUEO = 15            # ...dentro de esta ventana
LARGO_MINIMO_CONTRASENA = 10
