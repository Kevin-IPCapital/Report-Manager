import json
import logging
from datetime import date

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import JsonResponse
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from . import access as access_lib, auth, clients as client_lib, data, gaps as gap_lib, groups as groups_lib, reports as report_lib, scoring, throttle
from .models import Client, ClientAccess, ClientAlias, ClientGroup, ClientSourceMap, Company, CompanyMember, Report, ScoreWeight

log = logging.getLogger(__name__)


def _error(message, status):
    return JsonResponse({'error': message}, status=status)


def _json_body(request):
    try:
        body = json.loads(request.body or b'{}')
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _csrf_ok(request):
    # Cross-site requests cannot set a custom header without a CORS preflight, and the
    # refresh cookie is SameSite=Strict as well. Belt and braces for the cookie-using endpoints.
    return request.headers.get('X-Requested-With') == 'fetch'


def _company_dict(company):
    return {'id': company.pk, 'name': company.name} if company else None


def _user_company(user):
    member = CompanyMember.objects.filter(user=user).select_related('company').first()
    return _company_dict(member.company) if member else None


def _session_response(user, refresh_raw):
    response = JsonResponse({
        'access': auth.make_access(user),
        'expires_in': settings.ACCESS_TOKEN_TTL,
        'user': {'username': user.get_username(), 'name': user.get_full_name() or user.get_username(),
                 'is_staff': user.is_staff, 'is_owner': access_lib.is_owner(user), 'company': _user_company(user),
                 'scoped': access_lib.is_scoped_member(user)},   # scoped: limited to the clients they were given
    })
    auth.set_cookie(response, refresh_raw)
    return response


def check_credentials(request, username, password):
    """The single credential check. Returns a user, or None to reject.
    Stub: while DEV_LOGIN_BYPASS is on, every attempt succeeds as the given username."""
    if settings.DEV_LOGIN_BYPASS:
        User = get_user_model()
        name = username or 'dev'
        user = User.objects.filter(username=name).first()
        if not user:
            user = User(username=name)
            user.set_unusable_password()
            user.save()
        return user
    return authenticate(request, username=username, password=password)


@require_POST
def login(request):
    if not _csrf_ok(request):
        return _error('Forbidden', 403)
    body = _json_body(request)
    username = body.get('username')
    if throttle.blocked('login', request, username):
        return _error('Too many failed attempts. Try again later.', 429)
    user = check_credentials(request, username, body.get('password'))
    if not user:
        throttle.record('login', request, username)
        return _error('Invalid username or password', 401)
    throttle.clear('login', request, username)
    return _session_response(user, auth.start_session(user))


@require_POST
def refresh(request):
    if not _csrf_ok(request):
        return _error('Forbidden', 403)
    raw = request.COOKIES.get(auth.COOKIE)
    result = auth.rotate(raw) if raw else None
    if not result:
        response = _error('Session expired', 401)
        auth.clear_cookie(response)
        return response
    return _session_response(*result)


@require_POST
def logout(request):
    if not _csrf_ok(request):
        return _error('Forbidden', 403)
    raw = request.COOKIES.get(auth.COOKIE)
    if raw:
        auth.revoke(raw)
    response = JsonResponse({})
    auth.clear_cookie(response)
    return response


@require_POST
def register(request):
    if not _csrf_ok(request):
        return _error('Forbidden', 403)
    if not settings.REGISTRATION_OPEN:
        return _error('Registration is closed', 403)
    body = _json_body(request)
    username, email, password = (body.get('username') or '').strip(), (body.get('email') or '').strip(), body.get('password') or ''
    if not username:
        return _error('Username is required', 400)
    User = get_user_model()
    if User.objects.filter(username__iexact=username).exists():
        return _error('That username is taken', 409)
    try:
        validate_password(password, User(username=username, email=email))
    except ValidationError as exc:
        return _error(' '.join(exc.messages), 400)
    user = User.objects.create_user(username=username, email=email, password=password)
    return _session_response(user, auth.start_session(user))


@require_POST
def forgot_password(request):
    """Always answers the same way, so it cannot be used to discover which accounts exist."""
    if not _csrf_ok(request):
        return _error('Forbidden', 403)
    identifier = (_json_body(request).get('identifier') or '').strip()
    if throttle.blocked('forgot', request, identifier):
        return _error('Too many requests. Try again later.', 429)
    throttle.record('forgot', request, identifier)  # every request counts, so this cannot be used to send floods of email
    user = None
    if identifier:
        user = (get_user_model().objects.filter(Q(username__iexact=identifier) | Q(email__iexact=identifier), is_active=True)
                .exclude(email='').first())
    if user:
        uid, token = urlsafe_base64_encode(force_bytes(user.pk)), default_token_generator.make_token(user)
        link = f"{settings.FRONTEND_URL.rstrip('/')}/reset?uid={uid}&token={token}"
        try:
            send_mail('Reset your Report Manager password',
                      f'Use this link to choose a new password (valid for one hour):\n\n{link}\n\nIf you did not ask for this, ignore this email.',
                      None, [user.email])
        except Exception:  # a mail outage must not reveal whether the account exists
            log.exception('Could not send the password reset email')
    return JsonResponse({'message': 'If that account exists, a reset link has been sent.'})


