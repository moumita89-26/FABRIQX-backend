import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from .models import CustomerProfile, InfluencerProfile


# A valid, tiny GIF keeps these endpoint tests independent of image fixtures.
GIF_IMAGE = (
    b"GIF87a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,"
    b"\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
)
SVG_IMAGE = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><circle cx=".5" cy=".5" r=".5"/></svg>'


class InfluencerProfileEmailTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="profile-owner", email="creator@example.com", first_name="Original", password="CurrentPass123!",
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

    def test_incorrect_current_password_has_a_clear_error_message(self):
        response = self.client.patch(self.url, {
            "current_password": "WrongPass123!",
            "new_password": "NewPass456!",
            "confirm_password": "NewPass456!",
        }, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data["current_password"],
            ["Incorrect old password. Please try again."],
        )


class CustomerProfilePhotoTests(TestCase):
    def setUp(self):
        self.media_root = tempfile.mkdtemp(prefix="fabriqx-profile-api-")
        self.settings_override = override_settings(MEDIA_ROOT=self.media_root)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.addCleanup(shutil.rmtree, self.media_root, ignore_errors=True)
        self.user = get_user_model().objects.create_user(
            username="customer-owner", email="customer@example.com", password="CurrentPass123!",
        )
        self.profile = CustomerProfile.objects.create(user=self.user, phone="7000000000", email_verified=True)
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.url = "/api/v1/account/profile/"

    def upload(self, name):
        return SimpleUploadedFile(name, GIF_IMAGE, content_type="image/gif")

    def test_multipart_upload_is_returned_by_profile_and_login_serializers(self):
        response = self.client.patch(self.url, {"first_name": "Updated", "profile_image": self.upload("avatar.gif")}, format="multipart")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["profile_image"].endswith("/media/customers/profiles/avatar.gif"))
        self.assertEqual(response.data["first_name"], "Updated")

        details = self.client.get(self.url)
        self.assertEqual(details.status_code, 200, details.data)
        self.assertEqual(details.data["profile_image"], response.data["profile_image"])

        dashboard = self.client.get("/api/v1/account/dashboard/")
        self.assertEqual(dashboard.status_code, 200, dashboard.data)
        self.assertEqual(dashboard.data["customer"]["profile_image"], response.data["profile_image"])

        login = self.client.post(
            "/api/v1/auth/login/",
            {"login": "customer@example.com", "password": "CurrentPass123!"},
            format="json",
        )
        self.assertEqual(login.status_code, 200, login.data)
        self.assertEqual(login.data["user"]["profile_image"], response.data["profile_image"])

    def test_replacing_photo_removes_only_the_previous_file_after_commit(self):
        first = self.client.patch(self.url, {"profile_image": self.upload("first.gif")}, format="multipart")
        self.assertEqual(first.status_code, 200, first.data)
        old_name = self.profile.__class__.objects.get(pk=self.profile.pk).profile_image.name
        storage = self.profile.profile_image.storage
        self.assertTrue(storage.exists(old_name))

        with self.captureOnCommitCallbacks(execute=True):
            second = self.client.patch(self.url, {"profile_image": self.upload("second.gif")}, format="multipart")
        self.assertEqual(second.status_code, 200, second.data)
        self.profile.refresh_from_db()
        self.assertFalse(storage.exists(old_name))
        self.assertTrue(storage.exists(self.profile.profile_image.name))

    def test_safe_svg_photo_is_accepted(self):
        response = self.client.patch(
            self.url,
            {"profile_image": SimpleUploadedFile("avatar.svg", SVG_IMAGE, content_type="image/svg+xml")},
            format="multipart",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["profile_image"].endswith("/customers/profiles/avatar.svg"))

    def test_svg_with_script_is_rejected(self):
        response = self.client.patch(
            self.url,
            {"profile_image": SimpleUploadedFile("unsafe.svg", b"<svg><script>alert(1)</script></svg>", content_type="image/svg+xml")},
            format="multipart",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("profile_image", response.data)
