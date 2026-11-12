import os
import sys
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

from .env import database_from_url, load_env, without_search_path

BASE_DIR = Path(__file__).resolve().parent.parent
load_env(BASE_DIR / '.env')  # real environment variables take precedence over the file

DEBUG = os.environ.get('DJANGO_DEBUG', '0') == '1'  # fails closed: unset means production, which then demands a real secret key
SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', 'dev-only-insecure-key-change-me')
if not DEBUG and SECRET_KEY.startswith('dev-only'):
    raise ImproperlyConfigured('Set DJANGO_SECRET_KEY when DJANGO_DEBUG=0')
ALLOWED_HOSTS = os.environ.get('DJANGO_ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',')

# DEV ONLY: while on, login accepts ANY username/password (the user is created on first use). Off by default; it is
# only honoured while DJANGO_DEBUG=1, so it cannot be switched on in production. Real accounts: register, or createsuperuser.
DEV_LOGIN_BYPASS = DEBUG and os.environ.get('DJANGO_DEV_LOGIN_BYPASS', '0') == '1'

# ---- Production hardening (applies when DJANGO_DEBUG=0; run `manage.py check --deploy` to review) ----
if not DEBUG:
    SECURE_SSL_REDIRECT = os.environ.get('DJANGO_SSL_REDIRECT', '1') == '1'
    SECURE_HSTS_SECONDS = int(os.environ.get('DJANGO_HSTS_SECONDS', str(365 * 24 * 3600)))
    SECURE_CONTENT_TYPE_NOSNIFF = True
if os.environ.get('DJANGO_BEHIND_PROXY', '0') == '1':  # a reverse proxy terminates HTTPS and sets X-Forwarded-Proto
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# ---- Passwords and sign-in throttling ----
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 8}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]
THROTTLE_MAX_ATTEMPTS = int(os.environ.get('THROTTLE_MAX_ATTEMPTS', '5'))   # failed sign-ins (or reset requests) ...
THROTTLE_WINDOW = int(os.environ.get('THROTTLE_WINDOW_SECONDS', '900'))     # ... allowed per address and username in this time
CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}

INSTALLED_APPS = ['django.contrib.auth', 'django.contrib.contenttypes', 'api']
MIDDLEWARE = ['django.middleware.security.SecurityMiddleware', 'django.middleware.common.CommonMiddleware']
ROOT_URLCONF = 'config.urls'

# DATABASE_URL: the app database (Django users + refresh tokens). REPORTS_DATABASE_URL: the reporting
# database, queried from api/data.py via connections['reports'] (no Django tables are created there).
DATABASES = {'default': database_from_url(os.environ.get('DATABASE_URL') or 'sqlite:///db.sqlite3', BASE_DIR)}
if os.environ.get('REPORTS_DATABASE_URL'):
    DATABASES['reports'] = database_from_url(os.environ['REPORTS_DATABASE_URL'], BASE_DIR)

# `manage.py test` makes a new empty database, so the schema in DATABASE_URL's search_path does not exist there. Either point
# TEST_DATABASE_URL at somewhere else (for example sqlite:///test.sqlite3: tests then run in memory and need no PostgreSQL), or the
# search_path option is dropped for the test run and the test tables go in `public` of the temporary database.
if len(sys.argv) > 1 and sys.argv[1] == 'test':
    if os.environ.get('TEST_DATABASE_URL'):
        DATABASES['default'] = database_from_url(os.environ['TEST_DATABASE_URL'], BASE_DIR)
    else:
        DATABASES['default'] = without_search_path(DATABASES['default'])
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
USE_TZ = True
TIME_ZONE = 'UTC'

# ---- Accounts and email ----
# Open registration is on by default in debug and off otherwise; set DJANGO_REGISTRATION_OPEN=1 to allow it.
REGISTRATION_OPEN = os.environ.get('DJANGO_REGISTRATION_OPEN', '1' if DEBUG else '0') == '1'
FRONTEND_URL = os.environ.get('FRONTEND_URL', 'http://localhost:5173')  # used in password-reset links
PASSWORD_RESET_TIMEOUT = 3600
# Emails print to the backend console until you point EMAIL_BACKEND at SMTP (see .env).
EMAIL_BACKEND = os.environ.get('EMAIL_BACKEND', 'django.core.mail.backends.console.EmailBackend')
DEFAULT_FROM_EMAIL = os.environ.get('DEFAULT_FROM_EMAIL', 'Report Manager <no-reply@localhost>')
EMAIL_HOST = os.environ.get('EMAIL_HOST', 'localhost')
EMAIL_PORT = int(os.environ.get('EMAIL_PORT', '25'))
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD', '')
EMAIL_USE_TLS = os.environ.get('EMAIL_USE_TLS', '0') == '1'

# ---- Token policy (seconds) ----
ACCESS_TOKEN_TTL = 10 * 60            # short-lived, held in browser memory only
REFRESH_TOKEN_TTL = 7 * 24 * 3600     # sliding: each rotation extends it...
SESSION_MAX_AGE = 30 * 24 * 3600      # ...but never past this absolute cap from login
REFRESH_GRACE = 10                    # leeway window: a just-rotated token is still accepted this long
# DEV ONLY: lets any signed-in user open the Data Collection page and queue collector runs. Off unless explicitly
# enabled, and ignored when DEBUG is off. Normally give a user staff access instead: `manage.py makestaff <username>`.
# ---- Collector alerts (see backend/deploy/README.md) ----
COLLECTOR_ALERT_EMAILS = [e.strip() for e in os.environ.get('COLLECTOR_ALERT_EMAILS', '').split(',') if e.strip()]
COLLECTOR_HEARTBEAT_MAX_MINUTES = int(os.environ.get('COLLECTOR_HEARTBEAT_MAX_MINUTES', '5'))   # worker silent this long = alert
COLLECTOR_ALERT_REPEAT_HOURS = int(os.environ.get('COLLECTOR_ALERT_REPEAT_HOURS', '24'))      # reminder interval while a problem lasts
COLLECTOR_ALERT_ON_PARTIAL = os.environ.get('COLLECTOR_ALERT_ON_PARTIAL', '0') == '1'         # off: N-central 404 policy is undecided
DEV_COLLECTOR_HEALTH_FOR_ALL = DEBUG and os.environ.get('DJANGO_DEV_COLLECTOR_HEALTH_FOR_ALL', '0') == '1'

# Off by default: everyone signed in sees every client, as before. Set DJANGO_CLIENT_ACCESS_ENFORCED=1 once people have been given their clients
# (Access page, or `manage.py client_access`): then staff still see everything and everyone else only the clients they were given.
CLIENT_ACCESS_ENFORCED = os.environ.get('DJANGO_CLIENT_ACCESS_ENFORCED', '0') == '1'

# The first company, made when the companies migration runs: every existing person and client is put in it, so nothing changes that day.
DEFAULT_COMPANY_NAME = os.environ.get('DJANGO_DEFAULT_COMPANY_NAME', 'IP Capital').strip() or 'IP Capital'
