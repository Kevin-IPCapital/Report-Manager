import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('api', '0003_clients_scoring'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(
            name='Report',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=120)),
                ('shared', models.BooleanField(default=False)),
                ('config', models.JSONField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('owner', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='reports', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['name']},
        ),
        migrations.AddConstraint(
            model_name='report',
            constraint=models.UniqueConstraint(fields=('owner', 'name'), name='unique_report_name_per_owner'),
        ),
    ]