@require_POST
def reset_password(request):
    if not _csrf_ok(request):
        return _error('Forbidden', 403)
    body = _json_body(request)
    password = body.get('password') or ''
    User = get_user_model()
    try:
        user = User.objects.get(pk=force_str(urlsafe_base64_decode(body.get('uid') or '')))
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        user = None
    if not user or not default_token_generator.check_token(user, body.get('token') or ''):
        return _error('This reset link is invalid or has expired', 400)
    try:
        validate_password(password, user)
    except ValidationError as exc:
        return _error(' '.join(exc.messages), 400)
    user.set_password(password)
    user.save(update_fields=['password'])
    auth.revoke_all(user)  # also invalidates the link: the token depends on the old password hash
    return JsonResponse({'message': 'Password updated. You can sign in now.'})


@require_GET
@auth.require_auth
def section(request, name):
    start = end = None
    if name == 'tickets':  # the only section that depends on a date range
        try:
            start, end = (date.fromisoformat(request.GET[k]) for k in ('from', 'to'))
        except (KeyError, ValueError):
            return _error("'from' and 'to' are required (YYYY-MM-DD)", 400)
        if start > end:
            return _error("'from' must not be after 'to'", 400)
    for param in ('client', 'org'):   # one or several, comma separated
        try:
            client_lib.parse_ids(request.GET.get(param))
        except ValueError:
            return _error(f"'{param}' must be a number, or up to 50 numbers separated by commas", 400)
    if request.GET.get('device') and not request.GET['device'].isdigit():
        return _error("'device' must be a number", 400)
    params = request.GET
    if 'weights' in request.GET:   # a saved report's own patch weights, as JSON
        raw = request.GET['weights']
        try:
            weights, error = scoring.clean_weights(json.loads(raw)) if len(raw) <= 20000 else (None, 'too long')
        except ValueError:
            weights, error = None, 'not JSON'
        if error:
            return _error("'weights' must be a JSON object of numbers from 0 to 1000", 400)
        params = {**request.GET.dict(), 'weights': weights}
    try:
        payload = data.get_section(name, start, end, params, request.user)
    except access_lib.AccessDenied:   # asked for a client or customer this person may not see: refused, not shown as empty
        return _error('Forbidden', 403)
    if payload is None:
        return _error(f'Unknown section: {name}', 404)
    return JsonResponse(payload)


# ---------------- clients ----------------
def _clients():
    return Client.objects.select_related('company').prefetch_related('sources', 'aliases')


def _client_dict(c):
    return {'id': c.pk, 'name': c.name, 'contact_email': c.contact_email, 'notes': c.notes, 'is_active': c.is_active, 'company': _company_dict(c.company),
            'sources': sorted(({'source': s.source, 'external_id': s.external_id} for s in c.sources.all()),
                              key=lambda s: (s['source'], s['external_id'])),
            'aliases': sorted(a.alias for a in c.aliases.all())}


def _client_fields(body, client=None, allowed=None):
    """Validated fields, or an error message. Sources are matched by the source system's own ID; an ID or an alias
    can belong to only one client."""
    name = (body.get('name') or '').strip()
    if not name:
        return None, 'Name is required'
    others = Client.objects.exclude(pk=client.pk) if client else Client.objects.all()

    def shown(other):   # never name a client the person may not see (it belongs to another company)
        return other.name if access_lib.client_visible(other.pk, allowed) else 'another client'
    taken = {client_lib.normalise(o.name): shown(o) for o in others}
    taken.update({a.alias_norm: shown(a.client) for a in ClientAlias.objects.exclude(client=client).select_related('client')})
    if client_lib.normalise(name) in taken:
        return None, 'That name is already used by another client or alias'

    sources, seen = [], set()
    for item in body.get('sources') or []:
        source, external_id = (item or {}).get('source'), str((item or {}).get('external_id') or '').strip()
        if source not in client_lib.SOURCES:
            return None, f'Unknown source: {source}'
        if not external_id or len(external_id) > 100:
            return None, f'{client_lib.SOURCE_LABELS[source]} needs an ID of up to 100 characters'
        if (source, external_id) in seen:
            continue
        seen.add((source, external_id))
        owner = ClientSourceMap.objects.filter(source=source, external_id=external_id).exclude(client=client).select_related('client').first()
        if owner:
            return None, f'{client_lib.SOURCE_LABELS[source]} {external_id} is already linked to {shown(owner.client)}'
        sources.append((source, external_id))

    aliases, norms = [], {client_lib.normalise(name)}
    for alias in body.get('aliases') or []:
        alias = str(alias or '').strip()
        norm = client_lib.normalise(alias)
        if not norm or norm in norms:
            continue
        if len(alias) > 200:
            return None, 'An alias can have at most 200 characters'
        if norm in taken:
            return None, f'The alias "{alias}" is already used by {taken[norm]}'
        norms.add(norm)
        aliases.append((alias, norm))

    return {'name': name, 'contact_email': (body.get('contact_email') or '').strip(), 'notes': (body.get('notes') or '').strip(),
            'is_active': bool(body.get('is_active', True)), 'sources': sources, 'aliases': aliases}, None


