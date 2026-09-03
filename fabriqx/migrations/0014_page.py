from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("fabriqx", "0013_clear_shared_admin_group_permissions"),
    ]

    operations = [
        migrations.CreateModel(
            name="Page",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("title", models.CharField(max_length=180)),
                ("slug", models.SlugField(blank=True, max_length=200, unique=True)),
                ("content", models.TextField()),
                ("seo_title", models.CharField(blank=True, max_length=70)),
                ("seo_description", models.CharField(blank=True, max_length=170)),
                ("is_active", models.BooleanField(default=True)),
            ],
            options={"ordering": ("title",)},
        ),
    ]
