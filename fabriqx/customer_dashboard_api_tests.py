from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .models import Category, Coupon, CustomerProfile, Order, OrderItem, Product, ProductVariant, Review


class CustomerDashboardApiTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user("customer", "customer@example.com", "CurrentPass123!", first_name="Priya", last_name="Sharma")
        self.customer = CustomerProfile.objects.create(user=user, phone="+91 98765 43210", email_verified=True)
        category = Category.objects.create(name="Suits")
        product = Product.objects.create(category=category, name="Blue Suit", regular_price=Decimal("2499.00"))
        variant = ProductVariant.objects.create(product=product, sku="SUIT-001", stock_quantity=5)
        self.order = Order.objects.create(customer=self.customer, email=user.email, phone=self.customer.phone, status=Order.Status.DELIVERED, subtotal=Decimal("2499.00"), grand_total=Decimal("2499.00"))
        self.item = OrderItem.objects.create(order=self.order, variant=variant, product_name=product.name, sku=variant.sku, unit_price=Decimal("2499.00"), quantity=1, total=Decimal("2499.00"))
        self.review = Review.objects.create(product=product, customer=self.customer, order_item=self.item, rating=5, title="Lovely", body="Beautiful suit")
        Coupon.objects.create(code="WELCOME10", description="Welcome discount", discount_type=Coupon.DiscountType.PERCENTAGE, discount_value=10, starts_at=timezone.now() - timedelta(days=1), expires_at=timezone.now() + timedelta(days=1))
        self.client = APIClient()
        self.client.force_authenticate(user)

    def test_dashboard_and_coupon_endpoints_are_customer_scoped(self):
        response = self.client.get("/api/v1/account/dashboard/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["customer"]["name"], "Priya Sharma")
        self.assertEqual(response.data["summary"]["orders_count"], 1)
        self.assertEqual(response.data["recent_orders"][0]["status_display"], "Delivered")

        response = self.client.get("/api/v1/account/coupons/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["results"][0]["code"], "WELCOME10")
        self.assertTrue(response.data["results"][0]["is_applicable"])

    def test_customer_can_manage_only_own_reviews(self):
        response = self.client.patch(f"/api/v1/account/reviews/{self.review.pk}/", {"rating": 4, "comment": "Still lovely"}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["comment"], "Still lovely")

        other_user = get_user_model().objects.create_user("other", "other@example.com", "CurrentPass123!")
        other_customer = CustomerProfile.objects.create(user=other_user)
        self.client.force_authenticate(other_user)
        self.assertEqual(self.client.get(f"/api/v1/account/reviews/{self.review.pk}/").status_code, 404)
        self.assertEqual(self.client.delete(f"/api/v1/account/reviews/{self.review.pk}/").status_code, 404)