def _save_client(client, fields):
    sources, aliases = fields.pop('sources'), fields.pop('aliases')
    with transaction.atomic():
        if client is None:
            client = Client.objects.create(**fields)
        else:
            for key, value in fields.items():
                setattr(client, key, value)
            client.save()
        client.sources.all().delete()
        ClientSourceMap.objects.bulk_create([ClientSourceMap(client=client, source=s, external_id=e) for s, e in sources])
        client.aliases.all().delete()
        ClientAlias.objects.bulk_create([ClientAlias(client=client, alias=a, alias_norm=n) for a, n in aliases])
    return _clients().get(pk=client.pk)


@require_http_methods(['GET', 'POST'])
@auth.require_auth
def clients(request):
    if request.method == 'GET':
        queryset = _clients().order_by('name')
        allowed = access_lib.allowed_client_ids(request.user)   # someone limited to some clients sees only those
        if allowed is not None:
            queryset = queryset.filter(pk__in=allowed)
        return JsonResponse([_client_dict(c) for c in queryset], safe=False)
    if not request.user.is_staff:
        return _error('Forbidden', 403)
    body = _json_body(request)
    company_id, error = _new_client_company(request.user, body)
    if error:
        return _error(error, 400)
    fields, error = _client_fields(body, allowed=access_lib.allowed_client_ids(request.user))
    if error:
        return _error(error, 400)
    try:
        return JsonResponse(_client_dict(_save_client(None, {**fields, 'company_id': company_id})), status=201)
    except IntegrityError:
        return _error('Another change conflicts with this one. Reload and try again.', 409)


@require_http_methods(['PUT'])
@auth.require_auth
@auth.staff_required
def client_detail(request, pk):
    allowed = access_lib.allowed_client_ids(request.user)
    client = Client.objects.filter(pk=pk).first()
    if not client or not access_lib.client_visible(client.pk, allowed):   # another company's client looks like one that does not exist
        return _error('Client not found', 404)
    fields, error = _client_fields(_json_body(request), client, allowed)
    if error:
        return _error(error, 400)
    try:
        return JsonResponse(_client_dict(_save_client(client, fields)))
    except IntegrityError:
        return _error('Another change conflicts with this one. Reload and try again.', 409)


def _new_client_company(user, body):
    """(company ID, error) for a client being created. A company's administrator creates it in their own company; the platform owner may
    name the company (or none: unclaimed)."""
    if access_lib.is_owner(user):
        if body.get('company') is None:
            return None, None
        company = body['company']
        if isinstance(company, bool) or not isinstance(company, int) or not Company.objects.filter(pk=company).exists():
            return None, 'Unknown company'
        return company, None
    return access_lib.company_id_of(user), None


@require_POST
@auth.require_auth
@auth.staff_required
def client_claim(request, pk):
    """Claim a client for a company. Until it is claimed only administrators can see it. An administrator claims it for their own
    company, and only while nobody has; the platform owner can give it to any company, or take it back (company null)."""
    allowed = access_lib.allowed_client_ids(request.user)
    client = _clients().filter(pk=pk).first()
    if not client or not access_lib.client_visible(client.pk, allowed):
        return _error('Client not found', 404)
    body = _json_body(request)
    if access_lib.is_owner(request.user):
        if 'company' not in body:
            return _error('Say which company, or null to take it back', 400)
        company_id = body['company']
        if company_id is not None and (isinstance(company_id, bool) or not isinstance(company_id, int) or not Company.objects.filter(pk=company_id).exists()):
            return _error('Unknown company', 400)
    else:
        company_id = access_lib.company_id_of(request.user)
        if company_id is None:
            return _error('You are not part of a company', 403)
        if client.company_id not in (None, company_id):
            return _error('Client not found', 404)
    Client.objects.filter(pk=client.pk).update(company_id=company_id)
    return JsonResponse(_client_dict(_clients().get(pk=client.pk)))


def proposals_for_record(rows, mapped, everyone, available):
    """The unlinked customers as the record of gaps should know them: every one, whoever may see it, grouped without any client's name
    leaking into the grouping (the grouping only matters here for which customers are unlinked)."""
    return client_lib.group_customers(rows, mapped, {}, {}, available)


