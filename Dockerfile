# API Dockerfile
FROM python:3.11-slim

WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY src/ ./src/
COPY handlers/ ./handlers/
COPY local_server.py .
COPY releaseversion.txt .
COPY wsgi.py .
COPY buildinfo.json .

# Scenarios are mounted via docker-compose volume

EXPOSE 8000

CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "1", "wsgi:app"]
