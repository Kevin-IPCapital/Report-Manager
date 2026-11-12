"""Client groups, and making a new person. The rules, with no database and no Django, so they can be tested without either.

A **client group** is a named list of clients (or "all of the company's clients"). A person can be in several, and sees the clients of all of
them together (plus any clients they were given one by one, which still work). A group belongs to one company, and so does everything in it:
a company is a wall, and nothing here lets one company's group hold another company's client.

A **new person** is made by an administrator with no password chosen for them: the account starts with an unusable password, and a one-time
link to the "choose a password" page is emailed (and shown once to the administrator, as a fallback). The people views in views.py are thin
and use these functions for every decision, so what is checked here is what the server does."""
import re

MAX_NAME = 80
MAX_CLIENTS = 500
MAX_GROUPS_PER_PERSON = 50
USERNAME = re.compile(r'^[\w.@+-]{1,150}$')           # what Django accepts in a username
EMAIL = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


def _is_id(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _ids(raw, limit, what):
    """A list of whole numbers, without repeats, or an error sentence."""
    if not isinstance(raw, list) or len(raw) > limit or not all(_is_id(i) for i in raw):
        return None, f'Send {what} as a list of IDs (at most {limit})'
    return sorted(set(raw)), None


def clean_group(body, *, taken_names, company_client_ids):
    """A client group from a request: (fields, None) or (None, error).

    `taken_names` are the names of the company's other groups (compared ignoring case and spaces), `company_client_ids` the IDs of its clients. A group
    that is "all clients" holds no list of its own (it is always every client of the company, including ones made later). A group with no clients
    at all is allowed: it is just empty until clients are put in it."""
    if not isinstance(body, dict):
        return None, 'Send the group as an object'
    name = body.get('name')
    if not isinstance(name, str) or not name.strip():
        return None, 'Give the group a name'
    name = ' '.join(name.split())
    if len(name) > MAX_NAME:
        return None, f'A name can have at most {MAX_NAME} characters'
    if name.casefold() in {' '.join(str(n).split()).casefold() for n in taken_names}:
        return None, 'There is already a group with that name'
    everyone = body.get('all_clients', False)
    if not isinstance(everyone, bool):
        return None, 'all_clients must be true or false'
    if everyone:
        return {'name': name, 'all_clients': True, 'clients': []}, None
    ids, error = _ids(body.get('clients', []), MAX_CLIENTS, 'clients')
    if error:
        return None, error
    unknown = [i for i in ids if i not in set(company_client_ids)]
    if unknown:
        return None, f'Unknown client ID: {unknown[0]}'                       # also what a client of another company looks like: it is not named
    return {'name': name, 'all_clients': False, 'clients': ids}, None


def group_clients(groups, company_clients):
    """The client IDs a person's groups give them: for each group, every client of the company if it is "all clients", otherwise its own list.
    Only clients of the company count, whatever a group holds. `groups` are {'all_clients': bool, 'clients': [IDs]}."""
    mine = set(company_clients)
    seen = set()
    for group in groups:
        seen |= mine if group.get('all_clients') else set(group.get('clients', [])) & mine
    return seen


def _person_text(body, key, limit, label):
    value = body.get(key, '')
    if not isinstance(value, str) or len(value.strip()) > limit:
        return None, f'{label} must be text of at most {limit} characters'
    return value.strip(), None


def clean_person(body, *, taken_usernames, valid_group_ids, can_set_admin):
    """A new person from a request: (fields, None) or (None, error). Email is required, because the link to choose a password is sent to it.
    Only the platform owner may make an administrator (`can_set_admin`)."""
    if not isinstance(body, dict):
        return None, 'Send the person as an object'
    username = body.get('username')
    if not isinstance(username, str) or not username.strip():
        return None, 'Username is required'
    username = username.strip()
    if not USERNAME.fullmatch(username):
        return None, 'A username can only have letters, numbers and . @ + - _ (up to 150)'
    if username.casefold() in {str(u).casefold() for u in taken_usernames}:
        return None, 'That username is taken'
    email, error = _person_text(body, 'email', 254, 'Email')
    if error:
        return None, error
    if not email:
        return None, 'Email is required: the link to choose a password is sent there'
    if not EMAIL.fullmatch(email):
        return None, 'That does not look like an email address'
    first, error = _person_text(body, 'first_name', 150, 'First name')
    if error:
        return None, error
    last, error = _person_text(body, 'last_name', 150, 'Last name')
    if error:
        return None, error
    groups, error = _group_choice(body.get('client_groups', []), valid_group_ids)
    if error:
        return None, error
    staff = body.get('is_staff', False)
    if not isinstance(staff, bool):
        return None, 'is_staff must be true or false'
    if staff and not can_set_admin:
        return None, 'Only the platform owner can make an administrator'
    return {'username': username, 'email': email, 'first_name': first, 'last_name': last, 'client_groups': groups, 'is_staff': staff}, None


def _group_choice(raw, valid_group_ids):
    ids, error = _ids(raw, MAX_GROUPS_PER_PERSON, 'client_groups')
    if error:
        return None, error
    unknown = [i for i in ids if i not in set(valid_group_ids)]
    return (None, f'Unknown client group: {unknown[0]}') if unknown else (ids, None)


def clean_person_update(body, *, valid_group_ids, can_set_admin, is_self, target_is_owner):
    """Changes to an existing person: (fields, None) or (None, error); `fields` holds only what was sent. Nobody can deactivate themselves (they
    would lock themselves out), and nobody but the platform owner can change an administrator's rank or touch the owner."""
    if not isinstance(body, dict) or not body:
        return None, 'Send what to change'
    if target_is_owner and not can_set_admin:
        return None, 'Only the platform owner can change the platform owner'
    fields = {}
    for key, limit, label in (('first_name', 150, 'First name'), ('last_name', 150, 'Last name')):
        if key in body:
            fields[key], error = _person_text(body, key, limit, label)
            if error:
                return None, error
    if 'email' in body:
        email, error = _person_text(body, 'email', 254, 'Email')
        if error:
            return None, error
        if not email:
            return None, 'Email cannot be empty: the link to choose a password is sent there'
        if not EMAIL.fullmatch(email):
            return None, 'That does not look like an email address'
        fields['email'] = email
    if 'is_active' in body:
        if not isinstance(body['is_active'], bool):
            return None, 'is_active must be true or false'
        if is_self and not body['is_active']:
            return None, 'You cannot deactivate yourself'
        fields['is_active'] = body['is_active']
    if 'is_staff' in body:
        if not isinstance(body['is_staff'], bool):
            return None, 'is_staff must be true or false'
        if not can_set_admin:
            return None, 'Only the platform owner can change who is an administrator'
        if is_self and not body['is_staff']:
            return None, 'You cannot remove your own administrator rank'
        fields['is_staff'] = body['is_staff']
    if 'client_groups' in body:
        fields['client_groups'], error = _group_choice(body['client_groups'], valid_group_ids)
        if error:
            return None, error
    return (fields, None) if fields else (None, 'Nothing to change')


def setup_link(frontend_url, uid, token):
    """The link to the page where a person chooses their password (the same page "forgot password" uses)."""
    return f"{str(frontend_url).rstrip('/')}/reset?uid={uid}&token={token}"


def setup_email(name, link, minutes):
    """(subject, body) of the email that lets a new person choose a password."""
    hello = f'Hello {name},' if name else 'Hello,'
    return ('Your Report Manager account',
            f'{hello}\n\nAn account has been made for you on Report Manager. Use this link to choose your password (it works for {minutes} minutes, '
            f'and once):\n\n{link}\n\nIf the link has expired, ask the person who made your account for a new one, or use "Forgot password" on the sign-in page.')


def how_it_was_sent(email_backend, failed=False):
    """Honestly: `sent` (a real email backend), `console` (the default, which only prints to the server's console, so nothing was emailed) or `failed`."""
    if failed:
        return 'failed'
    return 'console' if str(email_backend).endswith('console.EmailBackend') else 'sent'


def group_payload(pk, name, all_clients, client_ids, members, company):
    """One client group as the API gives it. `members` are usernames; `company` is {id, name} or None."""
    return {'id': pk, 'name': name, 'all_clients': bool(all_clients), 'clients': sorted(client_ids), 'members': sorted(members, key=str.casefold), 'company': company}


def person_payload(username, name, email, *, is_staff, is_owner, is_active, company, clients, client_groups):
    """One person as the access overview gives them. `clients` are the clients given one by one, `client_groups` the IDs of their groups."""
    return {'username': username, 'name': name or username, 'email': email or '', 'is_staff': bool(is_staff), 'is_owner': bool(is_owner), 'is_active': bool(is_active),
            'company': company, 'clients': sorted(clients), 'client_groups': sorted(client_groups)}
