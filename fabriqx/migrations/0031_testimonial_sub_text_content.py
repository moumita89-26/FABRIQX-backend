from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("fabriqx", "0030_alter_banner_image_alter_brandlogo_logo"),
    ]

    operations = [
        migrations.RenameField(
            model_name="testimonial",
            old_name="quote",
            new_name="content",
        ),
        migrations.AddField(
            model_name="testimonial",
            name="sub_text",
            field=models.CharField(
                blank=True,
                help_text="Optional short text shown below the customer name.",
                max_length=200,
            ),
        ),
        migrations.AlterModelOptions(
            name="testimonial",
            options={"ordering": ("-created_at",)},
        ),
    ]
