# syntax=docker/dockerfile:1

FROM python:3.12-slim

# Logs sollen sofort im Terminal landen statt im Puffer zu hängen.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Abhängigkeiten zuerst: solange sich requirements.txt nicht ändert,
# kommt dieser Layer aus dem Cache.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY easy_server_cloner/ ./easy_server_cloner/
COPY main.py ./

# Backups und Zwischenstände sollen einen Neustart des Containers überleben —
# in docker-compose.yml sind beide als Volume eingehängt.
RUN mkdir -p /app/backups /app/state

# Nicht als root laufen.
RUN useradd --create-home --uid 1000 cloner \
    && chown -R cloner:cloner /app
USER cloner

CMD ["python", "main.py"]
