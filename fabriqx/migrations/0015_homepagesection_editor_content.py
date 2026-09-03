from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("fabriqx", "0014_page"),
    ]

    operations = [
        migrations.AddField(
            model_name="homepagesection",
            name="editor_content",
            field=models.TextField(
                blank=True,
                help_text="Rich-text content displayed in this homepage section.",
            ),
        ),
    ]
