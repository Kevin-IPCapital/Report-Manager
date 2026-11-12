import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('api', '0007_companies'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(
            name='ClientGroup',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=80)),
                ('all_clients', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('clients', models.ManyToManyField(blank=True, related_name='client_groups', to='api.client')),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='client_groups', to='api.company')),
                ('members', models.ManyToManyField(blank=True, related_name='client_groups', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddConstraint(
            model_name='clientgroup',
            constraint=models.UniqueConstraint(fields=('company', 'name'), name='unique_client_group_name'),
        ),
    ]
