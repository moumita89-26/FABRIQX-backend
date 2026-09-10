from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from .models import Category, CustomerProfile, Order, OrderItem, Product, ProductVariant, Review


class ReviewApiTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("reviewer", "reviewer@example.com", "CurrentPass123!")
        self.customer = CustomerProfile.objects.create(user=self.user, email_verified=True)
        category = Category.objects.create(name="Shirts")
        self.product = Product.objects.create(category=category, name="Linen Shirt", regular_price=Decimal("1499.00"))
        variant = ProductVariant.objects.create(product=self.product, sku="SHIRT-001", stock_quantity=3)
        order = Order.objects.create(
            customer=self.customer, email=self.user.email, status=Order.Status.DELIVERED,
            subtotal=Decimal("1499.00"), grand_total=Decimal("1499.00"),
        )
        self.order_item = OrderItem.objects.create(
            order=order, variant=variant, product_name=self.product.name, sku=variant.sku,
            unit_price=Decimal("1499.00"), quantity=1, total=Decimal("1499.00"),
        )
        self.client = APIClient()

    def test_anyone_can_list_only_approved_reviews_for_a_product(self):
        approved = Review.objects.create(
            product=self.product, customer=self.customer, order_item=self.order_item,
            rating=5, title="Excellent", body="Very comfortable", status=Review.Status.APPROVED,
        )
        other_user = get_user_model().objects.create_user("pending-reviewer", "pending@example.com", "CurrentPass123!")
        other_customer = CustomerProfile.objects.create(user=other_user)
        Review.objects.create(product=Product.objects.create(category=self.product.category, name="Other Shirt", regular_price=100), customer=other_customer, rating=3, title="Pending", body="Awaiting moderation")

        response = self.client.get(f"/api/v1/reviews/?product={self.product.pk}")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["id"], approved.id)
        self.assertEqual(response.data["results"][0]["product_id"], self.product.id)

    def test_authenticated_customer_can_submit_one_review_without_a_purchase(self):
        self.client.force_authenticate(self.user)

        response = self.client.post("/api/v1/reviews/", {
            "product": self.product.pk,
            "rating": 4,
            "title": "Great shirt",
            "body": "Good fabric and fit.",
        }, format="json")

        self.assertEqual(response.status_code, 201, response.data)
        review = Review.objects.get()
        self.assertEqual(review.status, Review.Status.PENDING)
        self.assertEqual(review.customer.user_id, self.user.id)
        self.assertFalse(review.is_verified_purchase)
        self.assertIsNone(review.order_item)

        duplicate = self.client.post("/api/v1/reviews/", {
            "product": self.product.pk,
            "rating": 5,
            "title": "Again",
            "body": "A duplicate.",
        }, format="json")
        self.assertEqual(duplicate.status_code, 400)

    def test_review_list_includes_reviewer_identity_and_photo(self):
        self.customer.profile_image = "customers/profiles/reviewer.jpg"
        self.customer.save(update_fields=("profile_image",))
        review = Review.objects.create(
            product=self.product, customer=self.customer, rating=5, title="Excellent",
            body="Very comfortable", status=Review.Status.APPROVED,
        )

        response = self.client.get(f"/api/v1/reviews/?product={self.product.pk}")

        self.assertEqual(response.status_code, 200, response.data)
        result = response.data["results"][0]
        self.assertEqual(result["id"], review.id)
        self.assertEqual(result["user_id"], self.user.id)
        self.assertEqual(result["customer_name"], self.user.username)
        self.assertIn("customers/profiles/reviewer.jpg", result["customer_photo"])

    def test_anonymous_user_cannot_submit_a_review(self):
        response = self.client.post("/api/v1/reviews/", {
            "product": self.product.pk,
            "rating": 4,
            "body": "Good fabric and fit.",
        }, format="json")

        self.assertEqual(response.status_code, 401)
