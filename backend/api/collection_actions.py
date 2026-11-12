"""Staff-protected read/write control plane for the allowlisted collector queue."""
import json
from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST
from .auth import can_manage_collectors, require_auth
from .collection_health_core import JOB_NAMES
from .collector_queue import batch_status, enqueue, heartbeat_status, schedule_status


def authorized(request):
    return can_manage_collectors(request.user)


@require_GET
@require_auth
def collection_execution(request):
    if not authorized(request):
        return JsonResponse({'error': 'Forbidden'}, status=403)
    state = batch_status(settings.BASE_DIR)
    state['schedules'] = schedule_status(settings.BASE_DIR)
    state['heartbeat'] = heartbeat_status(settings.BASE_DIR)
    return JsonResponse(state)


@require_POST
@require_auth
def request_collection(request):
    if not authorized(request):
        return JsonResponse({'error': 'Forbidden'}, status=403)
    # An authenticated bearer token is required by require_auth. Never accept
    # arbitrary commands or paths from the client.
    try:
        data = json.loads(request.body or '{}')
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    if not isinstance(data, dict):
        return JsonResponse({'error': 'Expected a JSON object'}, status=400)
    name = data.get('job')
    if name == 'all':
        names = list(JOB_NAMES)
        # 'Update all' includes only enabled jobs, in a fixed source-safe order.
        from .collector_queue import configuration
        try:
            cfg = configuration(settings.BASE_DIR)
        except (OSError, ValueError):
            return JsonResponse({'error': 'Collector configuration unavailable'}, status=503)
        names = [n for n in names if isinstance(cfg.get(n), dict) and cfg[n].get('enabled')]
    elif name in JOB_NAMES:
        names = [name]
    else:
        return JsonResponse({'error': 'Unknown collector'}, status=400)
    try:
        batch_id = enqueue(settings.BASE_DIR, names, requested_by=f'user:{request.user.pk}')
    except RuntimeError as exc:
        return JsonResponse({'error': str(exc)}, status=409)
    except ValueError as exc:
        return JsonResponse({'error': str(exc)}, status=400)
    except OSError:
        return JsonResponse({'error': 'Collector queue unavailable'}, status=503)
    return JsonResponse({'queued': True, 'batch_id': batch_id,
                         'message': 'Queued. Collection worker will process this request on its next tick.'}, status=202)
