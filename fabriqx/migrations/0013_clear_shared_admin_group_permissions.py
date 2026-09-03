from django.db import migrations


def clear_shared_admin_permissions(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    admin_group = Group.objects.filter(name="Admin").first()
    if admin_group:
        admin_group.permissions.clear()


class Migration(migrations.Migration):
    dependencies = [
        ("fabriqx", "0012_alter_homepagesection_section_type"),
    ]

    operations = [
        migrations.RunPython(clear_shared_admin_permissions, migrations.RunPython.noop),
    ]
