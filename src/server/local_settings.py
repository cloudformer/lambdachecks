"""What lambdachecks changes in Healthchecks: its own settings hook (hc/settings.py imports hc/local_settings.py
last). scripts/build_server.py copies this file there; Healthchecks' code is not touched.

Everything else stays Healthchecks' (SECRET_KEY, SITE_ROOT, ... from the environment, see terraform/main.tf).
Nothing here needs AWS at import time: collectstatic and compress run with it at build time.
"""
import os

DEBUG = os.getenv("DEBUG", "False") == "True"                          # Healthchecks defaults to True
REGISTRATION_OPEN = os.getenv("REGISTRATION_OPEN", "False") == "True"  # Healthchecks defaults to True

# the database: a database in an existing s3rds deployment (github.com/cloudformer/s3rds), Django's SQLite
# backend over HTTPS. Healthchecks' own variable names; DB_HOST = the s3rds Function URL host.
DATABASES = {
    "default": {
        "ENGINE": "s3rds.sqlite",
        "NAME": os.getenv("DB_NAME", "healthchecks"),
        "HOST": os.getenv("DB_HOST", ""),
        "USER": os.getenv("DB_USER", ""),
        "PASSWORD": os.getenv("DB_PASSWORD", ""),
    }
}

# the Function URL is HTTPS only
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# Healthchecks' logging, plus errors to stderr (CloudWatch): with DEBUG off Django prints nothing otherwise
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "db": {"level": "DEBUG", "class": "hc.logs.Handler"},
        "console": {"level": "ERROR", "class": "logging.StreamHandler"},
    },
    "loggers": {
        "django.request": {"level": "ERROR", "handlers": ["db", "console"]},
        "hc": {"level": "DEBUG", "handlers": ["db", "console"]},
    },
}
