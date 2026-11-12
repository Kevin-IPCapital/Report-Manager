import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def assign_default_company(apps, schema_editor):
    """Make the first company and put every existing person and client in it, so the day companies arrive nothing changes for anyone."""
    Company, CompanyMember, Client = apps.get_model('api', 'Company'), apps.get_model('api', 'CompanyMember'), apps.get_model('api', 'Client')
    User = apps.get_model(*settings.AUTH_USER_MODEL.split('.'))
    company, _ = Company.objects.get_or_create(name=settings.DEFAULT_COMPANY_NAME)
    for user_id in User.objects.exclude(company_membership__isnull=False).values_list('pk', flat=True):
        CompanyMember.objects.create(user_id=user_id, company=company)
    Client.objects.filter(company__isnull=True).update(company=company)


class Migration(migrations.Migration):
    dependencies = [
        ('api', '0006_clientaccess'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(
            name='Company',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=200, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
        ),
        migrations.CreateModel(
            name='CompanyMember',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='members', to='api.company')),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='company_membership', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddField(
            model_name='client',
            name='company',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='clients', to='api.company'),
        ),
        migrations.RunPython(assign_default_company, migrations.RunPython.noop),
    ]
