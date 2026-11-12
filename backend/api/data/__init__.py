"""Dashboard response contracts. All report data comes from the original source schemas.

One module per source: tickets (Autotask), patches (N-central), servers (SentinelOne), backups (Cove). Add a source by adding a
module and one line to `sections`."""
from .. import access
from . import backups, patches, servers, tickets

_ASK = object()   # "work out the limit from the user"


def _trend(params, allowed=None):
    from .. import snapshots  # imported here: snapshots uses the ORM and this package must stay importable without it
    return snapshots.trend(params, allowed)


def _limit(name, payload, allowed):
    """Apply a person's client limit to a section's answer: rows of other clients, or of no client yet, are removed. The patch sections
    are limited earlier, in the queries themselves, so that nothing about other clients is read."""
    if payload is None:
        return None
    if name == 'tickets':
        payload['tickets']['rows'] = access.only_allowed(payload['tickets']['rows'], allowed)
    elif name == 'server-health':
        payload['servers'] = access.only_allowed(payload['servers'], allowed)
    elif name == 'backups':
        payload['rows'] = access.only_allowed(payload['rows'], allowed)
    elif name == 'backup-detail' and not access.row_allowed(payload['device'].get('client_id'), allowed):
        return None   # the same answer as for a device that does not exist
    return payload


def get_section(name, start=None, end=None, params=None, user=None, allowed=_ASK):
    """Return the payload for a section, or None if the name is unknown. `user` selects that user's score weights and, while client access
    is enforced, which clients they may see (`allowed` can be given instead: a set of client IDs, or None for no limit). Asking for a
    client or customer outside that raises access.AccessDenied."""
    params = params or {}
    if allowed is _ASK:
        allowed = access.allowed_client_ids(user)
    if allowed is not None:
        access.check_request(params, allowed)
    sections = {
        'tickets': lambda: tickets.tickets(start, end),
        'patches': lambda: patches.patches(params, user, allowed),
        'patch-entries': lambda: patches.patch_entries(params, allowed),
        'patch-overview': lambda: patches.overview(params, user, allowed),
        'patch-trend': lambda: _trend(params, allowed),
        'server-health': servers.server_health,
        'backups': backups.backups,
        'backup-detail': lambda: backups.backup_detail(params),
    }
    handler = sections.get(name)
    if not handler:
        return None
    payload = handler()
    return payload if allowed is None else _limit(name, payload, allowed)
