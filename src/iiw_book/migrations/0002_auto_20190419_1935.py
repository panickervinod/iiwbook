# Generated by Django 2.2 on 2019-04-19 19:35

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('iiw_book', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='attendee',
            name='full_name',
            field=models.TextField(blank=True, default=None, null=True),
        ),
    ]
