from decimal import Decimal
from datetime import datetime, timezone as datetime_timezone
import hashlib
import hmac
import json
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from django.conf import settings
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import ValidationError
from django.test import Client, TestCase, override_settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.files.base import ContentFile
from django.core import mail
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from .models import Banner, BrandLogo, BrandLogoSection, Category, Coupon, CustomerProfile, FooterSocialLink, FooterSocialSection, GiftSection, GiftSectionFeature, GiftSectionStatistic, InfluencerCommission, InfluencerProfile, InventoryMovement, Invoice, NewsletterSettings, NewsletterSubscription, OfferBanner, OfferGridItem, OfferGridSection, Order, OrderItem, Page, Payment, Product, ProductVariant, SiteSettings, Testimonial, UserRole


class ModelTests(TestCase):
    def test_banner_and_logo_image_formats(self):
        from io import BytesIO
        from PIL import Image
        for model, field_name in ((Banner, "image"), (BrandLogo, "logo")):
            field = model._meta.get_field(field_name)
            for extension, image_format in (("jpg", "JPEG"), ("jpeg", "JPEG"), ("PNG", "PNG"), ("gif", "GIF"), ("webp", "WEBP"), ("bmp", "BMP"), ("jpg", "BMP")):
                with self.subTest(model=model.__name__, extension=extension, image_format=image_format):
                    stream = BytesIO()
                    Image.new("RGB", (2, 2)).save(stream, format=image_format)
                    upload = SimpleUploadedFile(f"image.{extension}", stream.getvalue())
                    if image_format in {"JPEG", "PNG", "GIF"}:
                        field.clean(upload, model())
                    else:
                        with self.assertRaises(ValidationError):
                            field.clean(upload, model())


    def setUp(self):
        self.user = get_user_model().objects.create_user("customer", "customer@example.com", "pass")
        self.customer = CustomerProfile.objects.create(user=self.user)
        self.category = Category.objects.create(name="Dresses")
        self.product = Product.objects.create(category=self.category, name="Celebration Dress", description="Test", regular_price=Decimal("2000.00"), sale_price=Decimal("1500.00"))
        self.variant = ProductVariant.objects.create(product=self.product, sku="DRESS-RED-M", size="M", color="Red", stock_quantity=4)

    def test_product_slug_stock_and_price(self):
        self.assertEqual(self.product.slug, "celebration-dress")
        self.assertEqual(self.product.total_stock, 4)
        self.assertEqual(self.variant.effective_price, Decimal("1500.00"))
        self.assertEqual(self.variant.stock_status, "Low stock")

    def test_application_timestamps_use_configured_local_timezone(self):
        utc_value = datetime(2026, 9, 4, 5, 30, tzinfo=datetime_timezone.utc)

        self.assertEqual(settings.TIME_ZONE, "Asia/Kolkata")
        self.assertEqual(timezone.localtime(utc_value).isoformat(), "2026-09-04T11:00:00+05:30")

    def test_new_banner_rejects_start_date_before_today(self):
        banner = Banner(
            title="Past campaign",
            image="banners/past.jpg",
            starts_at=timezone.now() - timezone.timedelta(days=1),
        )

        with self.assertRaisesMessage(ValidationError, "Start date cannot be earlier than today."):
            banner.full_clean()

    def test_banner_rejects_end_date_before_start_date(self):
        starts_at = timezone.now() + timezone.timedelta(days=1)
        banner = Banner(
            title="Invalid campaign range",
            image="banners/invalid-range.jpg",
            starts_at=starts_at,
            ends_at=starts_at - timezone.timedelta(hours=1),
        )

        with self.assertRaisesMessage(ValidationError, "End date cannot be earlier than the start date."):
            banner.full_clean()

    def test_new_gift_section_rejects_start_date_before_today(self):
        gift = GiftSection(
            internal_name="Past gift campaign",
            heading="Past gift",
            main_image="gift-sections/main/past.jpg",
            starts_at=timezone.now() - timezone.timedelta(days=1),
        )

        with self.assertRaisesMessage(ValidationError, "Start date cannot be earlier than today."):
            gift.full_clean()

    def test_new_offer_banner_rejects_start_date_before_today(self):
        offer = OfferBanner(
            internal_name="Past offer",
            desktop_image="offers/banners/desktop/past.jpg",
            shop_now_url="/offers/",
            starts_at=timezone.now() - timezone.timedelta(days=1),
        )

        with self.assertRaisesMessage(ValidationError, "Start date cannot be earlier than today."):
            offer.full_clean()

    def test_testimonial_rejects_numeric_customer_name(self):
        testimonial = Testimonial(customer_name="123.45", quote="Excellent", rating=5)

        with self.assertRaisesMessage(ValidationError, "Customer name cannot contain only a numeric value."):
            testimonial.full_clean()

    def test_testimonial_rating_uses_consistent_minimum_validation(self):
        for invalid_rating in (-1, 0):
            with self.subTest(rating=invalid_rating):
                testimonial = Testimonial(customer_name="Customer", quote="Excellent", rating=invalid_rating)
                with self.assertRaisesMessage(ValidationError, "Ensure this value is greater than or equal to 1."):
                    testimonial.full_clean()

    def test_cors_preflight_allows_configured_frontend_only(self):
        allowed = self.client.options(
            "/api/v1/auth/login/",
            HTTP_ORIGIN="http://localhost:5173",
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
            HTTP_ACCESS_CONTROL_REQUEST_HEADERS="content-type",
        )
        self.assertEqual(allowed.status_code, 204)
        self.assertEqual(allowed["Access-Control-Allow-Origin"], "http://localhost:5173")
        self.assertIn("POST", allowed["Access-Control-Allow-Methods"])

        rejected = self.client.options(
            "/api/v1/auth/login/",
            HTTP_ORIGIN="https://untrusted.example",
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
        )
        self.assertNotIn("Access-Control-Allow-Origin", rejected)

    def test_sale_price_validation(self):
        self.product.sale_price = Decimal("2500.00")
        with self.assertRaises(ValidationError):
            self.product.full_clean()

    def test_coupon_validation_and_normalization(self):
        coupon = Coupon(code=" save20 ", discount_type=Coupon.DiscountType.PERCENTAGE, discount_value=20, starts_at=timezone.now(), expires_at=timezone.now() + timezone.timedelta(days=1))
        coupon.full_clean()
        coupon.save()
        self.assertEqual(coupon.code, "SAVE20")

    def test_new_coupon_rejects_start_date_before_today(self):
        coupon = Coupon(
            code="PAST10",
            discount_type=Coupon.DiscountType.PERCENTAGE,
            discount_value=10,
            starts_at=timezone.now() - timezone.timedelta(days=1),
            expires_at=timezone.now() + timezone.timedelta(days=1),
        )

        with self.assertRaisesMessage(ValidationError, "Start date cannot be earlier than today."):
            coupon.full_clean()

    def test_order_invoice_and_commission_references(self):
        influencer_user = get_user_model().objects.create_user("influencer")
        influencer = InfluencerProfile.objects.create(user=influencer_user)
        order = Order.objects.create(customer=self.customer, email="customer@example.com", phone="1", influencer=influencer)
        invoice = Invoice.objects.create(order=order)
        commission = InfluencerCommission(influencer=influencer, order=order, rate=10, eligible_amount=Decimal("1000.00"), commission_amount=Decimal("100.00"))
        commission.full_clean()
        self.assertTrue(order.number.startswith("ORD-"))
        self.assertTrue(invoice.number.startswith("INV-"))
        self.assertTrue(influencer.affiliate_id.startswith("INF-"))
        influencer_user.fabriqx_role.refresh_from_db()
        self.assertEqual(influencer_user.fabriqx_role.role, UserRole.Role.INFLUENCER)

    def test_checkout_affiliate_code_links_order_to_active_influencer(self):
        influencer_user = get_user_model().objects.create_user("affiliate")
        influencer = InfluencerProfile.objects.create(user=influencer_user)
        order = Order.objects.create(customer=self.customer, email="customer@example.com", phone="1", affiliate_code=influencer.affiliate_id.lower())
        self.assertEqual(order.influencer, influencer)
        self.assertEqual(order.affiliate_code, influencer.affiliate_id)

        invalid_order = Order(customer=self.customer, email="customer@example.com", phone="1", affiliate_code="INVALID")
        with self.assertRaises(ValidationError):
            invalid_order.full_clean()


