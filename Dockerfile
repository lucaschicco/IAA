FROM python:3.12-slim

# Tesseract con español, poppler (pdftotext/pdftoppm) y libs de imagen
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr tesseract-ocr-spa \
        poppler-utils \
        libgl1 libglib2.0-0 \
        make git \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src

WORKDIR /app

COPY pyproject.toml ./
# Instala dependencias antes de copiar el código para aprovechar la cache de capas
# (el paquete vacío alcanza para que pip resuelva las dependencias en modo editable)
RUN mkdir -p src/consorcio && touch src/consorcio/__init__.py     && pip install --upgrade pip && pip install -e ".[dev]"
COPY . .

CMD ["sleep", "infinity"]
