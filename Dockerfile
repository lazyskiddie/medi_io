FROM python:3.11-slim

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
RUN mkdir -p staticfiles static data

# Collect static files — uses dummy SECRET_KEY, no DB needed here
RUN SECRET_KEY=build-time-only-key python manage.py collectstatic --noinput --clear

EXPOSE 8000

# migrate runs at startup against Supabase (DATABASE_URL env var must be set)
CMD ["sh", "-c", "python manage.py migrate --noinput && gunicorn --bind 0.0.0.0:${PORT:-8000} --workers 2 --threads 4 --timeout 120 labai.wsgi:application"]