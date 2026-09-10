from decimal import Decimal

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from fabriqx.admin import CouponAdmin, ReviewAdmin
from fabriqx.models import Category, Coupon, CustomerProfile, InfluencerProfile, Product, Review, ReviewAttachment


class ProductAdminNavigationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="product-admin", email="admin@example.com", password="StrongPass123!"
        )
        self.client.force_login(self.user)

    def test_faqs_and_reviews_are_grouped_with_products(self):
        response = self.client.get(reverse("admin:index"))
        self.assertEqual(response.status_code, 200)

        product_models = next(
            app["models"] for app in response.context["app_list"] if app["app_label"] == "products"
        )
        self.assertEqual(
            [model["object_name"] for model in product_models],
            ["Product", "Category", "InventoryMovement", "InventoryReport", "ProductFAQ", "Review", "Coupon"],
        )
        platform_models = next(
            app["models"] for app in response.context["app_list"] if app["app_label"] == "fabriqx"
        )
        self.assertNotIn("ProductFAQ", [model["object_name"] for model in platform_models])
        self.assertNotIn("Review", [model["object_name"] for model in platform_models])


class CouponAffiliateAdminFormTests(TestCase):
    def test_optional_affiliate_dropdown_shows_name_and_id_and_saves_selection(self):
        user = get_user_model().objects.create_user(
            username="creator", first_name="Asha", last_name="Patel", password="StrongPass123!"
        )
        influencer = InfluencerProfile.objects.create(user=user, affiliate_id="INF-ASH-1234")
        starts_at = timezone.now() + timezone.timedelta(days=1)
        form = CouponAdmin.CouponForm(
            data={
                "code": "ASHA10",
                "description": "",
                "discount_type": Coupon.DiscountType.PERCENTAGE,
                "discount_value": "10.00",
                "minimum_order_value": "0.00",
                "maximum_discount": "",
                "starts_at": starts_at.strftime("%Y-%m-%d %H:%M:%S"),
                "expires_at": (starts_at + timezone.timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S"),
                "total_usage_limit": "",
                "per_customer_limit": "1",
                "products": [],
                "is_active": "on",
                "affiliate": influencer.pk,
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        coupon = form.save()
        self.assertEqual(list(coupon.influencers.all()), [influencer])
        self.assertIn("Asha Patel (INF-ASH-1234)", [label for _, label in form.fields["affiliate"].choices])


class ReviewAttachmentAdminTests(TestCase):
    def test_review_supports_multiple_attachments_and_list_preview(self):
        user = get_user_model().objects.create_user(username="buyer", password="StrongPass123!")
        customer = CustomerProfile.objects.create(user=user)
        category = Category.objects.create(name="Reviews")
        product = Product.objects.create(category=category, name="Silk Scarf", regular_price=Decimal("100.00"))
        review = Review.objects.create(product=product, customer=customer, rating=5, title="Lovely", body="Beautiful.")
        ReviewAttachment.objects.create(review=review, image="reviews/first.jpg")
        ReviewAttachment.objects.create(review=review, image="reviews/second.jpg")

        self.assertEqual(review.attachments.count(), 2)
        admin_instance = ReviewAdmin(Review, admin.site)
        preview = str(admin_instance.attachment_preview(review))
        self.assertIn("reviews/first.jpg", preview)
        self.assertIn("reviews/second.jpg", preview)
