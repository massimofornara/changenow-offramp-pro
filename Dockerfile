# Dockerfile (root)
FROM python:3.11-slim

# Evita bytecode e buffering
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dipendenze
COPY services/api/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Codice
COPY services/api /app/services/api

# Render passa la porta via $PORT
ENV PYTHONPATH=/app
CMD ["bash", "-lc", "uvicorn services.api.main:app --host 0.0.0.0 --port ${PORT}"]
