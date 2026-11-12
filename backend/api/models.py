from django.conf import settings
from django.db import models


class RefreshToken(models.Model):
    """One row per refresh token ever issued. Only the SHA-256 hash is stored."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    family = models.UUIDField(db_index=True)          # one family per login session
    token_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    family_expires_at = models.DateTimeField()        # absolute session cap
    rotated_at = models.DateTimeField(null=True, blank=True)
    revoked = models.BooleanField(default=False)


class Client(models.Model):
    """The master client. Source customers (Autotask company, N-central org unit, ...) are linked to it
    through ClientSourceMap; other spellings of its name go in ClientAlias."""
    name = models.CharField(max_length=200, unique=True)
    contact_email = models.EmailField(blank=True)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    # The company this client belongs to. Empty means unclaimed: only administrators see it, until one claims it.
    company = models.ForeignKey('Company', null=True, blank=True, on_delete=models.SET_NULL, related_name='clients')


class ClientSourceMap(models.Model):
    """Links one customer in one source system to a client, by that system's own stable ID (never by name)."""
    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name='sources')
    source = models.CharField(max_length=20)        # autotask | ncentral | sentinelone | cove
    external_id = models.CharField(max_length=100)  # Autotask company_id, N-central org_unit_id, SentinelOne site_id, Cove customer id

    class Meta:
        constraints = [models.UniqueConstraint(fields=['source', 'external_id'], name='unique_source_external_id')]


class ClientAlias(models.Model):
    """Another name a client is known by. `alias_norm` is the normalised form, unique across all clients."""
    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name='aliases')
    alias = models.CharField(max_length=200)
    alias_norm = models.CharField(max_length=200, unique=True)


class ScoreWeight(models.Model):
    """A patch score weight. `user` is null for the shared default set; a user's own rows override it."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE)
    key = models.CharField(max_length=200)   # a patch type ('third-party') or '<type>:<category id>'
    weight = models.FloatField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['user', 'key'], condition=models.Q(user__isnull=False), name='unique_user_weight'),
            models.UniqueConstraint(fields=['key'], condition=models.Q(user__isnull=True), name='unique_shared_weight'),
        ]


class PatchSnapshot(models.Model):
    """The patch scores as of one N-central patch run, for all devices (client null) or one client.
    Stores the counts and the shared weights used, so history can be re-weighted or audited later."""
    run_id = models.BigIntegerField()
    client = models.ForeignKey(Client, null=True, blank=True, on_delete=models.CASCADE)
    captured_at = models.DateTimeField()          # when that run finished
    created_at = models.DateTimeField(auto_now_add=True)
    source = models.CharField(max_length=20, default='live')   # live | backfill
    weights = models.JSONField(default=dict)
    components = models.JSONField(default=list)   # [{type, category_id, category, installed, missing, compliance}]
    scores = models.JSONField(default=dict)       # {'overall': 91.2, 'windows-security': 95.0, ...}

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['run_id', 'client'], condition=models.Q(client__isnull=False), name='unique_snapshot_run_client'),
            models.UniqueConstraint(fields=['run_id'], condition=models.Q(client__isnull=True), name='unique_snapshot_run_all'),
        ]


class Report(models.Model):
    """A saved report definition: its name and its blocks (see api/reports.py). Private to its owner unless shared."""
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='reports')
    name = models.CharField(max_length=120)
    shared = models.BooleanField(default=False)
    config = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        constraints = [models.UniqueConstraint(fields=['owner', 'name'], name='unique_report_name_per_owner')]


class ClientGap(models.Model):
    """A discrepancy between the source systems and the master client list, kept so it is known how long it has lasted.

    kind: `missing` (an active client has no link in a source), `stale` (a link points at a customer that no longer exists) or `unlinked`
    (a customer in a source that no client is linked to). `key` says exactly which, so the same gap is recognised from one visit to the next.
    A gap is open until `resolved_at` is set; the record of resolved ones is kept."""
    key = models.CharField(max_length=300)
    kind = models.CharField(max_length=20)
    source = models.CharField(max_length=20)
    client = models.ForeignKey(Client, null=True, blank=True, on_delete=models.CASCADE, related_name='gaps')
    external_id = models.CharField(max_length=100, blank=True)
    label = models.CharField(max_length=300)
    first_seen_at = models.DateTimeField()
    last_seen_at = models.DateTimeField()
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['key'], condition=models.Q(resolved_at__isnull=True), name='unique_open_client_gap')]


class ClientAccess(models.Model):
    """Lets a person see one client's data. Used only when client access is enforced (DJANGO_CLIENT_ACCESS_ENFORCED); staff always see
    every client, and a person with no rows then sees none."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='client_access')
    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name='access')

    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'client'], name='unique_client_access')]


class Company(models.Model):
    """Whoever a person works for. A company has clients, and is a wall: nobody, administrators included, sees another company's data
    (once client access is enforced). The platform owner (a Django superuser) sits above the companies."""
    name = models.CharField(max_length=200, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)


class CompanyMember(models.Model):
    """Which company a person works for: one, for now."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='company_membership')
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='members')


class ClientGroup(models.Model):
    """A named list of clients (or all of a company's clients). The people in it see those clients, added to any they were given one by one, when
    client access is enforced. It belongs to one company and holds only that company's clients: a company is a wall. A person can be in several."""
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='client_groups')
    name = models.CharField(max_length=80)
    all_clients = models.BooleanField(default=False)
    clients = models.ManyToManyField(Client, blank=True, related_name='client_groups')
    members = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name='client_groups')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['company', 'name'], name='unique_client_group_name')]