def _resolution_state(allowed=None):
    """Everything the resolution page and the confirm step share: readable sources, clients, and what is already linked."""
    rows, unavailable = client_lib.source_customers()
    available = [s for s in client_lib.SOURCES if s not in unavailable]
    everyone = [_client_dict(c) for c in _clients().order_by('name')]
    mine = [c for c in everyone if access_lib.client_visible(c['id'], allowed)]   # what this person may see and be told about
    known = {}
    for c in mine:
        for name in (c['name'], *c['aliases']):
            tokens = client_lib.tokenise(name)
            if tokens:
                known.setdefault(tokens, (c['id'], c['name']))
    # A customer linked to ANY client, whoever's it is, is not "unlinked": otherwise another company's customer would look free to claim.
    mapped = {(link['source'], link['external_id']) for c in everyone for link in c['sources']}
    return rows, unavailable, available, everyone, mine, known, mapped


@require_GET
@auth.require_auth
@auth.staff_required
def clients_resolution(request):
    """Customers in the source systems that no client is linked to, grouped by identical name words (an inference, flagged as
    such), plus where each client is missing from a source or points at a customer that no longer exists."""
    allowed = access_lib.allowed_client_ids(request.user)
    rows, unavailable, available, everyone, clients, known, mapped = _resolution_state(allowed)
    client_sources = {c['id']: {link['source'] for link in c['sources']} for c in clients}
    proposals = client_lib.group_customers(rows, mapped, known, client_sources, available)
    everyones = client_lib.client_discrepancies(everyone, rows, available)
    visible = {c['id'] for c in clients}
    discrepancies = [d for d in everyones if d['id'] in visible]
    # Keep the record of how long each gap has lasted, for EVERY client (a gap of another company's client must not be closed just because
    # this person cannot see it), and show only what this person may see. The page must never fail because of it, so a problem here is
    # logged and the page simply has no dates.
    since, resolved = {}, []
    try:
        gap_lib.refresh_gaps(gap_lib.gaps_from(proposals_for_record(rows, mapped, everyone, available), everyones), available)
        since, resolved = gap_lib.open_since(allowed), gap_lib.recently_resolved(allowed=allowed)
    except Exception:
        log.exception('Could not update the record of client gaps')
    for proposal in proposals:
        proposal['since'] = gap_lib.proposal_since(proposal, since)
    for row in discrepancies:
        row['since'] = [since.get(key) for key in gap_lib.row_keys(row)]   # in step with row['discrepancies']
    return JsonResponse({
        'recently_resolved': resolved,
        'sources': [{'key': k, 'label': client_lib.SOURCE_LABELS[k], 'available': k in available} for k in client_lib.SOURCES],
        'proposals': proposals, 'clients': discrepancies,
        'summary': {'clients': len(clients), 'clients_with_discrepancies': sum(1 for d in discrepancies if d['discrepancies']),
                    'proposals': len(proposals), 'inferred': sum(1 for p in proposals if p['inferred']),
                    'unlinked_customers': sum(len(p['members']) for p in proposals), 'open_gaps': len(since)}})


@require_POST
@auth.require_auth
@auth.staff_required
def clients_resolve(request):
    """Confirm proposals. Body: {groups: [{name?, client_id?, members: [{source, external_id, customer_name}]}]}.
    Without client_id a new client is created; with it the members are added to that client. Each group is saved on its
    own, so one conflict does not undo the rest. Spelling variants are kept as aliases."""
    groups = _json_body(request).get('groups')
    if not isinstance(groups, list) or not 0 < len(groups) <= 500:
        return _error('Send between 1 and 500 groups', 400)
    result = {'created': 0, 'extended': 0, 'errors': []}
    allowed = access_lib.allowed_client_ids(request.user)
    new_company = access_lib.company_id_of(request.user) if not access_lib.is_owner(request.user) else None   # a new client joins the administrator's company
    for group in groups:
        group = group if isinstance(group, dict) else {}
        members = [m for m in group.get('members') or [] if isinstance(m, dict)]
        label = group.get('name') or (members[0].get('customer_name') if members else '') or 'group'
        if not members:
            result['errors'].append({'group': label, 'error': 'No customers in this group'})
            continue
        client = None
        links = [{'source': m.get('source'), 'external_id': m.get('external_id')} for m in members]
        names = [m.get('customer_name') for m in members if m.get('customer_name')]
        if group.get('client_id'):
            client = _clients().filter(pk=group['client_id']).first()
            if client and not access_lib.client_visible(client.pk, allowed):
                client = None
            if not client:
                result['errors'].append({'group': label, 'error': 'That client no longer exists'})
                continue
            body = {**_client_dict(client), 'sources': _client_dict(client)['sources'] + links, 'aliases': _client_dict(client)['aliases'] + names}
        else:
            body = {'name': group.get('name') or (names[0] if names else ''), 'sources': links, 'aliases': names}
        fields, error = _client_fields(body, client, allowed)
        if error:
            result['errors'].append({'group': label, 'error': error})
            continue
        try:
            _save_client(client, fields if client else {**fields, 'company_id': new_company})
        except IntegrityError:
            result['errors'].append({'group': label, 'error': 'Another change conflicts with this one. Reload and try again.'})
            continue
        result['extended' if client else 'created'] += 1
    return JsonResponse(result)


