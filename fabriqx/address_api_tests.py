from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from .models import Address, CustomerProfile


class AddressApiTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("address-user", "address@example.com", "CurrentPass123!")
        self.customer = CustomerProfile.objects.create(user=self.user)
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_addresses_can_be_filtered_by_type_and_default_is_unique_per_type(self):
        billing = Address.objects.create(
            customer=self.customer, address_type=Address.Type.BILLING, full_name="Buyer", phone="9999999999",
            line1="1 Billing Road", city="Delhi", state="Delhi", postal_code="110001", is_default=True,
        )
        response = self.client.post("/api/v1/account/addresses/", {
            "address_type": "shipping", "full_name": "Buyer", "phone": "9999999999",
            "line1": "2 Shipping Road", "city": "Mumbai", "state": "Maharashtra",
            "postal_code": "400001", "country": "India", "is_default": True,
        }, format="json")

        self.assertEqual(response.status_code, 201, response.data)
        shipping = Address.objects.get(pk=response.data["id"])
        self.assertTrue(shipping.is_default)
        self.assertTrue(billing.is_default)

        response = self.client.get("/api/v1/account/addresses/?address_type=shipping")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([item["id"] for item in response.data["results"]], [shipping.id])
