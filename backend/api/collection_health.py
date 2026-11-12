"""Read-only collector status. Development opt-in for authenticated non-staff users."""
from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.http import require_GET
from . import access
from .auth import can_manage_collectors, require_auth
from .collection_alerts import current_alerts
from .collection_health_core import build_health, public_freshness
from .collection_freshness import annotate_stored_data_age, source_capture_times
from django.utils import timezone


@require_GET
@require_auth
def collection_health(request):
    # Operational status is not customer-report data. Restrict to staff until
    # application-specific RBAC is implemented.
    if not can_manage_collectors(request.user):
        return JsonResponse({'error': 'Forbidden'}, status=403)
    result = build_health(settings.BASE_DIR)
    annotate_stored_data_age(result["jobs"], source_capture_times(), timezone.now())
    result['alerts'] = current_alerts(settings.BASE_DIR, result['jobs'])
    return JsonResponse(result, status=200 if result['configured'] else 503)


@require_GET
@require_auth
def data_freshness(request):
    """When each page's data was last collected, for everyone signed in, so no page presents old figures as current. Only a time, a state
    and a count of failed runs: nothing from the logs (the collector status page and the raw logs stay administrators' only)."""
    result = build_health(settings.BASE_DIR)
    annotate_stored_data_age(result['jobs'], source_capture_times(), timezone.now())
    return JsonResponse({'checked_at': result['checked_at'], 'sources': public_freshness(result['jobs'])})


@require_GET
@require_auth
def collection_latest_log(request, job):
    """Only allow known job names, never accept a file path from the client."""
    from .collection_log_core import latest_run_log
    from .collection_health_core import JOB_NAMES
    if not can_manage_collectors(request.user):
        return JsonResponse({'error': 'Forbidden'}, status=403)
    if access.enforced() and not access.is_owner(request.user):   # the raw log can name customers of every company
        return JsonResponse({'error': 'The raw log is for the platform owner'}, status=403)
    if job not in JOB_NAMES:
        return JsonResponse({'error': 'Unknown collector'}, status=404)
    try:
        result = latest_run_log(settings.BASE_DIR, job)
    except OSError:
        return JsonResponse({'error': 'Unable to read collector log'}, status=503)
    return JsonResponse(result)
