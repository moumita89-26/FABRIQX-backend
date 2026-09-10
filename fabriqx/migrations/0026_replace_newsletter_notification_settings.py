from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("fabriqx", "0025_sitesettings_and_more"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="newslettersettings",
            name="notification_email",
        ),
        migrations.RemoveField(
            model_name="newslettersettings",
            name="notifications_enabled",
        ),
        migrations.AddField(
            model_name="newslettersettings",
            name="title",
            field=models.CharField(default="Join our newsletter", max_length=150),
        ),
        migrations.AddField(
            model_name="newslettersettings",
            name="description",
            field=models.TextField(blank=True),
        ),
    ]
