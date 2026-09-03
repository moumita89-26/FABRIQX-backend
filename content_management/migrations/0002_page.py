from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("content_management", "0001_initial"),
        ("fabriqx", "0014_page"),
    ]

    operations = [
        migrations.CreateModel(
            name="Page",
            fields=[],
            options={"proxy": True, "indexes": [], "constraints": []},
            bases=("fabriqx.page",),
        ),
    ]
