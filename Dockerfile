FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1     PYTHONUNBUFFERED=1

WORKDIR /app

COPY services/api/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY services/api /app/services/api

EXPOSE 10000

CMD ["uvicorn", "services.api.main:app", "--host", "0.0.0.0", "--port", "10000"]
