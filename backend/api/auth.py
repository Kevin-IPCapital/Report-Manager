import hashlib
import hmac
import secrets
import uuid
from datetime import timedelta
from functools import wraps

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import signing
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone

from .models import RefreshToken

COOKIE = 'refresh_token'
COOKIE_PATH = '/api/auth'  # the browser only sends the refresh cookie to the auth endpoints


def _hash(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


# ---------------- access tokens: signed, expiring, stateless ----------------
def make_access(user):
    return signing.dumps({'uid': user.pk}, salt='access')


def verify_access(token):
    try:
        uid = signing.loads(token, salt='access', max_age=settings.ACCESS_TOKEN_TTL)['uid']
    except signing.BadSignature:  # includes SignatureExpired
        return None
    return get_user_model().objects.filter(pk=uid, is_active=True).first()


def require_auth(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        header = request.headers.get('Authorization', '')
        user = verify_access(header[7:]) if header.startswith('Bearer ') else None
        if not user:
            return JsonResponse({'error': 'Authentication required'}, status=401)
        request.user = user
        return view(request, *args, **kwargs)
    return wrapped


# ---------------- refresh tokens: opaque, rotated, reuse-detected ----------------
def _successor_raw(old_hash):
    # Deterministic (keyed by SECRET_KEY): a retry of the same old token inside the grace
    # window yields the SAME new token instead of forking a second one, and no raw token is stored.
    return hmac.new(settings.SECRET_KEY.encode(), f'successor:{old_hash}'.encode(), 'sha256').hexdigest()


def _store(user, raw, family, family_expires_at):
    expires = min(timezone.now() + timedelta(seconds=settings.REFRESH_TOKEN_TTL), family_expires_at)
    RefreshToken.objects.get_or_create(
        token_hash=_hash(raw),
        defaults={'user': user, 'family': family, 'expires_at': expires, 'family_expires_at': family_expires_at},
    )


def start_session(user):
    raw = secrets.token_urlsafe(32)
    _store(user, raw, uuid.uuid4(), timezone.now() + timedelta(seconds=settings.SESSION_MAX_AGE))
    return raw


def rotate(raw):
    """Exchange a refresh token for its successor. Returns (user, new_raw) or None."""
    now = timezone.now()
    with transaction.atomic():
        rt = RefreshToken.objects.select_related('user').filter(token_hash=_hash(raw)).first()
        if not rt or rt.revoked or rt.expires_at <= now or not rt.user.is_active:
            return None
        new_raw = _successor_raw(rt.token_hash)
        if rt.rotated_at is None:
            # Conditional update: if two requests race, one wins and the other falls through harmlessly.
            RefreshToken.objects.filter(pk=rt.pk, rotated_at__isnull=True).update(rotated_at=now)
            _store(rt.user, new_raw, rt.family, rt.family_expires_at)
        elif (now - rt.rotated_at).total_seconds() > settings.REFRESH_GRACE:
            # An already-rotated token used after the leeway window: treat as theft, kill the session.
            RefreshToken.objects.filter(family=rt.family).update(revoked=True)
            return None
        # else: replay inside the leeway window (lost response / parallel request) -> same successor.
        return rt.user, new_raw


def revoke(raw):
    rt = RefreshToken.objects.filter(token_hash=_hash(raw)).first()
    if rt:
        RefreshToken.objects.filter(family=rt.family).update(revoked=True)


def set_cookie(response, raw):
    response.set_cookie(COOKIE, raw, max_age=settings.REFRESH_TOKEN_TTL, httponly=True,
                        samesite='Strict', secure=not settings.DEBUG, path=COOKIE_PATH)


def clear_cookie(response):
    response.delete_cookie(COOKIE, path=COOKIE_PATH, samesite='Strict')


def staff_required(view):
    """Use under @require_auth: only staff may call the view."""
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_staff:
            return JsonResponse({'error': 'Forbidden'}, status=403)
        return view(request, *args, **kwargs)
    return wrapped


def can_manage_collectors(user):
    """Staff only. In DEBUG, DJANGO_DEV_COLLECTOR_HEALTH_FOR_ALL=1 additionally admits any signed-in user."""
    return bool(user.is_staff or (settings.DEBUG and getattr(settings, 'DEV_COLLECTOR_HEALTH_FOR_ALL', False)))


def revoke_all(user):
    """Sign the user out everywhere (used after a password reset)."""
    RefreshToken.objects.filter(user=user).update(revoked=True)
