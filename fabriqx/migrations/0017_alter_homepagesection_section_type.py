from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("fabriqx", "0016_alter_homepagesection_editor_content"),
    ]

    operations = [
        migrations.AlterField(
            model_name="homepagesection",
            name="section_type",
            field=models.CharField(
                choices=[
                    ("categories", "Category tiles"),
                    ("featured", "Featured products"),
                    ("trending", "Trending products"),
                    ("offers", "Offers strip"),
                    ("new", "New arrivals"),
                    ("testimonials", "Testimonials"),
                    ("newsletter", "Newsletter"),
                ],
                max_length=30,
            ),
        ),
    ]
