import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('api', '0005_clientgap'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(
            name='ClientAccess',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('client', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='access', to='api.client')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='client_access', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddConstraint(
            model_name='clientaccess',
            constraint=models.UniqueConstraint(fields=('user', 'client'), name='unique_client_access'),
        ),
    ]
