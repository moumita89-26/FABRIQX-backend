from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from .models import InfluencerProfile


class InfluencerProfileEmailTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="profile-owner", email="creator@example.com", first_name="Original",
        )
        self.profile = InfluencerProfile.objects.create(user=self.user, phone="7000000000")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.url = "/api/v1/influencer/profile/"

    def test_unchanged_email_with_existing_duplicate_allows_profile_edit(self):
        other = get_user_model().objects.create_user(username="legacy-account", email=self.user.email)
        response = self.client.patch(self.url, {
            "first_name": "Updated", "last_name": "Creator",
            "email": self.user.email, "phone": "7111111111",
        }, format="multipart")
        self.assertEqual(response.status_code, 200, response.data)
        self.user.refresh_from_db()
        self.profile.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(self.user.first_name, "Updated")
        self.assertEqual(self.profile.phone, "7111111111")
        self.assertEqual(other.first_name, "")

    def test_case_only_email_edit_with_existing_duplicate_is_allowed(self):
        get_user_model().objects.create_user(username="legacy-account", email=self.user.email)
        response = self.client.patch(self.url, {"email": "CREATOR@example.com"}, format="multipart")
        self.assertEqual(response.status_code, 200, response.data)

    def test_changing_to_another_accounts_email_is_rejected_without_saving(self):
        get_user_model().objects.create_user(username="other-account", email="taken@example.com")
        response = self.client.patch(self.url, {
            "email": "TAKEN@example.com", "first_name": "Rejected", "phone": "7222222222",
        }, format="multipart")
        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.profile.refresh_from_db()
        self.assertEqual(self.user.email, "creator@example.com")
        self.assertEqual(self.user.first_name, "Original")
        self.assertEqual(self.profile.phone, "7000000000")

    def test_changing_to_unused_email_succeeds(self):
        response = self.client.patch(self.url, {"email": "new@example.com"}, format="multipart")
        self.assertEqual(response.status_code, 200, response.data)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "new@example.com")
