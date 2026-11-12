import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('api', '0004_report'),
    ]
    operations = [
        migrations.CreateModel(
            name='ClientGap',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('key', models.CharField(max_length=300)),
                ('kind', models.CharField(max_length=20)),
                ('source', models.CharField(max_length=20)),
                ('external_id', models.CharField(blank=True, max_length=100)),
                ('label', models.CharField(max_length=300)),
                ('first_seen_at', models.DateTimeField()),
                ('last_seen_at', models.DateTimeField()),
                ('resolved_at', models.DateTimeField(blank=True, null=True)),
                ('client', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='gaps', to='api.client')),
            ],
        ),
        migrations.AddConstraint(
            model_name='clientgap',
            constraint=models.UniqueConstraint(condition=models.Q(resolved_at__isnull=True), fields=('key',), name='unique_open_client_gap'),
        ),
    ]