class AdminTests(TestCase):
    def setUp(self):
        self.admin_user = get_user_model().objects.create_superuser("admin", "admin@example.com", "pass")
        self.client = Client()
        self.client.force_login(self.admin_user)

    def test_banner_edit_with_invalid_hidden_dates_saves(self):
        media_root = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(override_settings(MEDIA_ROOT=media_root))
        starts_at = timezone.now() + timezone.timedelta(days=1)
        ends_at = starts_at - timezone.timedelta(hours=1)
        banner = Banner.objects.create(
            title="Old hero", image="banners/hero.jpg",
            starts_at=starts_at, ends_at=ends_at,
        )
        banner.image.save("hero.jpg", ContentFile(b"existing image"))
        response = self.client.post(
            reverse("admin:content_management_banner_change", args=(banner.pk,)),
            {"title": "Updated hero", "display_order": "0", "is_active": "on", "_save": "Save"},
        )
        self.assertEqual(response.status_code, 302)
        banner.refresh_from_db()
        self.assertEqual(banner.title, "Updated hero")
        self.assertEqual(banner.starts_at, starts_at)
        self.assertEqual(banner.ends_at, ends_at)

    def test_simplified_hero_form(self):
        banner = Banner.objects.create(title="Hero", image="banners/hero.jpg")
        for url in (reverse("admin:content_management_banner_add"), reverse("admin:content_management_banner_change", args=(banner.pk,))):
            response = self.client.get(url)
            fields = response.context["adminform"].form.fields
            self.assertEqual(fields["link"].label, "Shop Now Button URL")
            self.assertNotIn("starts_at", fields)
            self.assertNotIn("ends_at", fields)
            self.assertNotContains(response, "Existing banner image")
            self.assertNotContains(response, 'name="_continue"')
        add_page = self.client.get(reverse("admin:content_management_banner_add"))
        self.assertNotContains(add_page, 'class="addlink ')

    def test_staff_creation_requires_passwords_and_accepts_names(self):
        url = reverse("admin:auth_user_add")
        response = self.client.get(url)
        fields = response.context["adminform"].form.fields
        self.assertNotIn("username", fields)
        self.assertIn("first_name", fields)
        self.assertIn("last_name", fields)
        self.assertTrue(fields["password1"].required)
        self.assertTrue(fields["password2"].required)
        self.assertNotContains(response, "too similar to your other personal information")
        data = {"first_name": "Jane", "last_name": "Doe", "email": "jane@example.com", "_save": "Save"}
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 200)
        self.assertIn("password1", response.context["adminform"].form.errors)
        self.assertIn("password2", response.context["adminform"].form.errors)
        data.update(password1="UniquePass123!", password2="UniquePass123!")
        self.assertEqual(self.client.post(url, data).status_code, 302)
        user = get_user_model().objects.get(email="jane@example.com")
        self.assertEqual(user.get_full_name(), "Jane Doe")
        self.assertTrue(user.username)
        self.assertTrue(user.check_password("UniquePass123!"))

    def test_delete_confirmation_dialog_and_confirmed_delete(self):
        banner = Banner.objects.create(title="Delete me", image="banners/hero.jpg")
        url = reverse("admin:content_management_banner_delete", args=(banner.pk,))
        response = self.client.get(url)
        self.assertContains(response, "data-delete-confirmation")
        self.assertContains(response, '>Yes</button>')
        self.assertContains(response, '>No</button>')
        self.assertTrue(Banner.objects.filter(pk=banner.pk).exists())
        self.assertEqual(self.client.post(url, {"post": "yes"}).status_code, 302)
        self.assertFalse(Banner.objects.filter(pk=banner.pk).exists())

    def test_bulk_delete_confirmation_dialog(self):
        banner = Banner.objects.create(title="Delete me", image="banners/hero.jpg")
        url = reverse("admin:content_management_banner_changelist")
        data = {"action": "delete_selected", "_selected_action": [banner.pk]}
        response = self.client.post(url, data)
        self.assertContains(response, "data-delete-confirmation")
        self.assertTrue(Banner.objects.filter(pk=banner.pk).exists())
        data["post"] = "yes"
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.assertFalse(Banner.objects.filter(pk=banner.pk).exists())

    def test_brand_logo_add_action_opens_one_new_form(self):
        response = self.client.get(reverse("admin:content_management_brandlogosection_changelist"), follow=True)
        self.assertContains(response, "<span>Add Brand logo</span>", count=1)
        self.assertNotContains(response, "Add Brand logo section")
        response = self.client.get(reverse("admin:content_management_brandlogo_add"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("logo", response.context["adminform"].form.fields)
        self.assertNotIn("section", response.context["adminform"].form.fields)
        self.assertEqual(response.context["inline_admin_formsets"], [])

    def test_brand_logo_rejects_two_new_logos(self):
        from .admin import BrandLogoInline
        from content_management.models import BrandLogoSection as SectionProxy
        from django.test import RequestFactory
        section = BrandLogoSection.objects.create()
        request = RequestFactory().get("/")
        request.user = self.admin_user
        inline = BrandLogoInline(SectionProxy, admin.site)
        FormSet = inline.get_formset(request, section)
        gif = b"GIF87a\x01\x00\x01\x00\x80\x01\x00\x00\x00\x00ccc,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
        data = {"logos-TOTAL_FORMS": "2", "logos-INITIAL_FORMS": "0"}
        files = {}
        for index in range(2):
            data.update({f"logos-{index}-brand_name": f"Brand {index}", f"logos-{index}-display_order": "0"})
            files[f"logos-{index}-logo"] = SimpleUploadedFile(f"logo-{index}.gif", gif, content_type="image/gif")
        formset = FormSet(data=data, files=files, instance=section, prefix="logos")
        self.assertFalse(formset.is_valid())
        self.assertTrue(formset.non_form_errors())

    def test_brand_logo_edit_allows_one_more_logo(self):
        section = BrandLogoSection.objects.create()
        BrandLogo.objects.create(section=section, brand_name="Brand", logo="brand-logos/a.jpg")
        response = self.client.get(reverse("admin:content_management_brandlogosection_change", args=(section.pk,)))
        formset = response.context["inline_admin_formsets"][0].formset
        self.assertEqual(formset.max_num, 2)
        for field in ("alt_text", "url", "open_in_new_tab"):
            self.assertNotIn(field, formset.forms[0].fields)

    def test_core_models_registered(self):
        for model in (Product, ProductVariant, Order, Coupon, InfluencerProfile, InfluencerCommission):
            self.assertIn(model, admin.site._registry)

    def test_admin_login_uses_simple_fabriqx_brand_card(self):
        self.client.logout()
        response = self.client.get(reverse("admin:login"), HTTP_HOST="127.0.0.1")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "fabriqx-login-card")
        self.assertContains(response, "branding/fabriqx-logo.jpeg")
        self.assertContains(response, "Sign in to FABRIQX Administration")
        self.assertContains(response, "Forgot your password?")
        self.assertContains(response, reverse("admin_password_reset"))
        self.assertNotContains(response, "login_image")

    def test_admin_theme_menu_only_shows_functional_theme_options(self):
        response = self.client.get(reverse("admin:index"), HTTP_HOST="127.0.0.1")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "switchTheme('light')")
        self.assertContains(response, "switchTheme('dark')")
        self.assertNotContains(response, "switchTheme('auto')")

    def test_admin_password_reset_page_emails_active_staff_only(self):
        self.client.logout()

        page = self.client.get(reverse("admin_password_reset"), HTTP_HOST="127.0.0.1")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "fabriqx-login-card")
        self.assertContains(page, "Forgot your password?")

        response = self.client.post(
            reverse("admin_password_reset"),
            {"email": self.admin_user.email},
            HTTP_HOST="127.0.0.1",
        )
        self.assertRedirects(response, reverse("password_reset_done"), fetch_redirect_response=False)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.admin_user.email])
        self.assertIn("/admin/reset/", mail.outbox[0].body)

        reset_url = next(line for line in mail.outbox[0].body.splitlines() if "/admin/reset/" in line)
        token_response = self.client.get(urlparse(reset_url).path, HTTP_HOST="127.0.0.1")
        self.assertEqual(token_response.status_code, 302)
        reset_response = self.client.post(
            token_response.url,
            {
                "new_password1": "UpdatedAdminPass123!",
                "new_password2": "UpdatedAdminPass123!",
            },
            HTTP_HOST="127.0.0.1",
        )
        self.assertRedirects(reset_response, reverse("password_reset_complete"), fetch_redirect_response=False)
        self.admin_user.refresh_from_db()
        self.assertTrue(self.admin_user.check_password("UpdatedAdminPass123!"))

        customer = get_user_model().objects.create_user(
            "non_staff_reset",
            email="non-staff-reset@example.com",
            password="CustomerPass123!",
        )
        self.client.post(
            reverse("admin_password_reset"),
            {"email": customer.email},
            HTTP_HOST="127.0.0.1",
        )
        self.assertEqual(len(mail.outbox), 1)

    def test_admin_can_log_in_with_email_or_username(self):
        self.client.logout()

        email_login = self.client.post(
            reverse("admin:login"),
            {"username": "ADMIN@EXAMPLE.COM", "password": "pass", "next": reverse("admin:index")},
            HTTP_HOST="127.0.0.1",
        )
        self.assertRedirects(email_login, reverse("admin:index"), fetch_redirect_response=False)

        self.client.logout()
        username_login = self.client.post(
            reverse("admin:login"),
            {"username": "admin", "password": "pass", "next": reverse("admin:index")},
            HTTP_HOST="127.0.0.1",
        )
        self.assertRedirects(username_login, reverse("admin:index"), fetch_redirect_response=False)

    def test_admin_logout_uses_branded_login_card(self):
        response = self.client.post(reverse("admin:logout"), HTTP_HOST="127.0.0.1")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "fabriqx-login-shell")
        self.assertContains(response, "fabriqx-login-card")
        self.assertContains(response, "branding/fabriqx-logo.jpeg")
        self.assertContains(response, "Successfully logged out")
        self.assertContains(response, reverse("admin:login"))
        self.assertNotContains(response, "Return to site")

    def test_dashboard_renders_brand_and_cards(self):
        response = self.client.get(reverse("admin:index"), HTTP_HOST="127.0.0.1")
        self.assertEqual(response.status_code, 200)
        self.assertIn("dashboard_cards", response.context)
        self.assertEqual(response.context["dashboard_cards"][0]["title"], "Today's sales")
        self.assertContains(response, "Today&#x27;s sales")
        self.assertContains(response, "Sales — last 7 days")
        self.assertContains(response, 'data-type="line"', html=False)
        self.assertContains(response, 'data-type="bar"', html=False)
        self.assertContains(response, "FABRIQX Administration")
        app_models = {app["app_label"]: {model["object_name"] for model in app["models"]} for app in response.context["app_list"]}
        self.assertIn("Product", app_models["products"])
        self.assertIn("Coupon", app_models["products"])
        coupon_menu = next(model for app in response.context["app_list"] if app["app_label"] == "products" for model in app["models"] if model["object_name"] == "Coupon")
        self.assertEqual(coupon_menu["name"], "Manage coupons")
        product_models = next(app["models"] for app in response.context["app_list"] if app["app_label"] == "products")
        self.assertEqual(
            [model["object_name"] for model in product_models],
            ["Product", "Category", "InventoryMovement", "InventoryReport", "Coupon"],
        )
        self.assertIn("InfluencerProfile", app_models["influencers"])
        self.assertNotIn("InfluencerCommission", app_models["influencers"])
        influencer_menu = next(model for app in response.context["app_list"] if app["app_label"] == "influencers" for model in app["models"] if model["object_name"] == "InfluencerProfile")
        self.assertEqual(influencer_menu["name"], "Manage Influencers")
        influencer_models = next(app["models"] for app in response.context["app_list"] if app["app_label"] == "influencers")
        self.assertEqual([model["object_name"] for model in influencer_models], ["InfluencerProfile", "InfluencerReport"])
        self.assertEqual(
            app_models["content_management"],
            {"Banner", "BrandLogo", "FooterSocialSection", "GiftSection", "NewsletterSettings", "NewsletterSubscription", "OfferBanner", "OfferGridSection", "Page", "Testimonial"},
        )
        content_models = next(app["models"] for app in response.context["app_list"] if app["app_label"] == "content_management")
        self.assertEqual(
            [model["name"] for model in content_models],
            ["Hero banners", "Brand Logo Section", "Homepage gift sections", "Offer banners", "Offer grid", "Testimonials", "Footer social links", "Pages", "Newsletter settings", "Newsletter subscriptions"],
        )
        self.assertNotIn("HomepageSection", app_models["content_management"])
        self.assertIn("CustomerProfile", app_models["customers"])
        self.assertNotIn("Address", app_models["customers"])
        self.assertNotIn("WishlistItem", app_models["customers"])
        self.assertNotIn("orders", app_models)
        self.assertNotIn("finance", app_models)
        self.assertNotIn("marketing", app_models)
        self.assertNotIn("operations", app_models)
        self.assertNotIn("AuditLog", {name for models in app_models.values() for name in models})
        self.assertNotIn("UserRole", app_models["auth"])
        labels = [app["app_label"] for app in response.context["app_list"]]
        self.assertEqual(labels.index("influencers"), labels.index("customers") + 1)
        self.assertContains(response, 'aria-label="Open account menu"', html=False)
        self.assertContains(response, 'min-width: 19rem', html=False)
        self.assertNotContains(response, '<aside class="shrink-0 lg:w-96">', html=False)

    def test_groups_hidden_and_three_roles_available(self):
        response = self.client.get(reverse("admin:index"), HTTP_HOST="127.0.0.1")
        app_models = {app["app_label"]: {model["object_name"] for model in app["models"]} for app in response.context["app_list"]}
        self.assertNotIn("Group", app_models.get("auth", set()))
        self.assertEqual(set(UserRole.Role.values), {"admin", "customer", "influencer"})
        self.assertTrue(Group.objects.filter(name="Admin").exists())

    def test_users_list_displays_role(self):
        response = self.client.get(reverse("admin:auth_user_changelist"), HTTP_HOST="127.0.0.1")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Role")
        self.assertContains(response, "Super Admin")
        response = self.client.get(reverse("admin:auth_user_change", args=(self.admin_user.pk,)), HTTP_HOST="127.0.0.1")
        self.assertContains(response, "Role")
        self.assertContains(response, "one protected Super Admin account")
        self.assertTrue(response.context["adminform"].form.fields["is_superuser"].disabled)
        self.assertNotContains(response, 'id="id_groups"', html=False)
        self.assertNotContains(response, 'id="id_user_permissions"', html=False)

    def test_site_allows_only_one_protected_super_admin(self):
        second_user = get_user_model().objects.create_user("second_admin", email="second-admin@example.com")
        second_user.is_staff = True
        second_user.is_superuser = True

        with self.assertRaisesMessage(ValidationError, "only one Super Admin"):
            second_user.save()

        self.admin_user.is_superuser = False
        with self.assertRaisesMessage(ValidationError, "cannot be removed"):
            self.admin_user.save()

    def test_user_created_from_admin_accounts_is_always_admin(self):
        view_banner = Permission.objects.get(content_type__app_label="content_management", codename="view_banner")
        list_page = self.client.get(reverse("admin:auth_user_changelist"), HTTP_HOST="127.0.0.1")
        self.assertContains(list_page, "Add Admin / Staff")

        add_page = self.client.get(reverse("admin:auth_user_add"), HTTP_HOST="127.0.0.1")
        self.assertNotContains(add_page, "Account type")
        self.assertNotIn("role", add_page.context["adminform"].form.fields)
        self.assertContains(add_page, "Back to list")
        self.assertContains(add_page, reverse("admin:auth_user_changelist"))
        self.assertNotContains(add_page, "Password-based authentication")
        add_form = add_page.context["adminform"].form
        self.assertTrue(add_form.fields["password1"].widget.attrs["class"])
        self.assertEqual(
            add_form.fields["password1"].widget.attrs["class"],
            add_form.fields["password2"].widget.attrs["class"],
        )
        self.assertNotContains(add_page, '<option value="customer">', html=False)
        self.assertNotContains(add_page, '<option value="influencer">', html=False)

        response = self.client.post(
            reverse("admin:auth_user_add"),
            {
                "username": "new_admin",
                "email": "new-admin@example.com",
                "password1": "StrongPass123!",
                "password2": "StrongPass123!",
                "permissions": [view_banner.pk],
                "_save": "Save",
            },
            HTTP_HOST="127.0.0.1",
        )
        self.assertEqual(response.status_code, 302)
        user = get_user_model().objects.get(email="new-admin@example.com")
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_active)
        self.assertTrue(user.has_usable_password())
        self.assertTrue(user.check_password("StrongPass123!"))
        self.assertEqual(user.email, "new-admin@example.com")
        self.assertEqual(user.fabriqx_role.role, UserRole.Role.ADMIN)
        self.assertEqual(list(user.user_permissions.all()), [view_banner])

        self.client.logout()
        self.assertTrue(self.client.login(username=user.username, password="StrongPass123!"))
        self.assertEqual(
            self.client.get(reverse("admin:content_management_banner_changelist"), HTTP_HOST="127.0.0.1").status_code,
            200,
        )

    def test_admin_account_email_must_be_unique_case_insensitively(self):
        get_user_model().objects.create_user("existing_admin", email="Staff@Example.com", is_staff=True)

        response = self.client.post(
            reverse("admin:auth_user_add"),
            {
                "username": "duplicate_email_admin",
                "email": "staff@example.com",
                "password1": "StrongPass123!",
                "password2": "StrongPass123!",
                "permissions": [],
                "_save": "Save",
            },
            HTTP_HOST="127.0.0.1",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "An account with this email address already exists.")
        self.assertFalse(get_user_model().objects.filter(username="duplicate_email_admin").exists())

    def test_new_admin_account_receives_access_email(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("admin:auth_user_add"),
                {
                    "username": "emailed_admin",
                    "email": "emailed-admin@example.com",
                    "password1": "TemporaryPass123!",
                    "password2": "TemporaryPass123!",
                    "permissions": [],
                    "_save": "Save",
                },
                HTTP_HOST="127.0.0.1",
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        access_email = mail.outbox[0]
        self.assertEqual(access_email.to, ["emailed-admin@example.com"])
        self.assertIn(get_user_model().objects.get(email="emailed-admin@example.com").username, access_email.body)
        self.assertIn("TemporaryPass123!", access_email.body)
        self.assertIn(reverse("admin:login"), access_email.body)
        self.assertIn(reverse("admin:password_change"), access_email.body)
        self.assertIn("reset your password later", access_email.body)
        self.assertEqual(len(access_email.alternatives), 1)
        html_email, mime_type = access_email.alternatives[0]
        self.assertEqual(mime_type, "text/html")
        self.assertIn("Your admin account is ready", html_email)
        self.assertIn("Open admin panel", html_email)
        self.assertIn("TemporaryPass123!", html_email)

    def test_staff_only_sees_and_accesses_permitted_admin_models(self):
        staff = get_user_model().objects.create_user("limited_staff", password="pass", is_staff=True)
        view_banner = Permission.objects.get(content_type__app_label="content_management", codename="view_banner")
        staff.user_permissions.add(view_banner)
        staff.fabriqx_role.role = UserRole.Role.ADMIN
        staff.fabriqx_role.save()

        self.client.force_login(staff)
        response = self.client.get(reverse("admin:index"), HTTP_HOST="127.0.0.1")
        visible_models = {
            model["object_name"]
            for app in response.context["app_list"]
            for model in app["models"]
        }
        self.assertIn("Banner", visible_models)
        self.assertNotIn("Product", visible_models)
        self.assertNotIn("CustomerProfile", visible_models)

        self.assertEqual(
            self.client.get(reverse("admin:content_management_banner_changelist"), HTTP_HOST="127.0.0.1").status_code,
            200,
        )
        self.assertEqual(
            self.client.get(reverse("admin:products_product_changelist"), HTTP_HOST="127.0.0.1").status_code,
            403,
        )
        self.assertFalse(staff.has_perm("content_management.add_banner"))

    def test_only_superuser_can_manage_admin_accounts(self):
        staff = get_user_model().objects.create_user("ordinary_staff", password="pass", is_staff=True)
        staff.fabriqx_role.role = UserRole.Role.ADMIN
        staff.fabriqx_role.save()
        staff.user_permissions.add(Permission.objects.get(content_type__app_label="auth", codename="view_user"))
        self.client.force_login(staff)

        response = self.client.get(reverse("admin:auth_user_changelist"), HTTP_HOST="127.0.0.1")
        self.assertEqual(response.status_code, 403)

    def test_staff_accounts_can_be_deleted_but_superusers_are_protected(self):
        staff = get_user_model().objects.create_user("deletable_staff", password="pass", is_staff=True)
        staff.fabriqx_role.role = UserRole.Role.ADMIN
        staff.fabriqx_role.save()

        list_page = self.client.get(reverse("admin:auth_user_changelist"), HTTP_HOST="127.0.0.1")
        self.assertContains(list_page, reverse("admin:auth_user_delete", args=(staff.pk,)))
        self.assertContains(list_page, "Protected")

        protected_delete = self.client.get(
            reverse("admin:auth_user_delete", args=(self.admin_user.pk,)),
            HTTP_HOST="127.0.0.1",
        )
        self.assertEqual(protected_delete.status_code, 403)
        self.assertTrue(get_user_model().objects.filter(pk=self.admin_user.pk).exists())

        staff_delete = self.client.post(
            reverse("admin:auth_user_delete", args=(staff.pk,)),
            {"post": "yes"},
            HTTP_HOST="127.0.0.1",
        )
        self.assertEqual(staff_delete.status_code, 302)
        self.assertFalse(get_user_model().objects.filter(pk=staff.pk).exists())

    def test_admin_accounts_list_shows_row_edit_buttons(self):
        staff = get_user_model().objects.create_user("editable_staff", password="pass", is_staff=True)
        staff.fabriqx_role.role = UserRole.Role.ADMIN
        staff.fabriqx_role.save()

        list_page = self.client.get(reverse("admin:auth_user_changelist"), HTTP_HOST="127.0.0.1")

        self.assertEqual(list_page.status_code, 200)
        self.assertContains(list_page, "Edit")
        self.assertContains(list_page, reverse("admin:auth_user_change", args=(staff.pk,)))
        self.assertContains(list_page, reverse("admin:auth_user_change", args=(self.admin_user.pk,)))

    def test_customer_list_allows_edit_without_user_dropdown(self):
        customer = CustomerProfile.objects.create(
            user=get_user_model().objects.create_user("editable_customer", email="customer@example.com", first_name="Customer"),
            phone="1234567890",
        )
        response = self.client.get(
            reverse("admin:customers_customerprofile_changelist"),
            HTTP_HOST="127.0.0.1",
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(
            response,
            reverse("admin:customers_customerprofile_delete", args=(customer.pk,)),
        )
        self.assertNotContains(response, ">Delete</a>", html=False)
        self.assertContains(response, ">Edit</a>", html=False)
        self.assertContains(response, reverse("admin:customers_customerprofile_change", args=(customer.pk,)))

        change_page = self.client.get(
            reverse("admin:customers_customerprofile_change", args=(customer.pk,)),
            HTTP_HOST="127.0.0.1",
        )
        self.assertEqual(change_page.status_code, 200)
        self.assertNotContains(change_page, 'name="user"', html=False)
        self.assertTrue(change_page.context["adminform"].form.fields["username"].disabled)

    def test_customer_cannot_be_updated_with_empty_mandatory_fields(self):
        user = get_user_model().objects.create_user(
            "required_customer",
            email="required@example.com",
            first_name="Required",
        )
        customer = CustomerProfile.objects.create(user=user, phone="1234567890")

        response = self.client.post(
            reverse("admin:customers_customerprofile_change", args=(customer.pk,)),
            {
                "username": user.username,
                "first_name": "",
                "last_name": "",
                "email": "",
                "phone": "",
                "addresses-TOTAL_FORMS": "0",
                "addresses-INITIAL_FORMS": "0",
                "addresses-MIN_NUM_FORMS": "0",
                "addresses-MAX_NUM_FORMS": "1000",
                "_save": "Save",
            },
            HTTP_HOST="127.0.0.1",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This field is required.", count=3)
        user.refresh_from_db()
        customer.refresh_from_db()
        self.assertEqual((user.first_name, user.email, customer.phone), ("Required", "required@example.com", "1234567890"))

    def test_customer_accepts_a_non_existing_email_address(self):
        user = get_user_model().objects.create_user(
            "email_customer",
            email="old-customer@example.com",
            first_name="Email",
        )
        customer = CustomerProfile.objects.create(user=user, phone="1234567890")

        response = self.client.post(
            reverse("admin:customers_customerprofile_change", args=(customer.pk,)),
            {
                "username": user.username,
                "first_name": "Email",
                "last_name": "Customer",
                "email": "  New-Customer@Example.com ",
                "phone": "1234567890",
                "addresses-TOTAL_FORMS": "0",
                "addresses-INITIAL_FORMS": "0",
                "addresses-MIN_NUM_FORMS": "0",
                "addresses-MAX_NUM_FORMS": "1000",
                "_save": "Save",
            },
            HTTP_HOST="127.0.0.1",
        )

        self.assertEqual(response.status_code, 302)
        user.refresh_from_db()
        self.assertEqual(user.email, "new-customer@example.com")

    def test_category_list_shows_row_edit_buttons(self):
        category = Category.objects.create(name="Editable category", display_order=7)

        response = self.client.get(
            reverse("admin:products_category_changelist"),
            HTTP_HOST="127.0.0.1",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("admin:products_category_change", args=(category.pk,)))
        self.assertContains(response, ">Edit</a>", html=False)
        self.assertContains(response, ">7</td>", html=False)
        self.assertNotContains(response, 'name="form-0-display_order"', html=False)
        self.assertContains(response, "category-drag-handle")
        self.assertContains(response, "fabriqx/admin/category_sort.js")

    def test_category_admin_regenerates_slug_and_allows_deletion(self):
        add_response = self.client.post(
            reverse("admin:products_category_add"),
            {
                "name": "New Summer Party Dresses",
                "slug": "stale-test-category",
                "is_active": "on",
                "_save": "Save",
            },
            HTTP_HOST="127.0.0.1",
        )

        self.assertEqual(add_response.status_code, 302)
        category = Category.objects.get(name="New Summer Party Dresses")
        self.assertEqual(category.slug, "new-summer-party-dresses")

        change_response = self.client.post(
            reverse("admin:products_category_change", args=(category.pk,)),
            {
                "name": "Completely Different Collection",
                "slug": "stale-test-category",
                "is_active": "on",
                "_save": "Save",
            },
            HTTP_HOST="127.0.0.1",
        )

        self.assertEqual(change_response.status_code, 302)
        category.refresh_from_db()
        self.assertEqual(category.name, "Completely Different Collection")
        self.assertEqual(category.slug, "completely-different-collection")

        delete_response = self.client.post(
            reverse("admin:products_category_delete", args=(category.pk,)),
            {"post": "yes"},
            HTTP_HOST="127.0.0.1",
        )

        self.assertEqual(delete_response.status_code, 302)
        self.assertFalse(Category.objects.filter(pk=category.pk).exists())

    def test_product_requires_at_least_one_variant_with_sku(self):
        category = Category.objects.create(name="Product category")
        add_url = reverse("admin:products_product_add")
        add_page = self.client.get(add_url, HTTP_HOST="127.0.0.1")
        variant_formset = next(
            inline.formset
            for inline in add_page.context["inline_admin_formsets"]
            if inline.formset.prefix == "variants"
        )
        self.assertEqual(variant_formset.min_num, 1)
        self.assertContains(add_page, 'name="variants-0-sku"', html=False)

        response = self.client.post(
            add_url,
            {
                "category": str(category.pk),
                "name": "Product without SKU",
                "slug": "product-without-sku",
                "regular_price": "1000.00",
                "status": Product.Status.DRAFT,
                "images-TOTAL_FORMS": "1",
                "images-INITIAL_FORMS": "0",
                "images-MIN_NUM_FORMS": "0",
                "images-MAX_NUM_FORMS": "1000",
                "variants-TOTAL_FORMS": "1",
                "variants-INITIAL_FORMS": "0",
                "variants-MIN_NUM_FORMS": "1",
                "variants-MAX_NUM_FORMS": "1000",
                "_save": "Save",
            },
            HTTP_HOST="127.0.0.1",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This field is required.")
        self.assertFalse(Product.objects.filter(name="Product without SKU").exists())

    def test_empty_inventory_movement_shows_validation_instead_of_crashing(self):
        response = self.client.post(
            reverse("admin:products_inventorymovement_add"),
            {"_save": "Save"},
            HTTP_HOST="127.0.0.1",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This field is required.")
        self.assertEqual(InventoryMovement.objects.count(), 0)

    def test_empty_coupon_shows_validation_instead_of_crashing(self):
        response = self.client.post(
            reverse("admin:products_coupon_add"),
            {"_save": "Save"},
            HTTP_HOST="127.0.0.1",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This field is required.")
        self.assertEqual(Coupon.objects.count(), 0)

    def test_coupon_admin_displays_specific_past_start_date_error(self):
        starts_at = timezone.localtime(timezone.now() - timezone.timedelta(days=1))
        expires_at = timezone.localtime(timezone.now() + timezone.timedelta(days=1))
        response = self.client.post(
            reverse("admin:products_coupon_add"),
            {
                "code": "PAST-COUPON",
                "discount_type": Coupon.DiscountType.PERCENTAGE,
                "discount_value": "10.00",
                "minimum_order_value": "0.00",
                "starts_at_0": starts_at.strftime("%Y-%m-%d"),
                "starts_at_1": starts_at.strftime("%H:%M:%S"),
                "expires_at_0": expires_at.strftime("%Y-%m-%d"),
                "expires_at_1": expires_at.strftime("%H:%M:%S"),
                "per_customer_limit": "1",
                "is_active": "on",
                "_save": "Save",
            },
            HTTP_HOST="127.0.0.1",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Start date cannot be earlier than today.")
        self.assertFalse(Coupon.objects.filter(code="PAST-COUPON").exists())

    def test_categories_can_be_reordered_by_drag_and_drop_endpoint(self):
        first = Category.objects.create(name="First category", display_order=0)
        second = Category.objects.create(name="Second category", display_order=1)
        third = Category.objects.create(name="Third category", display_order=2)

        response = self.client.post(
            reverse("admin:products_category_reorder"),
            data=json.dumps({"ordered_ids": [third.pk, first.pk, second.pk]}),
            content_type="application/json",
            HTTP_HOST="127.0.0.1",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            list(Category.objects.order_by("display_order").values_list("pk", flat=True)),
            [third.pk, first.pk, second.pk],
        )
        self.assertEqual(
            list(Category.objects.order_by("display_order").values_list("display_order", flat=True)),
            [0, 1, 2],
        )

    def test_newsletter_subscriptions_cannot_be_added_from_admin(self):
        subscription = NewsletterSubscription.objects.create(email="frontend@example.com", source="frontend")
        list_response = self.client.get(
            reverse("admin:content_management_newslettersubscription_changelist"),
            HTTP_HOST="127.0.0.1",
        )
        self.assertEqual(list_response.status_code, 200)
        self.assertNotContains(list_response, "Add Newsletter subscription")
        self.assertContains(list_response, "frontend@example.com")

        add_response = self.client.get(
            reverse("admin:content_management_newslettersubscription_add"),
            HTTP_HOST="127.0.0.1",
        )
        self.assertEqual(add_response.status_code, 403)
        self.assertEqual(
            self.client.get(
                reverse("admin:content_management_newslettersubscription_change", args=(subscription.pk,)),
                HTTP_HOST="127.0.0.1",
            ).status_code,
            200,
        )

    def test_newsletter_settings_is_singleton(self):
        add_url = reverse("admin:content_management_newslettersettings_add")
        self.assertEqual(self.client.get(add_url, HTTP_HOST="127.0.0.1").status_code, 200)
        NewsletterSettings.objects.create(title="Join our newsletter")
        self.assertEqual(self.client.get(add_url, HTTP_HOST="127.0.0.1").status_code, 403)

    def test_cms_pages_can_be_managed_and_publicly_retrieved(self):
        page = Page.objects.create(
            title="About FABRIQX",
            content="About page content",
            seo_title="About us",
            is_active=True,
        )
        admin_response = self.client.get(
            reverse("admin:content_management_page_changelist"),
            HTTP_HOST="127.0.0.1",
        )
        self.assertEqual(admin_response.status_code, 200)
        self.assertContains(admin_response, "About FABRIQX")
        self.assertContains(admin_response, reverse("admin:content_management_page_add"))

        self.client.logout()
        api_response = self.client.get(f"/api/v1/pages/{page.slug}/", HTTP_HOST="127.0.0.1")
        self.assertEqual(api_response.status_code, 200)
        self.assertEqual(api_response.json()["data"]["content"], "About page content")

        page.is_active = False
        page.save()
        self.assertEqual(
            self.client.get(f"/api/v1/pages/{page.slug}/", HTTP_HOST="127.0.0.1").status_code,
            404,
        )

    def test_admin_can_email_customer_secure_password_reset(self):
        user = get_user_model().objects.create_user(
            "reset_customer",
            email="reset-customer@example.com",
            password="OriginalPass123!",
        )
        customer = CustomerProfile.objects.create(user=user)
        response = self.client.post(
            reverse("admin:customers_customerprofile_changelist"),
            {
                "action": "send_password_reset",
                "_selected_action": [customer.pk],
            },
            HTTP_HOST="127.0.0.1",
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        reset_email = mail.outbox[0]
        self.assertEqual(reset_email.to, ["reset-customer@example.com"])
        self.assertIn("uid=", reset_email.body)
        self.assertIn("token=", reset_email.body)
        self.assertNotIn("OriginalPass123!", reset_email.body)

    def test_roles_page_uses_sidebar_read_write_matrix(self):
        response = self.client.get(reverse("admin:auth_user_change", args=(self.admin_user.pk,)), HTTP_HOST="127.0.0.1")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Permissions")
        self.assertContains(response, "Select all")
        self.assertNotContains(response, "Full access")
        self.assertContains(response, ">View<", html=False)
        self.assertContains(response, ">Edit<", html=False)
        for heading in (">Create<", ">Update<"):
            self.assertNotContains(response, heading, html=False)
        self.assertContains(response, "Content Management System")
        self.assertContains(response, "Products &amp; Inventory")
        self.assertNotContains(response, "Payments &amp; Finance")
        self.assertNotContains(response, "Marketing &amp; Reviews")
        self.assertNotContains(response, "Operations &amp; Integrations")
        for hidden_model in ("Addresses", "Influencer Commissions", "Serviceable Pincodes", "Integration Events"):
            self.assertNotContains(response, hidden_model)

    def test_cms_long_text_uses_rich_text_editor(self):
        response = self.client.get(reverse("admin:content_management_testimonial_add"), HTTP_HOST="127.0.0.1")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<trix-editor", html=False)
        self.assertContains(response, "unfold/forms/js/trix/trix.js", html=False)

    def test_homepage_section_has_rich_text_editor(self):
        response = self.client.get(reverse("admin:content_management_homepagesection_add"), HTTP_HOST="127.0.0.1")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="editor_content"', html=False)
        self.assertContains(response, "<trix-editor", html=False)
        self.assertContains(response, "Introduction / custom text")
        self.assertNotContains(response, 'name="content"', html=False)
        self.assertLess(response.content.index(b"Offers strip"), response.content.index(b"New arrivals"))

    def test_gift_section_admin_has_structured_content_and_repeatable_rows(self):
        response = self.client.get(reverse("admin:content_management_giftsection_add"), HTTP_HOST="127.0.0.1")
        self.assertEqual(response.status_code, 200)
        for field_name in (
            "badge_eyebrow", "badge_title", "accent_heading", "heading", "description",
            "main_image", "gift_image", "background_image", "thank_you_title",
            "thank_you_text", "cta_label", "cta_url",
        ):
            self.assertContains(response, f'name="{field_name}"', html=False)
        self.assertContains(response, 'name="features-0-text"', html=False)
        self.assertContains(response, 'name="features-4-text"', html=False)
        self.assertNotContains(response, 'name="features-5-text"', html=False)
        self.assertContains(response, 'name="statistics-0-value"', html=False)
        self.assertContains(response, 'name="statistics-2-value"', html=False)
        self.assertNotContains(response, 'name="statistics-3-value"', html=False)
        feature_formset, statistic_formset = [inline.formset for inline in response.context["inline_admin_formsets"]]
        self.assertEqual((feature_formset.total_form_count(), feature_formset.max_num), (5, 5))
        self.assertEqual((statistic_formset.total_form_count(), statistic_formset.max_num), (3, 3))
        self.assertEqual(feature_formset.min_num, 5)
        self.assertEqual(statistic_formset.min_num, 3)
        self.assertContains(response, "Small text above value")
        self.assertNotContains(response, ">Eyebrow</")
        self.assertNotContains(response, "Save and add another")

    def test_hero_banner_list_displays_order_without_textbox(self):
        Banner.objects.create(title="Homepage hero", image="banners/hero.jpg", display_order=3)

        response = self.client.get(
            reverse("admin:content_management_banner_changelist"),
            HTTP_HOST="127.0.0.1",
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'name="form-0-display_order"', html=False)
        self.assertContains(response, ">3</td>", html=False)

    def test_hero_banner_rejects_image_larger_than_five_mb(self):
        gif = b"GIF87a\x01\x00\x01\x00\x80\x01\x00\x00\x00\x00ccc,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
        upload = SimpleUploadedFile(
            "oversized.gif",
            gif + b"\0" * (5 * 1024 * 1024),
            content_type="image/gif",
        )

        response = self.client.post(
            reverse("admin:content_management_banner_add"),
            {"title": "Oversized hero", "image": upload, "display_order": "0", "is_active": "on", "_save": "Save"},
            HTTP_HOST="127.0.0.1",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Image file size must not exceed 5 MB.")
        self.assertContains(response, "Maximum file size: 5 MB.")
        self.assertFalse(Banner.objects.filter(title="Oversized hero").exists())

    def test_existing_hero_banner_image_has_preview(self):
        banner = Banner.objects.create(title="Hero", image="banners/hero.jpg")
        response = self.client.get(reverse("admin:content_management_banner_change", args=(banner.pk,)))
        self.assertContains(response, 'data-image-preview="image"')
        self.assertContains(response, banner.image.url)
        self.assertNotContains(response, "Existing banner image")

    def test_only_one_gift_section_can_be_added_in_admin(self):
        GiftSection.objects.create(
            internal_name="Homepage gift",
            heading="Get More Joy!",
            main_image="gift-sections/main/model.jpg",
        )
        changelist_url = reverse("admin:content_management_giftsection_changelist")
        add_url = reverse("admin:content_management_giftsection_add")

        changelist = self.client.get(changelist_url, HTTP_HOST="127.0.0.1")

        self.assertEqual(changelist.status_code, 200)
        self.assertNotContains(changelist, add_url)
        self.assertEqual(self.client.get(add_url, HTTP_HOST="127.0.0.1").status_code, 403)

    def test_brand_logo_section_has_one_new_slot_and_is_singleton(self):
        add_url = reverse("admin:content_management_brandlogosection_add")
        response = self.client.get(add_url, HTTP_HOST="127.0.0.1")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="logos-0-brand_name"', html=False)
        self.assertNotContains(response, 'name="logos-1-brand_name"', html=False)
        self.assertContains(response, "Maximum file size: 5 MB.")
        self.assertContains(response, 'class="inline-field-help', count=4, html=False)
        logo_formset = response.context["inline_admin_formsets"][0].formset
        self.assertEqual((logo_formset.min_num, logo_formset.max_num), (1, 1))
        self.assertNotContains(response, "Save and add another")
        BrandLogoSection.objects.create(internal_name="Homepage brands")
        self.assertEqual(self.client.get(add_url, HTTP_HOST="127.0.0.1").status_code, 403)

    def test_brand_logo_section_list_does_not_show_heading_column(self):
        BrandLogoSection.objects.create(internal_name="Homepage brands", heading="Our brands")

        response = self.client.get(
            reverse("admin:content_management_brandlogosection_changelist"),
            HTTP_HOST="127.0.0.1", follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("heading", response.context["cl"].list_display)
        self.assertNotContains(response, "Our brands")

    def test_brand_logo_section_rejects_all_empty_logo_slots(self):
        response = self.client.post(
            reverse("admin:content_management_brandlogosection_add"),
            {
                "logos-TOTAL_FORMS": "6",
                "logos-INITIAL_FORMS": "0",
                "logos-MIN_NUM_FORMS": "1",
                "logos-MAX_NUM_FORMS": "6",
                "_save": "Save",
            },
            HTTP_HOST="127.0.0.1",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This field is required.")
        self.assertFalse(BrandLogoSection.objects.exists())

    def test_brand_logo_rejects_image_larger_than_five_mb(self):
        gif = b"GIF87a\x01\x00\x01\x00\x80\x01\x00\x00\x00\x00ccc,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
        upload = SimpleUploadedFile(
            "oversized-logo.gif",
            gif + b"\0" * (5 * 1024 * 1024),
            content_type="image/gif",
        )

        response = self.client.post(
            reverse("admin:content_management_brandlogosection_add"),
            {
                "logos-TOTAL_FORMS": "6",
                "logos-INITIAL_FORMS": "0",
                "logos-MIN_NUM_FORMS": "1",
                "logos-MAX_NUM_FORMS": "6",
                "logos-0-logo": upload,
                "logos-0-brand_name": "Oversized brand",
                "logos-0-display_order": "0",
                "logos-0-is_active": "on",
                "_save": "Save",
            },
            HTTP_HOST="127.0.0.1",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Image file size must not exceed 5 MB.")
        self.assertContains(response, "Maximum file size: 5 MB.")
        self.assertFalse(BrandLogoSection.objects.exists())

    def test_brand_logo_can_be_removed_from_section(self):
        section = BrandLogoSection.objects.create(internal_name="Homepage brands")
        logos = [
            BrandLogo.objects.create(
                section=section,
                brand_name=f"Brand {index}",
                logo=f"brand-logos/brand-{index}.gif",
                display_order=index,
            )
            for index in range(6)
        ]
        data = {
            "logos-TOTAL_FORMS": "6",
            "logos-INITIAL_FORMS": "6",
            "logos-MIN_NUM_FORMS": "0",
            "logos-MAX_NUM_FORMS": "6",
            "logos-0-DELETE": "on",
            "_save": "Save",
        }
        for index, logo in enumerate(logos):
            data.update({
                f"logos-{index}-id": str(logo.pk),
                f"logos-{index}-section": str(section.pk),
                f"logos-{index}-brand_name": logo.brand_name,
                f"logos-{index}-alt_text": logo.alt_text,
                f"logos-{index}-url": logo.url,
                f"logos-{index}-display_order": str(logo.display_order),
                f"logos-{index}-is_active": "on",
            })

        response = self.client.post(
            reverse("admin:content_management_brandlogosection_change", args=(section.pk,)),
            data,
            HTTP_HOST="127.0.0.1",
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(BrandLogo.objects.filter(pk=logos[0].pk).exists())
        self.assertEqual(section.logos.count(), 5)

    def test_offer_grid_allows_up_to_three_items_and_is_singleton(self):
        add_url = reverse("admin:content_management_offergridsection_add")
        response = self.client.get(add_url, HTTP_HOST="127.0.0.1")
        self.assertContains(response, 'name="items-0-shop_now_url"', html=False)
        self.assertNotContains(response, 'name="items-3-shop_now_url"', html=False)
        item_formset = response.context["inline_admin_formsets"][0].formset
        self.assertEqual((item_formset.min_num, item_formset.max_num), (1, 3))
        OfferGridSection.objects.create()
        self.assertEqual(self.client.get(add_url, HTTP_HOST="127.0.0.1").status_code, 403)

    def test_offer_grid_rejects_empty_mandatory_item_fields(self):
        response = self.client.post(
            reverse("admin:content_management_offergridsection_add"),
            {
                "internal_name": "Homepage offer grid",
                "is_active": "on",
                "items-TOTAL_FORMS": "3",
                "items-INITIAL_FORMS": "0",
                "items-MIN_NUM_FORMS": "3",
                "items-MAX_NUM_FORMS": "3",
                "_save": "Save",
            },
            HTTP_HOST="127.0.0.1",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This field is required.")
        self.assertFalse(OfferGridSection.objects.exists())

    def test_footer_social_editor_supports_dynamic_links_and_is_singleton(self):
        add_url = reverse("admin:content_management_footersocialsection_add")
        response = self.client.get(add_url, HTTP_HOST="127.0.0.1")
        self.assertContains(response, 'name="links-0-url"', html=False)
        self.assertNotContains(response, 'name="links-1-url"', html=False)
        self.assertContains(response, 'name="links-MAX_NUM_FORMS" value="10"', html=False)
        link_formset = response.context["inline_admin_formsets"][0].formset
        self.assertEqual((link_formset.min_num, link_formset.max_num), (1, 10))
        FooterSocialSection.objects.create()
        self.assertEqual(self.client.get(add_url, HTTP_HOST="127.0.0.1").status_code, 403)

    def test_footer_social_section_rejects_empty_mandatory_link_fields(self):
        response = self.client.post(
            reverse("admin:content_management_footersocialsection_add"),
            {
                "links-TOTAL_FORMS": "1",
                "links-INITIAL_FORMS": "0",
                "links-MIN_NUM_FORMS": "1",
                "links-MAX_NUM_FORMS": "10",
                "_save": "Save",
            },
            HTTP_HOST="127.0.0.1",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This field is required.")
        self.assertFalse(FooterSocialSection.objects.exists())

    def test_admin_can_create_influencer_with_credentials(self):
        photo = SimpleUploadedFile(
            "creator.gif",
            b"GIF87a\x01\x00\x01\x00\x80\x01\x00\x00\x00\x00ccc,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;",
            content_type="image/gif",
        )
        with tempfile.TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(
                    reverse("admin:influencers_influencerprofile_add"),
                    {
                        "username": "creator_one",
                        "first_name": "Creator",
                        "last_name": "One",
                        "email": "creator@example.com",
                        "password": "StrongPass123!",
                        "confirm_password": "StrongPass123!",
                        "phone": "9999999999",
                        "profile_image": photo,
                        "address_line_1": "12 MG Road",
                        "address_line_2": "Near Central Mall",
                        "address_city": "Bengaluru",
                        "address_state": "Karnataka",
                        "address_postal_code": "560001",
                        "address_country": "India",
                        "social_handle": "@creator_one",
                        "commission_rate": "12.50",
                        "commission_type": "percentage",
                        "commission_fixed_amount": "0",
                        "is_active": "on",
                        "_save": "Save",
                    },
                    HTTP_HOST="127.0.0.1",
                )
        self.assertEqual(response.status_code, 302)
        user = get_user_model().objects.get(username="creator_one")
        self.assertTrue(user.check_password("StrongPass123!"))
        self.assertEqual(user.first_name, "Creator")
        self.assertEqual(user.last_name, "One")
        self.assertEqual(user.fabriqx_role.role, UserRole.Role.INFLUENCER)
        self.assertTrue(user.influencer_profile.affiliate_id.startswith("INF-"))
        self.assertTrue(user.influencer_profile.profile_image.name.startswith("influencers/profiles/creator"))
        self.assertEqual(user.influencer_profile.address, {
            "line1": "12 MG Road",
            "line2": "Near Central Mall",
            "city": "Bengaluru",
            "state": "Karnataka",
            "postal_code": "560001",
            "country": "India",
        })
        self.assertEqual(len(mail.outbox), 1)
        onboarding_email = mail.outbox[0]
        self.assertEqual(onboarding_email.to, ["creator@example.com"])
        self.assertIn("Username: creator_one", onboarding_email.body)
        self.assertIn("Temporary password: StrongPass123!", onboarding_email.body)
        self.assertIn(user.influencer_profile.affiliate_id, onboarding_email.body)
        self.assertIn("/influencer/login", onboarding_email.body)
        self.assertEqual(len(onboarding_email.alternatives), 1)
        html_email, mime_type = onboarding_email.alternatives[0]
        self.assertEqual(mime_type, "text/html")
        self.assertIn("Welcome to FABRIQX, Creator One", html_email)
        self.assertIn("Sign in to your workspace", html_email)
        self.assertIn("StrongPass123!", html_email)

        change_response = self.client.get(
            reverse("admin:influencers_influencerprofile_change", args=(user.influencer_profile.pk,)),
            HTTP_HOST="127.0.0.1",
        )
        self.assertContains(change_response, 'name="address_line_1" value="12 MG Road"', html=False)
        self.assertContains(change_response, 'name="address_city" value="Bengaluru"', html=False)
        self.assertContains(change_response, 'name="first_name" value="Creator"', html=False)
        self.assertContains(change_response, 'name="last_name" value="One"', html=False)

    def test_influencer_admin_rejects_password_shorter_than_eight_characters(self):
        response = self.client.post(
            reverse("admin:influencers_influencerprofile_add"),
            {
                "username": "short_password_creator",
                "first_name": "Short",
                "last_name": "Password",
                "email": "short-password@example.com",
                "password": "Abc1234",
                "confirm_password": "Abc1234",
                "commission_rate": "10.00",
                "commission_type": "percentage",
                "commission_fixed_amount": "0",
                "is_active": "on",
                "_save": "Save",
            },
            HTTP_HOST="127.0.0.1",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This password is too short. It must contain at least 8 characters.")
        self.assertFalse(get_user_model().objects.filter(username="short_password_creator").exists())

    def test_influencer_email_must_be_unique_case_insensitively(self):
        get_user_model().objects.create_user("existing", email="Creator@Example.com")
        response = self.client.post(
            reverse("admin:influencers_influencerprofile_add"),
            {
                "username": "another_creator",
                "email": "creator@example.com",
                "password": "StrongPass123!",
                "confirm_password": "StrongPass123!",
                "commission_rate": "10.00",
                "commission_type": "percentage",
                "commission_fixed_amount": "0",
                "is_active": "on",
                "_save": "Save",
            },
            HTTP_HOST="127.0.0.1",
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "An account with this email address already exists.")
        self.assertFalse(get_user_model().objects.filter(username="another_creator").exists())

    def test_site_settings_supports_fixed_and_percentage_commission(self):
        settings_object = SiteSettings(commission_type=SiteSettings.CommissionType.FIXED, commission_fixed_amount=Decimal("250"))
        settings_object.full_clean()

    def test_site_settings_admin_switches_commission_fields_dynamically(self):
        settings_object = SiteSettings.load()
        change_url = reverse("admin:fabriqx_sitesettings_change", args=(settings_object.pk,))
        list_response = self.client.get(
            reverse("admin:fabriqx_sitesettings_changelist"),
            HTTP_HOST="127.0.0.1",
        )
        self.assertRedirects(list_response, change_url)
        response = self.client.get(change_url, HTTP_HOST="127.0.0.1")
        self.assertContains(response, "commission_type == &#x27;percentage&#x27;")
        self.assertContains(response, "commission_type == &#x27;fixed&#x27;")
        self.assertNotContains(response, "Back to list")

        response = self.client.post(change_url, {
            "commission_type": "fixed",
            "commission_fixed_amount": "250.00",
            "_save": "Save",
        }, HTTP_HOST="127.0.0.1")
        self.assertRedirects(response, change_url)
        settings_object.refresh_from_db()
        self.assertEqual(settings_object.commission_fixed_amount, Decimal("250.00"))
        self.assertEqual(settings_object.commission_rate, Decimal("0"))

    def test_influencer_form_has_no_payment_or_tax_fields(self):
        response = self.client.get(reverse("admin:influencers_influencerprofile_add"), HTTP_HOST="127.0.0.1")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Payment and tax details")
        self.assertNotContains(response, "Bank account")
        self.assertNotContains(response, "Tax identifier")
        self.assertNotContains(response, "Social handle")

    def test_influencer_credentials_use_unfold_input_borders(self):
        response = self.client.get(reverse("admin:influencers_influencerprofile_add"), HTTP_HOST="127.0.0.1")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "border-base-200")
        self.assertContains(response, 'autocomplete="new-password"', count=2, html=False)

    def test_manage_influencers_list_shows_add_action(self):
        response = self.client.get(reverse("admin:influencers_influencerprofile_changelist"), HTTP_HOST="127.0.0.1")
        self.assertContains(response, reverse("admin:influencers_influencerprofile_add"))
        self.assertContains(response, "Add Influencer")

    def test_product_import_actions_and_sample(self):
        list_response = self.client.get(reverse("admin:products_product_changelist"), HTTP_HOST="127.0.0.1")
        self.assertContains(list_response, "Import Products")
        self.assertContains(list_response, "Download Sample")
        sample = self.client.get(reverse("admin:products_product_download_product_sample"), HTTP_HOST="127.0.0.1")
        self.assertEqual(sample.status_code, 200)
        self.assertIn("spreadsheetml", sample["Content-Type"])

        with tempfile.TemporaryDirectory() as media_root:
            image = Path(media_root) / "products/import/dress.jpg"
            image.parent.mkdir(parents=True)
            image.write_bytes(b"sample-image")
            csv_data = (
                "category,category_audience,product_name,slug,description,regular_price,status,sku,size,color,stock_quantity,image_path,is_primary_image\n"
                "Dresses,women,Imported Dress,imported-dress,Imported product,1999.00,active,IMPORT-M-RED,M,Red,20,products/import/dress.jpg,true\n"
            ).encode()
            upload = SimpleUploadedFile("products.csv", csv_data, content_type="text/csv")
            with override_settings(MEDIA_ROOT=Path(media_root)):
                response = self.client.post(reverse("admin:products_product_import_products"), {"import_file": upload}, HTTP_HOST="127.0.0.1")
            self.assertEqual(response.status_code, 302)
            product = Product.objects.get(slug="imported-dress")
            self.assertEqual(product.variants.get().stock_quantity, 20)
            self.assertEqual(product.images.get().image.name, "products/import/dress.jpg")

    def test_product_import_accepts_public_image_url(self):
        csv_data = (
            "category,product_name,slug,description,regular_price,status,sku,stock_quantity,image_url,is_primary_image\n"
            "Dresses,Remote Image Dress,remote-image-dress,Imported product,1499.00,active,REMOTE-M,10,https://cdn.example.com/dress.jpg,true\n"
        ).encode()
        upload = SimpleUploadedFile("products.csv", csv_data, content_type="text/csv")
        with tempfile.TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=Path(media_root)):
            with patch("fabriqx.product_import._download_image", return_value=("remote-image-dress.jpg", ContentFile(b"image-bytes"))):
                response = self.client.post(reverse("admin:products_product_import_products"), {"import_file": upload}, HTTP_HOST="127.0.0.1")
        self.assertEqual(response.status_code, 302)
        product = Product.objects.get(slug="remote-image-dress")
        self.assertTrue(product.images.exists())
        self.assertNotEqual(product.images.get().image.name, "remote-image-dress.jpg")

    def test_product_import_treats_url_in_image_path_as_remote_image(self):
        csv_data = (
            "category,product_name,regular_price,sku,image_path\n"
            "Dresses,Remote Path Dress,1499.00,REMOTE-PATH,https://cdn.example.com/dress.jpg\n"
        ).encode()
        upload = SimpleUploadedFile("products.csv", csv_data, content_type="text/csv")
        with tempfile.TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=Path(media_root)):
            with patch("fabriqx.product_import._download_image", return_value=("remote-path-dress.jpg", ContentFile(b"image-bytes"))) as download:
                response = self.client.post(reverse("admin:products_product_import_products"), {"import_file": upload}, HTTP_HOST="127.0.0.1")
        self.assertEqual(response.status_code, 302)
        download.assert_called_once_with("https://cdn.example.com/dress.jpg", "remote-path-dress")

    def test_product_import_allows_new_product_without_image(self):
        csv_data = (
            "category,product_name,regular_price,sku,stock_quantity\n"
            "Dresses,Image Optional Dress,1499.00,NO-IMAGE-M,10\n"
        ).encode()
        upload = SimpleUploadedFile("products.csv", csv_data, content_type="text/csv")
        response = self.client.post(
            reverse("admin:products_product_import_products"),
            {"import_file": upload},
            HTTP_HOST="127.0.0.1",
        )
        self.assertEqual(response.status_code, 302)
        product = Product.objects.get(slug="image-optional-dress")
        self.assertFalse(product.images.exists())


class CustomerApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.category = Category.objects.create(name="Celebration Wear")
        self.product = Product.objects.create(
            category=self.category,
            name="Silk Dress",
            description="A celebration dress",
            regular_price=Decimal("2000.00"),
            sale_price=Decimal("1500.00"),
            status=Product.Status.ACTIVE,
            is_trending=True,
        )
        self.variant = ProductVariant.objects.create(product=self.product, sku="SILK-M", size="M", stock_quantity=5)

    def register_customer(self):
        response = self.client.post("/api/v1/auth/register/", {
            "email": "buyer@example.com", "phone": "9999999999", "first_name": "Buyer", "last_name": "One", "password": "StrongPass123!",
        }, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertIn("refresh", response.data)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access']}")
        return response

    def test_new_newsletter_subscription_is_saved(self):
        response = self.client.post("/api/v1/newsletter/", {"email": "reader@example.com", "source": "homepage"}, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertTrue(NewsletterSubscription.objects.filter(email="reader@example.com", is_active=True).exists())

    def test_homepage_returns_active_structured_gift_sections(self):
        section = GiftSection.objects.create(
            internal_name="September free gift",
            accent_heading="Shop More,",
            heading="Get More Joy!",
            description="Every purchase comes with a free gift.",
            main_image="gift-sections/main/model.jpg",
            gift_image="gift-sections/gifts/gift.png",
            cta_label="Shop now",
            cta_url="/products/",
        )
        for index in range(6):
            GiftSectionFeature.objects.create(section=section, text=f"Benefit {index + 1}", display_order=index)
        GiftSectionFeature.objects.create(section=section, text="Hidden benefit", is_active=False)
        for index in range(4):
            GiftSectionStatistic.objects.create(
                section=section,
                eyebrow="Trusted by" if index == 0 else "",
                value=f"{index + 1}K+",
                label=f"Statistic {index + 1}",
                display_order=index,
            )
        GiftSection.objects.create(
            internal_name="Future campaign",
            heading="Coming soon",
            main_image="gift-sections/main/future.jpg",
            starts_at=timezone.now() + timezone.timedelta(days=1),
        )
        GiftSection.objects.create(
            internal_name="Duplicate active campaign",
            heading="Must not be returned",
            main_image="gift-sections/main/duplicate.jpg",
        )

        response = self.client.get("/api/v1/homepage/", HTTP_HOST="testserver")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["gift_sections"]), 1)
        gift = response.data["gift_sections"][0]
        self.assertEqual(gift["internal_name"], "September free gift")
        self.assertEqual(gift["heading"], "Get More Joy!")
        self.assertEqual(gift["main_image"], "http://testserver/media/gift-sections/main/model.jpg")
        self.assertEqual([feature["text"] for feature in gift["features"]], [f"Benefit {index}" for index in range(1, 6)])
        self.assertEqual([statistic["value"] for statistic in gift["statistics"]], ["1K+", "2K+", "3K+"])
        self.assertEqual(gift["cta_url"], "/products/")

    def test_homepage_returns_brand_logo_section_with_six_active_logos(self):
        section = BrandLogoSection.objects.create(background_color="#f7e8d8")
        for index in range(7):
            BrandLogo.objects.create(
                section=section,
                brand_name=f"Brand {index + 1}",
                logo=f"brand-logos/brand-{index + 1}.svg",
                display_order=index,
            )

        response = self.client.get("/api/v1/homepage/", HTTP_HOST="testserver")

        payload = response.data["brand_logo_section"]
        self.assertEqual(payload["background_color"], "#f7e8d8")
        self.assertEqual(len(payload["logos"]), 6)
        self.assertEqual(payload["logos"][0]["alt_text"], "Brand 1")
        self.assertEqual(payload["logos"][0]["logo"], "http://testserver/media/brand-logos/brand-1.svg")

    def test_homepage_returns_offer_banner_and_three_grid_items(self):
        OfferBanner.objects.create(internal_name="Jewellery offer", desktop_image="offers/banner.jpg", shop_now_url="/jewellery/")
        grid = OfferGridSection.objects.create()
        for index in range(4):
            OfferGridItem.objects.create(section=grid, internal_name=f"Offer {index}", desktop_image=f"offers/grid-{index}.jpg", shop_now_url=f"/offer-{index}/", display_order=index)

        response = self.client.get("/api/v1/homepage/", HTTP_HOST="testserver")

        self.assertEqual(response.data["offer_banners"][0]["shop_now_url"], "/jewellery/")
        self.assertEqual(len(response.data["offer_grid"]["items"]), 3)

    def test_homepage_returns_footer_social_links(self):
        section = FooterSocialSection.objects.create(heading="SOCIAL")
        FooterSocialLink.objects.create(section=section, platform_name="Instagram", icon="footer/social-icons/instagram.svg", url="https://instagram.com/fabriqx")

        response = self.client.get("/api/v1/homepage/", HTTP_HOST="testserver")

        self.assertEqual(response.data["footer_social"]["heading"], "SOCIAL")
        self.assertEqual(response.data["footer_social"]["links"][0]["aria_label"], "Instagram")

    def test_complete_influencer_dashboard_api_scope(self):
        influencer_user = get_user_model().objects.create_user(
            "dashboard_creator", email="dashboard@example.com", password="CreatorPass123!"
        )
        influencer = InfluencerProfile.objects.create(
            user=influencer_user, phone="9000000000"
        )
        customer_user = get_user_model().objects.create_user("referral_customer", email="referral@example.com")
        customer = CustomerProfile.objects.create(user=customer_user)

        paid_order = Order.objects.create(
            customer=customer, email=customer_user.email, phone="9111111111",
            shipping_address={"line1": "Private customer address"},
            billing_address={"line1": "Private billing address"},
            subtotal=Decimal("1000"), grand_total=Decimal("1000"), influencer=influencer,
            status=Order.Status.DELIVERED,
        )
        OrderItem.objects.create(
            order=paid_order, variant=self.variant, product_name=self.product.name,
            sku=self.variant.sku, unit_price=Decimal("1000"), quantity=1, total=Decimal("1000"),
        )
        Payment.objects.create(
            order=paid_order, provider="test", status=Payment.Status.PAID,
            amount=Decimal("1000"), paid_at=timezone.now(),
        )
        commission = InfluencerCommission.objects.create(
            influencer=influencer, order=paid_order, rate=Decimal("10"),
            eligible_amount=Decimal("1000"), commission_amount=Decimal("100"),
            status=InfluencerCommission.Status.APPROVED,
        )
        pending_order = Order.objects.create(
            customer=customer, email=customer_user.email, phone="9111111111",
            subtotal=Decimal("500"), grand_total=Decimal("500"), influencer=influencer,
        )
        Payment.objects.create(order=pending_order, provider="test", amount=Decimal("500"))

        self.client.force_authenticate(influencer_user)
        profile = self.client.patch(
            "/api/v1/influencer/profile/",
            {"first_name": "Dashboard", "address": {"city": "Delhi"}},
            format="json",
        )
        self.assertEqual(profile.status_code, 200, profile.data)
        self.assertEqual(profile.data["address"]["city"], "Delhi")
        self.assertEqual(profile.data["affiliate_id"], influencer.affiliate_id)

        influencer.address = {"line1": "12 MG Road", "city": "Delhi", "country": "India"}
        influencer.save(update_fields=("address", "updated_at"))
        profile = self.client.patch(
            "/api/v1/influencer/profile/",
            {"address": {"city": "Mumbai"}},
            format="json",
        )
        self.assertEqual(profile.status_code, 200, profile.data)
        self.assertEqual(profile.data["address"], {"line1": "12 MG Road", "city": "Mumbai", "country": "India"})

        displayed_profile = self.client.get("/api/v1/influencer/profile/")
        self.assertEqual(displayed_profile.status_code, 200, displayed_profile.data)
        self.assertEqual(displayed_profile.data["address"], profile.data["address"])

        photo = SimpleUploadedFile(
            "updated.gif",
            b"GIF87a\x01\x00\x01\x00\x80\x01\x00\x00\x00\x00ccc,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;",
            content_type="image/gif",
        )
        with tempfile.TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            profile = self.client.patch(
                "/api/v1/influencer/profile/",
                {"photo": photo},
                format="multipart",
            )
            self.assertEqual(profile.status_code, 200, profile.data)
            self.assertIn("/media/influencers/profiles/updated", profile.data["photo"])
            self.assertIn("?v=", profile.data["photo"])
            influencer.refresh_from_db()
            self.assertTrue(influencer.profile_image.name.startswith("influencers/profiles/updated"))

            displayed_profile = self.client.get("/api/v1/influencer/profile/")
            self.assertEqual(displayed_profile.data["photo"], profile.data["photo"])

        dashboard = self.client.get("/api/v1/influencer/dashboard/")
        self.assertEqual(dashboard.status_code, 200, dashboard.data)
        self.assertEqual(dashboard.data["total_referred_orders"], 2)
        self.assertEqual(dashboard.data["successful_orders"], 1)
        self.assertEqual(Decimal(dashboard.data["total_sales"]), Decimal("1000"))
        self.assertEqual(Decimal(dashboard.data["approved_commission"]), Decimal("100"))

        orders = self.client.get("/api/v1/influencer/orders/")
        self.assertEqual(orders.status_code, 200, orders.data)
        self.assertEqual(orders.data["count"], 2)
        self.assertNotIn("shipping_address", orders.data["results"][0])
        self.assertNotIn("billing_address", orders.data["results"][0])

        detail = self.client.get(f"/api/v1/influencer/orders/{paid_order.pk}/")
        self.assertEqual(detail.status_code, 200, detail.data)
        self.assertEqual(detail.data["items"][0]["sku"], self.variant.sku)
        other_user = get_user_model().objects.create_user("other_creator", email="other-creator@example.com")
        other_influencer = InfluencerProfile.objects.create(user=other_user)
        other_order = Order.objects.create(
            customer=customer, email=customer_user.email, phone="9111111111",
            subtotal=Decimal("200"), grand_total=Decimal("200"), influencer=other_influencer,
        )
        self.assertEqual(self.client.get(f"/api/v1/influencer/orders/{other_order.pk}/").status_code, 404)

        sales = self.client.get("/api/v1/influencer/sales/?period=day")
        self.assertEqual(sales.status_code, 200, sales.data)
        self.assertEqual(sales.data["successful_orders"], 1)
        self.assertEqual(len(sales.data["series"]), 1)
        self.assertEqual(self.client.get("/api/v1/influencer/sales/?period=year").status_code, 400)

        commissions = self.client.get("/api/v1/influencer/commissions/")
        self.assertEqual(commissions.status_code, 200, commissions.data)
        self.assertEqual(commissions.data["results"][0]["order_number"], paid_order.number)

        paid_order.status = Order.Status.RETURNED
        paid_order.save(update_fields=("status", "updated_at"))
        commission.refresh_from_db()
        self.assertEqual(commission.status, InfluencerCommission.Status.REVERSED)
        refreshed_dashboard = self.client.get("/api/v1/influencer/dashboard/")
        self.assertEqual(refreshed_dashboard.data["successful_orders"], 0)
        self.assertEqual(Decimal(refreshed_dashboard.data["total_commission"]), Decimal("0"))

        influencer.is_active = False
        influencer.save(update_fields=("is_active", "updated_at"))
        self.assertEqual(self.client.get("/api/v1/influencer/dashboard/").status_code, 403)

    def test_api_responses_use_uniform_envelope(self):
        invalid = self.client.post(
            "/api/v1/auth/login/",
            {"login": "missing@example.com", "password": "incorrect"},
            format="json",
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid.json(), {
            "success": False,
            "message": "Invalid credentials.",
            "status_code": 400,
        })

        response = self.client.get("/api/v1/homepage/")
        body = response.json()
        self.assertEqual(body["success"], True)
        self.assertEqual(body["message"], "Request successful.")
        self.assertEqual(body["status_code"], 200)
        self.assertIn("data", body)

    def test_public_storefront_and_swagger_are_available(self):
        self.assertEqual(self.client.get("/api/v1/homepage/").status_code, 200)
        products = self.client.get("/api/v1/products/?search=silk")
        self.assertEqual(products.status_code, 200)
        self.assertEqual(products.data["count"], 1)
        self.assertEqual(self.client.get("/api/schema/").status_code, 200)
        self.assertEqual(self.client.get("/api/docs/").status_code, 200)

    @override_settings(
        MAILERS={"default": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}},
        FRONTEND_RESET_PASSWORD_URL="https://developer-beta.com/fabriqx/reset-password",
    )
    def test_customer_forgot_and_reset_password(self):
        self.register_customer()
        self.client.credentials()
        requested = self.client.post("/api/v1/auth/forgot-password/", {"email": "buyer@example.com"}, format="json")
        self.assertEqual(requested.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(len(mail.outbox[0].alternatives), 1)
        reset_html, mime_type = mail.outbox[0].alternatives[0]
        self.assertEqual(mime_type, "text/html")
        self.assertIn("Reset my password", reset_html)
        reset_url = next(part for part in mail.outbox[0].body.split() if part.startswith("https://"))
        params = parse_qs(urlparse(reset_url).query)
        payload = {"uid": params["uid"][0], "token": params["token"][0], "new_password": "NewStrongPass456!", "confirm_password": "NewStrongPass456!"}
        reset = self.client.post("/api/v1/auth/reset-password/", payload, format="json")
        self.assertEqual(reset.status_code, 200, reset.data)
        reused = self.client.post("/api/v1/auth/reset-password/", payload, format="json")
        self.assertEqual(reused.status_code, 400)
        login = self.client.post("/api/v1/auth/login/", {"login": "buyer@example.com", "password": "NewStrongPass456!"}, format="json")
        self.assertEqual(login.status_code, 200, login.data)

    @override_settings(
        MAILERS={"default": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}},
        FRONTEND_RESET_PASSWORD_URL="https://developer-beta.com/fabriqx/reset-password",
    )
    def test_influencer_forgot_and_reset_password(self):
        user = get_user_model().objects.create_user(
            "creator_reset",
            email="creator-reset@example.com",
            password="OriginalPass123!",
        )
        InfluencerProfile.objects.create(user=user)

        requested = self.client.post(
            "/api/v1/auth/forgot-password/",
            {"email": "CREATOR-RESET@example.com"},
            format="json",
        )
        self.assertEqual(requested.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        reset_url = next(part for part in mail.outbox[0].body.split() if part.startswith("https://"))
        params = parse_qs(urlparse(reset_url).query)
        self.assertEqual(params["account"], ["influencer"])
        payload = {
            "uid": params["uid"][0],
            "token": params["token"][0],
            "account": params["account"][0],
            "new_password": "NewCreatorPass456!",
            "confirm_password": "NewCreatorPass456!",
        }
        reset = self.client.post("/api/v1/auth/reset-password/", payload, format="json")
        self.assertEqual(reset.status_code, 200, reset.data)
        login = self.client.post(
            "/api/v1/auth/login/",
            {"login": "creator-reset@example.com", "password": "NewCreatorPass456!"},
            format="json",
        )
        self.assertEqual(login.status_code, 200, login.data)

    def test_customer_can_change_password_after_login(self):
        self.register_customer()
        rejected = self.client.post(
            "/api/v1/auth/change-password/",
            {
                "current_password": "WrongPass123!",
                "new_password": "NewCustomerPass456!",
                "confirm_password": "NewCustomerPass456!",
            },
            format="json",
        )
        self.assertEqual(rejected.status_code, 400)

        changed = self.client.post(
            "/api/v1/auth/change-password/",
            {
                "current_password": "StrongPass123!",
                "new_password": "NewCustomerPass456!",
                "confirm_password": "NewCustomerPass456!",
            },
            format="json",
        )
        self.assertEqual(changed.status_code, 200, changed.data)
        self.assertTrue(get_user_model().objects.get(email="buyer@example.com").check_password("NewCustomerPass456!"))

    def test_influencer_can_change_password_after_login(self):
        user = get_user_model().objects.create_user(
            "creator_change",
            email="creator-change@example.com",
            password="OriginalPass123!",
        )
        InfluencerProfile.objects.create(user=user)
        self.client.force_authenticate(user)

        changed = self.client.post(
            "/api/v1/auth/change-password/",
            {
                "current_password": "OriginalPass123!",
                "new_password": "NewCreatorPass456!",
                "confirm_password": "NewCreatorPass456!",
            },
            format="json",
        )
        self.assertEqual(changed.status_code, 200, changed.data)
        user.refresh_from_db()
        self.assertTrue(user.check_password("NewCreatorPass456!"))

    @override_settings(MARKETPLACE_WEBHOOK_SECRET="test-webhook-secret")
    def test_customer_cart_affiliate_checkout_payment_and_invoice(self):
        self.register_customer()
        influencer_user = get_user_model().objects.create_user("creator", "creator@example.com", "pass")
        influencer = InfluencerProfile.objects.create(user=influencer_user)
        coupon = Coupon.objects.create(
            code="CREATOR10", discount_type=Coupon.DiscountType.PERCENTAGE, discount_value=Decimal("10"),
            starts_at=timezone.now() - timezone.timedelta(days=1), expires_at=timezone.now() + timezone.timedelta(days=1),
        )
        coupon.influencers.add(influencer)

        cart = self.client.post("/api/v1/cart/", {"variant": self.variant.pk, "quantity": 2}, format="json")
        self.assertEqual(cart.status_code, 201, cart.data)
        affiliate = self.client.post("/api/v1/checkout/affiliate/validate/", {"affiliate_id": influencer.affiliate_id}, format="json")
        self.assertEqual(affiliate.status_code, 200)
        self.assertEqual(affiliate.data["coupon"]["code"], "CREATOR10")
        checkout = self.client.post("/api/v1/checkout/", {
            "email": "buyer@example.com", "phone": "9999999999", "shipping_address": {"line1": "1 Main Street", "city": "Delhi"},
            "affiliate_id": influencer.affiliate_id, "payment_provider": "marketplace",
        }, format="json")
        self.assertEqual(checkout.status_code, 201, checkout.data)
        self.assertEqual(Decimal(checkout.data["discount_total"]), Decimal("300.00"))
        order_id = checkout.data["id"]
        initialized = self.client.post(f"/api/v1/account/orders/{order_id}/initialize-payment/", {}, format="json")
        self.assertEqual(initialized.status_code, 200, initialized.data)
        payload = json.dumps({"order_number": checkout.data["number"], "transaction_id": "PAY-123", "status": "paid", "amount": checkout.data["grand_total"]}).encode()
        signature = hmac.new(b"test-webhook-secret", payload, hashlib.sha256).hexdigest()
        paid = self.client.post("/api/v1/payments/marketplace/webhook/", payload, content_type="application/json", HTTP_X_MARKETPLACE_SIGNATURE=signature)
        self.assertEqual(paid.status_code, 200, paid.data)
        order = Order.objects.get(pk=order_id)
        self.assertTrue(order.invoice.number.startswith("INV-"))
        self.assertEqual(order.commission.commission_amount, Decimal("270.00"))
        self.assertEqual(ProductVariant.objects.get(pk=self.variant.pk).stock_quantity, 3)
