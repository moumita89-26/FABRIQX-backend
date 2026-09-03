from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("fabriqx", "0016_alter_homepagesection_editor_content"),
        ("products", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="Coupon",
            fields=[],
            options={
                "verbose_name": "Manage coupon",
                "verbose_name_plural": "Manage coupons",
                "proxy": True,
                "indexes": [],
                "constraints": [],
            },
            bases=("fabriqx.coupon",),
        ),
    ]
