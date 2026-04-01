FROM python:3.11-slim

# Install Tesseract OCR + system deps
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    tesseract-ocr-eng \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create dirs — /data will be overridden by Fly.io persistent volume at runtime
RUN mkdir -p staticfiles static data

# Collect static files at build time (no DB needed)
RUN SECRET_KEY=build-time-only-key python manage.py collectstatic --noinput --clear

EXPOSE 8080

# At container startup: run migrations then start gunicorn
# Fly.io sets $PORT automatically (default 8080)
CMD ["sh", "-c", "python manage.py migrate --noinput && gunicorn --bind 0.0.0.0:${PORT:-8080} --workers 2 --threads 4 --timeout 120 labai.wsgi:application"]