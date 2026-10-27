# Atajos para trabajar con Docker. Requiere .env (copiar de .env.example).
COMPOSE = docker compose
RUN = $(COMPOSE) exec app

.PHONY: build up down logs shell ingestar preguntar expensas evaluar-consultas test lint evaluar-ocr psql cypher bot bot-logs bot-down respaldar restaurar consorcios asignar migrar consultas

build:        ## Construye la imagen de la app
	$(COMPOSE) build

up:           ## Levanta app, postgres, neo4j y mlflow
	$(COMPOSE) up -d

down:         ## Baja todo (los datos quedan en los volúmenes)
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f --tail=100

shell:        ## Bash dentro del contenedor de la app
	$(RUN) bash

ingestar:     ## make ingestar PDF="datos/documentos/archivo.pdf" [METODO=auto|texto|tesseract|vision]
	$(RUN) consorcio ingestar "$(PDF)" --metodo $(or $(METODO),auto)

evaluar-ocr:  ## Compara la transcripción de un PDF contra un .md de referencia
	$(RUN) consorcio evaluar-ocr "$(PDF)" "$(REF)" --metodo $(or $(METODO),auto)

preguntar:    ## make preguntar Q="¿Puedo tener dos autos en mi cochera?"
	$(RUN) consorcio preguntar "$(Q)"

expensas:     ## make expensas QUE=rubros PERIODO=2026-07  (QUE=resumen|rubros|gastos|unidades)
	$(RUN) consorcio expensas $(or $(QUE),resumen) $(if $(PERIODO),--periodo $(PERIODO)) $(if $(UNIDAD),--unidad "$(UNIDAD)") $(if $(TEXTO),--texto "$(TEXTO)")

evaluar-consultas:  ## make evaluar-consultas [PARTICION=dev|test|todas] [CON_MODELO=1]
	$(RUN) consorcio evaluar-consultas datos/evaluacion/preguntas.csv --particion $(or $(PARTICION),dev) $(if $(CON_MODELO),--con-modelo) --sin-detalle

test:
	$(RUN) pytest -q

lint:
	$(RUN) ruff check src tests

bot:          ## Levanta el bot de Telegram (necesita TELEGRAM_BOT_TOKEN en .env)
	$(COMPOSE) --profile bot up -d bot

bot-logs:
	$(COMPOSE) logs -f --tail=100 bot

bot-down:
	$(COMPOSE) --profile bot stop bot

respaldar:    ## Vuelca Postgres a datos/respaldo/postgres-<fecha>.sql (para pasar la base ya cargada)
	$(COMPOSE) exec -T postgres pg_dump -U consorcio -d consorcio --clean --if-exists --no-owner > datos/respaldo/postgres-$$(date +%Y-%m-%d).sql

restaurar:    ## make restaurar [DUMP=datos/respaldo/postgres-2026-10-26.sql]: carga el volcado en el Postgres local
	$(COMPOSE) exec -T postgres psql -U consorcio -d consorcio -q < $(or $(DUMP),$(lastword $(sort $(wildcard datos/respaldo/postgres-*.sql))))

consultas:    ## Últimas consultas de cualquier canal. make consultas ORIGEN=telegram
	$(RUN) consorcio consultas $(if $(ORIGEN),--origen $(ORIGEN)) $(if $(CONSORCIO),--consorcio $(CONSORCIO)) --ultimas $(or $(N),20)

consorcios:   ## Lista las bases (consorcios) y los chats asignados. make asignar CHAT=123 A=demo
	$(RUN) consorcio consorcios

asignar:      ## make asignar CHAT=<id de chat> A=<consorcio>: ese número/grupo pasa a usar esa base
	$(RUN) consorcio consorcios --asignar-chat $(CHAT) --a $(A)

migrar:       ## Migra una base anterior al 26/10/2026 al esquema con consorcios (idempotente)
	$(RUN) consorcio migrar

psql:         ## Consola de Postgres
	$(COMPOSE) exec postgres psql -U consorcio -d consorcio

cypher:       ## Consola de Neo4j
	$(COMPOSE) exec neo4j cypher-shell -u neo4j -p consorcio123