# ---------------- who may see which clients ----------------
@require_GET
@auth.require_auth
@auth.staff_required
def access_overview(request):
    """Staff: the people of their company (the owner: everyone), the clients each may see (given one by one, and through the client groups they are in),
    the client groups, and whether the limits are switched on. Administrators see every client of their company whatever is listed."""
    owner, mine = access_lib.is_owner(request.user), access_lib.company_id_of(request.user)
    grants, in_groups = {}, {}
    for user_id, client_id in ClientAccess.objects.values_list('user_id', 'client_id'):
        grants.setdefault(user_id, []).append(client_id)
    groups = list(_visible_groups(request.user))
    for group in groups:
        for member in group.members.all():
            in_groups.setdefault(member.pk, []).append(group.pk)
    members = {m.user_id: m.company for m in CompanyMember.objects.select_related('company')}
    users = get_user_model().objects.order_by('username')
    clients = Client.objects.select_related('company').order_by('name')
    if not owner:   # an administrator never sees another company's people or clients
        users = users.filter(pk__in=[uid for uid, company in members.items() if company.pk == mine])
        clients = clients.filter(company_id=mine)
    return JsonResponse({
        'enforced': access_lib.enforced(), 'owner': owner, 'company': _user_company(request.user), 'can_make_admin': owner,
        'users': [groups_lib.person_payload(u.get_username(), u.get_full_name(), u.email, is_staff=u.is_staff, is_owner=u.is_superuser, is_active=u.is_active,
                                            company=_company_dict(members.get(u.pk)), clients=grants.get(u.pk, []), client_groups=in_groups.get(u.pk, [])) for u in users],
        'clients': [{'id': c.pk, 'name': c.name, 'company': _company_dict(c.company)} for c in clients],
        'client_groups': [_group_dict(g) for g in groups]})


@require_http_methods(['PUT'])
@auth.require_auth
@auth.staff_required
def access_for_user(request, username):
    """Staff: replace the clients one person may see. Body {clients: [client IDs]}."""
    user = get_user_model().objects.filter(username=username).first()
    theirs = access_lib.company_id_of(user) if user else None
    if not user or (not access_lib.is_owner(request.user) and theirs != access_lib.company_id_of(request.user)):
        return _error('User not found', 404)   # an administrator cannot tell whether someone in another company exists
    ids = _json_body(request).get('clients')
    if not isinstance(ids, list) or len(ids) > 500 or any(isinstance(i, bool) or not isinstance(i, int) for i in ids):
        return _error('Send clients as a list of client IDs (at most 500)', 400)
    if ids and theirs is None:
        return _error('This person is not part of a company yet', 400)
    found = set(Client.objects.filter(pk__in=ids, company_id=theirs).values_list('pk', flat=True))   # only their own company's clients
    unknown = sorted(set(ids) - found)
    if unknown:
        return _error(f'Unknown client ID: {unknown[0]}', 400)
    with transaction.atomic():
        ClientAccess.objects.filter(user=user).exclude(client_id__in=found).delete()
        have = set(ClientAccess.objects.filter(user=user).values_list('client_id', flat=True))
        ClientAccess.objects.bulk_create([ClientAccess(user=user, client_id=i) for i in sorted(found - have)])
    return JsonResponse({'username': user.get_username(), 'clients': sorted(found)})


# ---------------- companies ----------------
def _company_row(company):
    return {'id': company.pk, 'name': company.name, 'members': company.members.count(), 'clients': company.clients.count()}


def _company_name_error(name, exclude=None):
    if not name:
        return 'A company needs a name'
    if len(name) > 200:
        return 'A company name can have at most 200 characters'
    clash = Company.objects.exclude(pk=exclude) if exclude else Company.objects.all()
    if any(c.name.lower() == name.lower() for c in clash):
        return 'That name is already used by another company'
    return None


@require_http_methods(['GET', 'POST'])
@auth.require_auth
@auth.staff_required
def companies(request):
    """GET: the companies (an administrator sees only their own; the platform owner sees all). POST: the platform owner makes a new one."""
    owner = access_lib.is_owner(request.user)
    if request.method == 'GET':
        queryset = Company.objects.order_by('name')
        if not owner:
            mine = access_lib.company_id_of(request.user)
            queryset = queryset.filter(pk=mine) if mine else queryset.none()
        return JsonResponse([_company_row(c) for c in queryset], safe=False)
    if not owner:
        return _error('Forbidden', 403)
    name = str(_json_body(request).get('name') or '').strip()
    error = _company_name_error(name)
    if error:
        return _error(error, 400)
    try:
        return JsonResponse(_company_row(Company.objects.create(name=name)), status=201)
    except IntegrityError:
        return _error('That name is already used by another company', 400)


