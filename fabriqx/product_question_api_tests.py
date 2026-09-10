from django.test import TestCase
from rest_framework.test import APIClient

from .models import Category, Product, ProductFAQ


class ProductFAQApiTests(TestCase):
    def setUp(self):
        category = Category.objects.create(name="FAQ category")
        self.product = Product.objects.create(
            category=category,
            name="FAQ product",
            regular_price=100,
            status=Product.Status.ACTIVE,
        )
        self.client = APIClient()

    def test_public_list_only_returns_active_faqs(self):
        active = ProductFAQ.objects.create(
            product=self.product, question="Does it fit true to size?", answer="Yes.",
        )
        ProductFAQ.objects.create(
            product=self.product, question="Inactive FAQ", answer="No.", is_active=False,
        )

        response = self.client.get(f"/api/v1/products/{self.product.slug}/faqs/")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([item["id"] for item in response.data], [active.id])
        self.assertEqual(response.data[0]["answer"], "Yes.")

        detail_response = self.client.get(f"/api/v1/products/{self.product.slug}/")
        self.assertEqual(detail_response.status_code, 200, detail_response.data)
        self.assertEqual([item["id"] for item in detail_response.data["faqs"]], [active.id])

    def test_faq_list_is_read_only(self):
        response = self.client.post(f"/api/v1/products/{self.product.slug}/faqs/", {"question": "Question"}, format="json")

        self.assertEqual(response.status_code, 405)
