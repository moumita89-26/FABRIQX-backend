from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import CustomerProfile


class AdminFormDesignTests(TestCase):
    def setUp(self):
        users = get_user_model()
        self.admin = users.objects.create_superuser("form-admin", "admin@example.com", "OriginalPass123!")
        self.staff = users.objects.create_user("form-staff", "staff@example.com", "OriginalPass123!", is_staff=True)
        self.client.force_login(self.admin)
        self.url = reverse("admin:auth_user_change", args=[self.staff.pk])

    def payload(self, **changes):
        return {
            "username": self.staff.username, "email": self.staff.email,
            "is_active": "on", "is_staff": "on", "password1": "", "password2": "",
            "date_joined_0": "2026-09-01", "date_joined_1": "12:00:00",
            **changes,
        }

    def test_password_fields_replace_password_summary(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        fields = response.context["adminform"].form.fields
        self.assertNotIn("password", fields)
        for name in ("password1", "password2"):
            self.assertEqual(fields[name].widget.input_type, "password")
            self.assertFalse(fields[name].required)
            self.assertIn("border", fields[name].widget.attrs["class"])
        self.assertNotContains(response, "Raw passwords are not stored")

    def test_matching_password_is_hashed_and_saved(self):
        response = self.client.post(self.url, self.payload(password1="NewStrongPass456!", password2="NewStrongPass456!"))
        self.assertEqual(response.status_code, 302)
        self.staff.refresh_from_db()
        self.assertTrue(self.staff.check_password("NewStrongPass456!"))

    def test_blank_password_keeps_existing_hash(self):
        original = self.staff.password
        response = self.client.post(self.url, self.payload(first_name="Updated"))
        self.assertEqual(response.status_code, 302)
        self.staff.refresh_from_db()
        self.assertEqual(self.staff.password, original)
        self.assertEqual(self.staff.first_name, "Updated")

    def test_mismatch_and_missing_confirmation_are_rejected(self):
        for confirmation in ("DifferentPass456!", ""):
            with self.subTest(confirmation=confirmation):
                response = self.client.post(self.url, self.payload(password1="NewStrongPass456!", password2=confirmation))
                self.assertEqual(response.status_code, 200)
                self.assertIn("password2", response.context["adminform"].form.errors)
                self.staff.refresh_from_db()
                self.assertTrue(self.staff.check_password("OriginalPass123!"))

    def test_weak_password_is_rejected(self):
        response = self.client.post(self.url, self.payload(password1="123", password2="123"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("password1", response.context["adminform"].form.errors)

    def test_changing_own_password_keeps_admin_logged_in(self):
        response = self.client.post(
            reverse("admin:auth_user_change", args=[self.admin.pk]),
            self.payload(username=self.admin.username, email=self.admin.email,
                         password1="NewStrongPass456!", password2="NewStrongPass456!"),
        )
        self.assertEqual(response.status_code, 302)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.check_password("NewStrongPass456!"))
        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 200)

    def test_custom_customer_inputs_use_shared_widget_styling(self):
        customer = CustomerProfile.objects.create(user=self.staff)
        response = self.client.get(reverse("admin:customers_customerprofile_change", args=[customer.pk]))
        self.assertEqual(response.status_code, 200)
        fields = response.context["adminform"].form.fields
        for name in ("username", "first_name", "last_name", "email", "phone"):
            classes = fields[name].widget.attrs["class"].split()
            self.assertIn("border", classes)
            self.assertIn("rounded-default", classes)
