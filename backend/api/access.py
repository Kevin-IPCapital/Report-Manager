"""Which clients a signed-in person may see.

Three levels, and a wall between companies (all of it only while access is enforced, settings.CLIENT_ACCESS_ENFORCED, off by default;
while it is off every signed-in person sees everything, exactly as before, so turning it on is a deliberate step):

* The **platform owner** (a Django superuser) sits above the companies and sees everything.
* A **company administrator** (staff, with a company) sees all of their own company's clients, plus clients nobody has claimed yet (so
  there is someone who can claim them) and data not linked to any client yet. They never see another company's clients: an admin of
  company A cannot see company B's data.
* Anyone else sees only the clients they were given (one by one, or through the client groups they are in), and only if those belong to their own
  company. A person with no company sees nothing.

`allowed_client_ids` is the one place that decides: None means no limit, otherwise a Scope (a set of client IDs, which also says whether
data not linked to any client may be seen). The data layer applies it. The decision itself is the pure function `scope_for`."""
from django.conf import settings


class AccessDenied(Exception):
    """The person asked for a client or customer they may not see."""


class Scope(frozenset):
    """The client IDs a person may see. `unlinked` says whether they may also see data that is not linked to any client yet (so that an
    administrator can find it and link it)."""
    unlinked = False

    def __new__(cls, ids=(), unlinked=False):
        scope = super().__new__(cls, ids)
        scope.unlinked = bool(unlinked)
        return scope


def enforced():
    return bool(getattr(settings, 'CLIENT_ACCESS_ENFORCED', False))


def scope_for(*, owner, staff, has_company, company_clients, unclaimed, granted):
    """Pure: the decision. Returns None (no limit) or a Scope.

    `company_clients` are the IDs of the person's company's clients, `unclaimed` those of clients no company has claimed, `granted` the
    clients this person was given (used only for someone who is not an administrator)."""
    if owner:
        return None
    if not has_company:
        return Scope()
    if staff:
        return Scope(set(company_clients) | set(unclaimed), unlinked=True)
    return Scope(set(company_clients) & set(granted))


def company_id_of(user):
    from .models import CompanyMember
    return CompanyMember.objects.filter(user=user).values_list('company_id', flat=True).first()


def is_owner(user):
    return bool(user is not None and getattr(user, 'is_superuser', False))


def allowed_client_ids(user):
    """None when there is no limit (nobody signed in, the platform owner, or access not enforced); otherwise a Scope."""
    if user is None or not enforced() or is_owner(user):
        return None
    from .models import Client, ClientAccess
    company_id = company_id_of(user)
    if company_id is None:
        return scope_for(owner=False, staff=False, has_company=False, company_clients=(), unclaimed=(), granted=())
    staff = bool(getattr(user, 'is_staff', False))
    company_clients = set(Client.objects.filter(company_id=company_id).values_list('pk', flat=True))
    return scope_for(
        owner=False, staff=staff, has_company=True,
        company_clients=company_clients,
        unclaimed=Client.objects.filter(company__isnull=True).values_list('pk', flat=True) if staff else (),
        granted=() if staff else set(ClientAccess.objects.filter(user=user).values_list('client_id', flat=True)) | group_client_ids(user, company_id, company_clients))


def group_client_ids(user, company_id, company_clients):
    """The clients this person's client groups give them: every group of their company that they are in (see groups.group_clients)."""
    from . import groups
    from .models import ClientGroup
    mine = ClientGroup.objects.filter(members=user, company_id=company_id).prefetch_related('clients')
    return groups.group_clients([{'all_clients': g.all_clients, 'clients': [c.pk for c in g.clients.all()]} for g in mine], company_clients)


def is_limited(user):
    return allowed_client_ids(user) is not None


def is_scoped_member(user):
    """Limited to particular clients they were given (not an administrator, not the owner)."""
    return enforced() and user is not None and not is_owner(user) and not getattr(user, 'is_staff', False)


def client_visible(client_id, allowed):
    """May this client be seen? (A client that does not exist, or has none, is `None`: see row_allowed.)"""
    return allowed is None or client_id in allowed


def row_allowed(client_id, allowed):
    """May a row with this client ID be seen? A row linked to no client yet can be seen only by someone allowed to (an administrator)."""
    if allowed is None:
        return True
    if client_id is None:
        return bool(getattr(allowed, 'unlinked', False))
    return client_id in allowed


def option_allowed(option, allowed):
    """May this client choice (as the patch data builds them: `client` is the client's ID, or None for an unlinked customer) be seen?"""
    return row_allowed(option['client'], allowed)


def org_units_of(allowed):
    """The N-central customers (org units) a person may see: those of their clients, and, for an administrator, those linked to no client."""
    from . import clients
    units = {unit for client_id in allowed for unit in clients.org_units_for(client_id)}
    if getattr(allowed, 'unlinked', False):
        linked = {int(org) for org in clients.ncentral_org_map()}
        units |= {row['org_unit_id'] for row in clients.device_customers()} - linked
    return sorted(units)


def check_request(params, allowed):
    """Refuse a request that names a client, or a customer, outside what is allowed. Anything not named is limited by the data layer."""
    from . import clients
    try:
        client_ids, orgs = clients.parse_ids(params.get('client')), clients.parse_ids(params.get('org'))
    except ValueError:
        raise AccessDenied('an unreadable client or customer') from None
    outside = [client_id for client_id in client_ids if client_id not in allowed]
    if outside:
        raise AccessDenied(f'client {outside[0]}')
    mine = set(org_units_of(allowed)) if orgs else set()
    outside = [org for org in orgs if org not in mine]
    if outside:
        raise AccessDenied(f'customer {outside[0]}')


def only_allowed(rows, allowed, key='client_id'):
    """The rows a person may see (see row_allowed)."""
    return [r for r in rows if row_allowed(r.get(key), allowed)]
