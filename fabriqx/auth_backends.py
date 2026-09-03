from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend


class UsernameOrEmailBackend(ModelBackend):
    """Authenticate a user with either their username or email address."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        user_model = get_user_model()
        login = username or kwargs.get(user_model.USERNAME_FIELD)
        if login is None or password is None:
            return None

        try:
            user = user_model._default_manager.get(**{user_model.USERNAME_FIELD: login})
        except user_model.DoesNotExist:
            email_matches = user_model._default_manager.filter(email__iexact=login)
            user = email_matches.first() if email_matches.count() == 1 else None

        if user is None:
            # Keep password-hashing work comparable to a failed login for an
            # existing account, reducing username/email timing differences.
            user_model().set_password(password)
            return None

        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
