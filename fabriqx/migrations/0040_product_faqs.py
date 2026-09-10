from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("fabriqx", "0039_customer_profile_image")]

    operations = [
        migrations.RenameModel(old_name="ProductQuestion", new_name="ProductFAQ"),
        migrations.RenameField(model_name="productfaq", old_name="is_published", new_name="is_active"),
        migrations.AlterField(
            model_name="productfaq",
            name="is_active",
            field=models.BooleanField(default=True),
        ),
        migrations.RemoveField(model_name="productfaq", name="customer"),
        migrations.RemoveField(model_name="productfaq", name="answered_at"),
        migrations.AddField(
            model_name="productfaq",
            name="display_order",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AlterField(
            model_name="productfaq",
            name="product",
            field=models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="faqs", to="fabriqx.product"),
        ),
        migrations.AlterModelOptions(
            name="productfaq",
            options={"ordering": ("display_order", "id"), "verbose_name": "Product FAQ", "verbose_name_plural": "Product FAQs"},
        ),
    ]
