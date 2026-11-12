from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('api', '0001_initial')]
    operations = [
        migrations.CreateModel(
            name='Client',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('company_id', models.IntegerField(blank=True, null=True, unique=True)),
                ('name', models.CharField(max_length=200, unique=True)),
                ('contact_email', models.EmailField(blank=True, max_length=254)),
                ('notes', models.TextField(blank=True)),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
        ),
    ]
