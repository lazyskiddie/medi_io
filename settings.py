"""
LabAI Django Settings
Database: Supabase (free hosted Postgres) via DATABASE_URL
"""
import os
import dj_database_url
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get(
    "SECRET_KEY",
    "django-insecure-labai-dev-key-change-in-production-please"
)

DEBUG = os.environ.get("DEBUG", "False") == "True"

ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "*").split(",")

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.staticfiles",
    "core",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "labai.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
            ],
        },
    },
]

WSGI_APPLICATION = "labai.wsgi.application"

# ── DATABASE — Supabase Postgres ──────────────────────────────────
# Set DATABASE_URL in your Render environment variables.
# Get it from: Supabase → Project → Settings → Database → URI
# Format: postgresql://postgres:[PASSWORD]@db.[PROJECT].supabase.co:5432/postgres
#
# For local development without Supabase, falls back to SQLite.

_DATABASE_URL = os.environ.get("DATABASE_URL", "")

if _DATABASE_URL:
    DATABASES = {
        "default": dj_database_url.config(
            default=_DATABASE_URL,
            conn_max_age=600,        # keep connections alive for 10 min
            conn_health_checks=True, # auto-reconnect dropped connections
            ssl_require=True,        # Supabase requires SSL
        )
    }
else:
    # Local fallback — SQLite for development without Supabase
    _DB_PATH = BASE_DIR / "data" / "labai.db"
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": str(_DB_PATH),
            "OPTIONS": {"timeout": 20},
        }
    }

# ── STATIC FILES ──────────────────────────────────────────────────
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

# ── APP SETTINGS ──────────────────────────────────────────────────
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin@labai2024")

# Max upload size: 200 MB for batch uploads
DATA_UPLOAD_MAX_MEMORY_SIZE = 200 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 200 * 1024 * 1024

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ── SECURITY (production) ─────────────────────────────────────────
if not DEBUG:
    SECURE_BROWSER_XSS_FILTER   = True
    X_FRAME_OPTIONS              = "DENY"
    SECURE_CONTENT_TYPE_NOSNIFF = True