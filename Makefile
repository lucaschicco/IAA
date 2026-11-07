# Atajos para correr el sistema. Escribí `make` para ver la lista.
# Las variables (DATABASE_URL, JWT_SECRETO) se leen del archivo .env.

-include .env
export

DATABASE_URL ?= postgresql://postgres:postgres@localhost:5432/consorcio
TEST_DATABASE_URL ?= postgresql://postgres:postgres@localhost:5432/auth_test
PYTHON ?= python3

.PHONY: ayuda instalar db db-apagar db-reiniciar auth test-auth

ayuda:
	@echo "make instalar      Instala las dependencias de Python"
	@echo "make db            Levanta Postgres en Docker (crea las tablas la primera vez)"
	@echo "make db-apagar     Apaga Postgres (los datos se conservan)"
	@echo "make db-reiniciar  Borra todos los datos y arranca de cero"
	@echo "make auth          Levanta el backend de login en http://localhost:8000/docs"
	@echo "make test-auth     Corre los tests de login y permisos"

instalar:
	$(PYTHON) -m pip install -r src/auth/requirements.txt

db:
	docker compose up -d --wait db

db-apagar:
	docker compose down

db-reiniciar:
	docker compose down -v
	docker compose up -d --wait db

auth:
	@test -n "$(JWT_SECRETO)" || (echo "Falta JWT_SECRETO en el .env" && exit 1)
	PYTHONPATH=src $(PYTHON) -m uvicorn auth.app:app --reload

test-auth:
	-docker compose exec -T db createdb -U postgres auth_test 2>/dev/null
	DATABASE_URL=$(TEST_DATABASE_URL) PYTHONPATH=src $(PYTHON) -m pytest tests/ -q
