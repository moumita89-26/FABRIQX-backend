from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("fabriqx", "0015_homepagesection_editor_content"),
    ]

    operations = [
        migrations.AlterField(
            model_name="homepagesection",
            name="editor_content",
            field=models.TextField(
                blank=True,
                help_text="Optional formatted text displayed with this section.",
                verbose_name="Introduction / custom text",
            ),
        ),
    ]
