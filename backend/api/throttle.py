"""Failed-attempt throttling for sign-in and password-reset requests, kept in Django's cache.

The default cache is per process, which is correct for a single server process. With several workers, point
CACHES at a shared cache (for example Redis or the database cache) so the counts are shared."""
import hashlib

from django.conf import settings
from django.core.cache import cache


def _key(scope, request, name):
    who = hashlib.sha256(str(name or '').strip().casefold().encode()).hexdigest()[:24]
    return f"throttle:{scope}:{request.META.get('REMOTE_ADDR', '?')}:{who}"


def blocked(scope, request, name):
    return cache.get(_key(scope, request, name), 0) >= settings.THROTTLE_MAX_ATTEMPTS


def record(scope, request, name):
    key = _key(scope, request, name)
    cache.add(key, 0, settings.THROTTLE_WINDOW)  # starts the window on the first attempt only
    try:
        cache.incr(key)
    except ValueError:  # the entry expired between the two calls
        cache.set(key, 1, settings.THROTTLE_WINDOW)


def clear(scope, request, name):
    cache.delete(_key(scope, request, name))