@require_http_methods(['PUT'])
@auth.require_auth
@auth.staff_required
def company_detail(request, pk):
    """The platform owner renames a company."""
    if not access_lib.is_owner(request.user):
        return _error('Forbidden', 403)
    company = Company.objects.filter(pk=pk).first()
    if not company:
        return _error('Company not found', 404)
    name = str(_json_body(request).get('name') or '').strip()
    error = _company_name_error(name, exclude=company.pk)
    if error:
        return _error(error, 400)
    company.name = name
    company.save(update_fields=['name'])
    return JsonResponse(_company_row(company))


@require_http_methods(['PUT'])
@auth.require_auth
@auth.staff_required
def user_company(request, username):
    """The platform owner puts a person in a company (body {company: id}) or takes them out (null). Moving someone drops the clients they
    were given in the old company, which no longer apply."""
    if not access_lib.is_owner(request.user):
        return _error('Forbidden', 403)
    user = get_user_model().objects.filter(username=username).first()
    if not user:
        return _error('User not found', 404)
    body = _json_body(request)
    if 'company' not in body:
        return _error('Say which company, or null to take them out of theirs', 400)
    company = None
    if body['company'] is not None:
        company = None if isinstance(body['company'], bool) or not isinstance(body['company'], int) else Company.objects.filter(pk=body['company']).first()
        if not company:
            return _error('Unknown company', 400)
    with transaction.atomic():
        ClientAccess.objects.filter(user=user).delete()
        user.client_groups.clear()   # the old company's groups no longer apply either
        if company is None:
            CompanyMember.objects.filter(user=user).delete()
        else:
            CompanyMember.objects.update_or_create(user=user, defaults={'company': company})
    return JsonResponse({'username': user.get_username(), 'company': _company_dict(company)})


# ---------------- client groups, and making people ----------------
def _group_dict(group):
    return groups_lib.group_payload(group.pk, group.name, group.all_clients, [c.pk for c in group.clients.all()],
                                    [u.get_username() for u in group.members.all()], _company_dict(group.company))


def _visible_groups(user):
    """The client groups this person may see: the platform owner all of them, an administrator those of their own company."""
    queryset = ClientGroup.objects.select_related('company').prefetch_related('clients', 'members').order_by('company__name', 'name')
    if access_lib.is_owner(user):
        return queryset
    mine = access_lib.company_id_of(user)
    return queryset.filter(company_id=mine) if mine else queryset.none()


def _acting_company(user, body):
    """The company an administrator is acting in: their own. The platform owner names one in the request (company: its ID), or uses their own if
    they have one. Returns (company, None) or (None, error)."""
    mine = access_lib.company_id_of(user)
    if access_lib.is_owner(user):
        wanted = body.get('company', mine)
        company = None if isinstance(wanted, bool) or not isinstance(wanted, int) else Company.objects.filter(pk=wanted).first()
        return (company, None) if company else (None, 'Choose a company')
    company = Company.objects.filter(pk=mine).first() if mine else None
    return (company, None) if company else (None, 'You are not part of a company yet')


def _group_in_reach(user, pk):
    return _visible_groups(user).filter(pk=pk).first()    # another company's group looks the same as none


@require_http_methods(['GET', 'POST'])
@auth.require_auth
@auth.staff_required
def client_groups(request):
    """GET: the client groups (an administrator sees their own company's; the platform owner all). POST: make one. Body {name, all_clients, clients: [IDs]},
    and for the platform owner {company: ID}. A group holds only its own company's clients."""
    if request.method == 'GET':
        return JsonResponse([_group_dict(g) for g in _visible_groups(request.user)], safe=False)
    body = _json_body(request)
    company, error = _acting_company(request.user, body)
    if error:
        return _error(error, 400)
    fields, error = groups_lib.clean_group(body, taken_names=list(company.client_groups.values_list('name', flat=True)),
                                           company_client_ids=list(company.clients.values_list('pk', flat=True)))
    if error:
        return _error(error, 400)
    try:
        with transaction.atomic():
            group = ClientGroup.objects.create(company=company, name=fields['name'], all_clients=fields['all_clients'])
            group.clients.set(fields['clients'])
    except IntegrityError:
        return _error('There is already a group with that name', 400)
    return JsonResponse(_group_dict(_group_in_reach(request.user, group.pk)), status=201)


