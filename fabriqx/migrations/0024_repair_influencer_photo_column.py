from django.db import migrations


def restore_profile_image_column(apps, schema_editor):
    table = "fabriqx_influencerprofile"
    with schema_editor.connection.cursor() as cursor:
        columns = {
            column.name
            for column in schema_editor.connection.introspection.get_table_description(cursor, table)
        }
        if "photo" in columns and "profile_image" not in columns:
            quoted_table = schema_editor.quote_name(table)
            quoted_photo = schema_editor.quote_name("photo")
            quoted_profile_image = schema_editor.quote_name("profile_image")
            schema_editor.execute(
                f"ALTER TABLE {quoted_table} RENAME COLUMN {quoted_photo} TO {quoted_profile_image}"
            )


class Migration(migrations.Migration):
    dependencies = [
        ("fabriqx", "0023_newslettersettings_remove_testimonial_display_order"),
    ]

    operations = [
        migrations.RunPython(restore_profile_image_column, migrations.RunPython.noop),
    ]
