from django.contrib.auth import get_user_model
from django.contrib.auth.forms import PasswordResetForm


class AdminPasswordResetForm(PasswordResetForm):
    """Send admin reset links only to active staff accounts."""

    def get_users(self, email):
        user_model = get_user_model()
        email_field_name = user_model.get_email_field_name()
        users = user_model._default_manager.filter(
            **{
                f"{email_field_name}__iexact": email,
                "is_active": True,
                "is_staff": True,
            }
        )
        return (user for user in users if user.has_usable_password())