@require_http_methods(['PUT', 'DELETE'])
@auth.require_auth
@auth.staff_required
def client_group_detail(request, pk):
    """PUT: replace a group (the whole group, as for POST). DELETE: remove it; the people in it simply lose what it gave them."""
    group = _group_in_reach(request.user, pk)
    if not group:
        return _error('Group not found', 404)
    if request.method == 'DELETE':
        group.delete()
        return JsonResponse({'deleted': pk})
    company = group.company
    fields, error = groups_lib.clean_group(_json_body(request), taken_names=list(company.client_groups.exclude(pk=group.pk).values_list('name', flat=True)),
                                           company_client_ids=list(company.clients.values_list('pk', flat=True)))
    if error:
        return _error(error, 400)
    try:
        with transaction.atomic():
            group.name, group.all_clients = fields['name'], fields['all_clients']
            group.save(update_fields=['name', 'all_clients'])
            group.clients.set(fields['clients'])
    except IntegrityError:
        return _error('There is already a group with that name', 400)
    return JsonResponse(_group_dict(_group_in_reach(request.user, pk)))


def _setup_link_for(user):
    """A one-time link for this person to choose their password (the page "forgot password" uses), emailed when there is an address. The link is also
    returned, because the administrator may need to pass it on: with the default email backend nothing is really sent, and `sent` says so."""
    uid, token = urlsafe_base64_encode(force_bytes(user.pk)), default_token_generator.make_token(user)
    link = groups_lib.setup_link(settings.FRONTEND_URL, uid, token)
    minutes = settings.PASSWORD_RESET_TIMEOUT // 60
    sent = 'none'
    if user.email:
        subject, text = groups_lib.setup_email(user.first_name or user.get_username(), link, minutes)
        failed = False
        try:
            send_mail(subject, text, None, [user.email])
        except Exception:   # the link is still returned, so the administrator can pass it on
            log.exception('Could not send the account email')
            failed = True
        sent = groups_lib.how_it_was_sent(settings.EMAIL_BACKEND, failed)
    return {'username': user.get_username(), 'email': user.email, 'setup_link': link, 'valid_for_minutes': minutes, 'sent': sent}


def _person_in_reach(acting, username):
    """The person, if this administrator may manage them: anyone for the platform owner, otherwise only people of their own company. Else None."""
    user = get_user_model().objects.filter(username=username).first()
    if not user:
        return None
    if access_lib.is_owner(acting):
        return user
    theirs, mine = access_lib.company_id_of(user), access_lib.company_id_of(acting)
    return user if mine is not None and theirs == mine else None


@require_POST
@auth.require_auth
@auth.staff_required
def create_person(request):
    """Make a new person in the administrator's company (the platform owner names the company). No password is chosen for them: they get a one-time link
    to choose their own. Body {username, email, first_name, last_name, client_groups: [IDs], is_staff}. Only the platform owner can make an administrator."""
    body = _json_body(request)
    company, error = _acting_company(request.user, body)
    if error:
        return _error(error, 400)
    User = get_user_model()
    fields, error = groups_lib.clean_person(body, taken_usernames=list(User.objects.values_list('username', flat=True)),
                                            valid_group_ids=list(company.client_groups.values_list('pk', flat=True)), can_set_admin=access_lib.is_owner(request.user))
    if error:
        return _error(error, 409 if error == 'That username is taken' else 400)
    try:
        with transaction.atomic():
            user = User(username=fields['username'], email=fields['email'], first_name=fields['first_name'], last_name=fields['last_name'], is_staff=fields['is_staff'])
            user.set_unusable_password()
            user.save()
            CompanyMember.objects.create(user=user, company=company)
            for group in company.client_groups.filter(pk__in=fields['client_groups']):
                group.members.add(user)
    except IntegrityError:
        return _error('That username is taken', 409)
    return JsonResponse(_setup_link_for(user), status=201)


@require_http_methods(['PUT'])
@auth.require_auth
@auth.staff_required
def update_person(request, username):
    """Change a person: name, email, whether they are active (a person is deactivated, never deleted), their client groups, and (platform owner only)
    whether they are an administrator. Send only what changes."""
    user = _person_in_reach(request.user, username)
    if not user:
        return _error('User not found', 404)   # someone in another company looks the same as no one
    company_id = access_lib.company_id_of(user)
    valid = list(ClientGroup.objects.filter(company_id=company_id).values_list('pk', flat=True)) if company_id else []
    fields, error = groups_lib.clean_person_update(_json_body(request), valid_group_ids=valid, can_set_admin=access_lib.is_owner(request.user),
                                                   is_self=user.pk == request.user.pk, target_is_owner=user.is_superuser)
    if error:
        return _error(error, 403 if error.startswith('Only the platform owner') else 400)
    if fields.get('client_groups') and company_id is None:
        return _error('This person is not part of a company yet', 400)
    with transaction.atomic():
        for key in ('first_name', 'last_name', 'email', 'is_active', 'is_staff'):
            if key in fields:
                setattr(user, key, fields[key])
        user.save()
        if 'client_groups' in fields:
            user.client_groups.set(list(ClientGroup.objects.filter(company_id=company_id, pk__in=fields['client_groups'])) if company_id else [])
    if fields.get('is_active') is False:
        auth.revoke_all(user)   # a deactivated person is signed out everywhere at once
    return JsonResponse({'username': user.get_username(), **{k: v for k, v in fields.items()}})


