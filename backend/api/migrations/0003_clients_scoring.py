import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def move_company_ids(apps, schema_editor):
    """Each client's old company_id was an Autotask company ID: keep it as an Autotask source link."""
    Client = apps.get_model('api', 'Client')
    ClientSourceMap = apps.get_model('api', 'ClientSourceMap')
    for client in Client.objects.exclude(company_id__isnull=True):
        ClientSourceMap.objects.create(client=client, source='autotask', external_id=str(client.company_id))


class Migration(migrations.Migration):
    dependencies = [
        ('api', '0002_client'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(
            name='ClientSourceMap',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('source', models.CharField(max_length=20)),
                ('external_id', models.CharField(max_length=100)),
                ('client', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='sources', to='api.client')),
            ],
        ),
        migrations.AddConstraint(
            model_name='clientsourcemap',
            constraint=models.UniqueConstraint(fields=('source', 'external_id'), name='unique_source_external_id'),
        ),
        migrations.CreateModel(
            name='ClientAlias',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('alias', models.CharField(max_length=200)),
                ('alias_norm', models.CharField(max_length=200, unique=True)),
                ('client', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='aliases', to='api.client')),
            ],
        ),
        migrations.CreateModel(
            name='ScoreWeight',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('key', models.CharField(max_length=200)),
                ('weight', models.FloatField()),
                ('user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddConstraint(
            model_name='scoreweight',
            constraint=models.UniqueConstraint(condition=models.Q(user__isnull=False), fields=('user', 'key'), name='unique_user_weight'),
        ),
        migrations.AddConstraint(
            model_name='scoreweight',
            constraint=models.UniqueConstraint(condition=models.Q(user__isnull=True), fields=('key',), name='unique_shared_weight'),
        ),
        migrations.CreateModel(
            name='PatchSnapshot',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('run_id', models.BigIntegerField()),
                ('captured_at', models.DateTimeField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('source', models.CharField(default='live', max_length=20)),
                ('weights', models.JSONField(default=dict)),
                ('components', models.JSONField(default=list)),
                ('scores', models.JSONField(default=dict)),
                ('client', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to='api.client')),
            ],
        ),
        migrations.AddConstraint(
            model_name='patchsnapshot',
            constraint=models.UniqueConstraint(condition=models.Q(client__isnull=False), fields=('run_id', 'client'), name='unique_snapshot_run_client'),
        ),
        migrations.AddConstraint(
            model_name='patchsnapshot',
            constraint=models.UniqueConstraint(condition=models.Q(client__isnull=True), fields=('run_id',), name='unique_snapshot_run_all'),
        ),
        migrations.RunPython(move_company_ids, migrations.RunPython.noop),
        migrations.RemoveField(model_name='client', name='company_id'),
    ]
