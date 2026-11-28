# Código

Paquete `consorcio/` (se instala con `pip install -e .`; dentro de Docker ya está instalado).

| Módulo | Qué hace |
|---|---|
| `config.py` | Configuración desde `.env` (proveedor de modelo, umbrales de OCR, conexiones, precios) |
| `llm.py` | Acceso al modelo independiente del proveedor (`google_genai`, `anthropic`, `openai`, `fake`) + contador de llamadas, tokens y costo |
| `observabilidad.py` | Trazas en MLflow (opcional) y logging |
| `datos_personales.py` | `tapar`: reemplaza mails, CUIT, teléfonos, DNI, direcciones y nombres antes de mandar un texto a MLflow (spec en `docs/2026-11-27-spec-privacidad-trazas.md`) |
| `cli.py` | Línea de comandos `consorcio ...`; junto con la evaluación, el único lugar que resuelve `CONSORCIO_POR_DEFECTO` |
| `identidad.py` | `Identidad` inmutable y `resolver_identidad(sujeto_externo, consorcio_id)`, que falla sin membresía activa y junta los roles y unidades de esa persona en el consorcio (rol principal y UF); equipo de plataforma y última actividad. Agnóstico al login |
| `auth/` | Login de Lucas: `cuentas.py` (autenticar con bloqueo, invitaciones), `seguridad.py` (Argon2, JWT), la API JSON (`app.py`, `make api`) y `alta.py` (alta de consorcios y unidades por terminal). Ver `auth/LEEME.md` |
| `tope.py` | Tope de gasto mensual sobre el libro `gastos_modelo`: lo consultan `atender` e `ingestar_pdf` |
| `crm.py` | Métricas agregadas del CRM, solo SQL: la única lectura que cruza consorcios. No importa `Mensaje` |
| `texto.py` | Normalización de texto compartida (búsqueda, verificación de citas, evaluación) e ids: `id_documento(consorcio, hash)`, `consorcio_de_chat_id` |
| `ingesta/` | **Tool 1**: PDF → hash y control de duplicados → clasificar calidad → extraer texto → estructurar → guardar en disco → preparar guardado → persistir (grafo de LangGraph en `grafo.py`). `liquidacion.py`: liquidaciones de expensas (modelo para rubros y gastos, regex para el prorrateo por unidad) |
| `consulta/` | **Tool 2**: pregunta → `buscador.py` (tsvector o palabras clave en Postgres) → responder con cita → verificar la cita en el texto original o derivar (grafo en `grafo.py`); `evaluar.py` mide recall@k y calidad de respuestas contra `datos/evaluacion/preguntas.csv` |
| `agente/` | Router y flujos (grafo en `grafo.py`, `atender` es la entrada): `nodos.py` (funcionalidades), `registro.py` (qué funcionalidades hay), `esquemas.py` (`SalidaRouter`), `memoria.py` (conversaciones), `bandeja.py` (solicitudes y aprobación), `semilla.py` (`sembrar-demo`), `guion_fake.py` (respuestas del fake con `FAKE_GUIONADO`), `evaluar.py` (`evaluar-agente`: el set de `datos/evaluacion/mensajes_agente.csv` con el router del modelo y uno por palabras clave) |
| `almacen/` | Persistencia. `postgres.py`: consorcios y chats, documentos con `consorcio_id`, datos estructurados y sensibles, liquidaciones, consultas, usuarios y membresías; `crear_tablas` controla la revisión de Alembic; `migrar_consorcios`. `crudos.py`: PDF originales en un bucket S3 (`<consorcio>/<sha256>.pdf`) |
| `canales/` | Cómo le hablan al agente: `telegram.py` (bot por long polling: PDF → ingesta, texto → consulta, `/expensas` → SQL; lista blanca de chats). Solo formatea; no tiene lógica propia. `formato.py`: textos compartidos con la web |
| `web/` | Web de pruebas con FastAPI: `app.py` (aplicación y cabeceras), `rutas.py` (chat, documentos, solicitudes, bandeja), `crm_rutas.py` (`/crm`), `sesion.py` (cookie firmada, CSRF, control de acceso), `autenticacion.py` (`ProveedorLogin` con mail y contraseña, `SelectorDesarrollo` y el registro por invitación: lo único que sabe del login), `plantillas/`, `estaticos/` |

El esquema de Postgres se versiona con Alembic en `migraciones/` (fuera del paquete; configuración en `alembic.ini`).

Las instrucciones que recibe el modelo viven en `skills/ingesta/SKILL.md` (transcribir, clasificar, estructurar, liquidación), `skills/consulta/SKILL.md` (responder con cita) y `skills/agente/SKILL.md` (router), no en el código.