@require_POST
@auth.require_auth
@auth.staff_required
def new_setup_link(request, username):
    """A fresh link for a person to choose a password, when the first has expired or never arrived."""
    user = _person_in_reach(request.user, username)
    if not user:
        return _error('User not found', 404)
    if not user.is_active:
        return _error('This person is deactivated; bring them back first', 400)
    return JsonResponse(_setup_link_for(user))


# ---------------- saved reports ----------------
def _report_dict(report, user):
    return {'id': report.pk, 'name': report.name, 'shared': report.shared, 'owner': report.owner.username, 'mine': report.owner_id == user.id,
            'config': report.config, 'updated_at': report.updated_at.isoformat().replace('+00:00', 'Z')}


def _report_fields(body, report, user):
    """(name, shared, config) to save, or an error message. Anyone may keep their own reports; only staff may share one with everybody."""
    name = body.get('name', report.name if report else '')
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120:
        return None, 'A report needs a name of at most 120 characters'
    shared = body.get('shared', report.shared if report else False)
    if not isinstance(shared, bool):
        return None, "'shared' must be true or false"
    if shared and not user.is_staff and not (report and report.shared):
        return None, 'Only staff can share a report'
    raw = body['config'] if 'config' in body else (report.config if report else None)
    if len(json.dumps(raw, default=str)) > report_lib.MAX_CONFIG_BYTES:
        return None, 'The report is too large'
    try:
        config = report_lib.validate_template(raw)
    except ValueError as exc:
        return None, str(exc)
    return (name.strip(), shared, config), None


def _visible_reports(user):
    return Report.objects.filter(Q(owner=user) | Q(shared=True)).select_related('owner')


@require_http_methods(['GET', 'POST'])
@auth.require_auth
def reports(request):
    """GET: your reports and the shared ones. POST: save a new report (it is yours)."""
    if request.method == 'GET':
        return JsonResponse([_report_dict(r, request.user) for r in _visible_reports(request.user).order_by('name', 'pk')], safe=False)
    fields, error = _report_fields(_json_body(request), None, request.user)
    if error:
        return _error(error, 400)
    name, shared, config = fields
    if Report.objects.filter(owner=request.user, name__iexact=name).exists():
        return _error('You already have a report with that name', 400)
    try:
        report = Report.objects.create(owner=request.user, name=name, shared=shared, config=config)
    except IntegrityError:
        return _error('You already have a report with that name', 400)
    return JsonResponse(_report_dict(report, request.user), status=201)


@require_http_methods(['PUT', 'DELETE'])
@auth.require_auth
def report_detail(request, pk):
    """PUT replaces a report's name, sharing or definition; DELETE removes it. Only its owner (or staff) may."""
    report = _visible_reports(request.user).filter(pk=pk).first()
    if not report:
        return _error('Report not found', 404)
    if report.owner_id != request.user.id and not request.user.is_staff:
        return _error('Forbidden', 403)
    if request.method == 'DELETE':
        report.delete()
        return JsonResponse({})
    fields, error = _report_fields(_json_body(request), report, request.user)
    if error:
        return _error(error, 400)
    name, shared, config = fields
    if Report.objects.filter(owner=report.owner, name__iexact=name).exclude(pk=report.pk).exists():
        return _error('You already have a report with that name', 400)
    report.name, report.shared, report.config = name, shared, config
    try:
        report.save()
    except IntegrityError:
        return _error('You already have a report with that name', 400)
    return JsonResponse(_report_dict(report, request.user))


# ---------------- score weights ----------------
def _weights_payload(user):
    shared, own = scoring.shared_weights(), scoring.user_weights(user)
    return {'shared': shared, 'user': own, 'effective': scoring.effective_weights(shared, own)}


@require_http_methods(['GET', 'PUT'])
@auth.require_auth
def weights(request):
    """GET: shared, the user's own, and the effective weights. PUT {scope: 'user' | 'shared', weights: {...}} replaces
    that set (an empty object clears it). Changing the shared set needs staff."""
    if request.method == 'GET':
        return JsonResponse(_weights_payload(request.user))
    body = _json_body(request)
    scope = body.get('scope', 'user')
    if scope not in ('user', 'shared'):
        return _error("scope must be 'user' or 'shared'", 400)
    if scope == 'shared' and not request.user.is_staff:
        return _error('Only staff can change the shared weights', 403)
    cleaned, error = scoring.clean_weights(body.get('weights', {}))
    if error:
        return _error(error, 400)
    owner = request.user if scope == 'user' else None
    with transaction.atomic():
        ScoreWeight.objects.filter(user=owner).delete()
        ScoreWeight.objects.bulk_create([ScoreWeight(user=owner, key=k, weight=w) for k, w in cleaned.items()])
    return JsonResponse(_weights_payload(request.user))
