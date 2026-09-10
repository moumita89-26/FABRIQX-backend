from decimal import Decimal, InvalidOperation
import hashlib
import hmac
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Avg, Count, F, Q, Sum
from django.db.models.functions import TruncDay, TruncMonth, TruncWeek
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.mail import send_mail
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import generics, parsers, permissions, status, viewsets
from rest_framework.pagination import PageNumberPagination
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken, TokenError

from . import models as m
from .api_serializers import (
    AddressSerializer, AffiliateValidationSerializer, BrandLogoSerializer, BrandLogoSectionSerializer, CartItemSerializer, CartSerializer,
    CategorySerializer, CheckoutSerializer, ContactSubmissionSerializer, CouponApplySerializer, CustomerCouponSerializer, CustomerDashboardSerializer, CustomerProfileSerializer,
    EmptySerializer, FooterSocialSectionSerializer, ForgotPasswordSerializer, GiftSectionSerializer, HomepageResponseSerializer, InfluencerCommissionSerializer, InfluencerDashboardSerializer, InfluencerOrderSerializer, InfluencerProfileSerializer, InfluencerSalesSerializer, LoginSerializer, LogoutSerializer, MarketplaceWebhookSerializer, NewsletterSerializer, NewsletterSettingsSerializer, OfferBannerSerializer, OfferGridSectionSerializer, OrderReasonSerializer, OrderSerializer,
    PageSerializer, PaymentConfirmationSerializer, ProductDetailSerializer, ProductListSerializer, RefundRequestSerializer, SalesReportPointSerializer,
    ProductFAQSerializer, PublicReviewSerializer, RegistrationSerializer, ResetPasswordSerializer, ReviewCreateSerializer, CustomerReviewSerializer, VerifyEmailSerializer,
    ReviewSerializer, UserSerializer, WishlistSerializer,
)


class IsCustomer(permissions.BasePermission):
    message = "A customer account is required."

    def has_permission(self, request, view):
        return request.user.is_authenticated and hasattr(request.user, "customer_profile")


class ProductPagination(PageNumberPagination):
    """Storefront product pages honour the page size requested by the UI."""

    page_size_query_param = "page_size"
    max_page_size = 100


class IsInfluencer(permissions.BasePermission):
    message = "An influencer account is required."

    def has_permission(self, request, view):
        return (
            request.user.is_authenticated
            and hasattr(request.user, "influencer_profile")
            and request.user.influencer_profile.is_active
        )


class IsCustomerOrInfluencer(permissions.BasePermission):
    message = "A customer or influencer account is required."

    def has_permission(self, request, view):
        return request.user.is_authenticated and (
            hasattr(request.user, "customer_profile")
            or hasattr(request.user, "influencer_profile")
        )


INVALID_REFERRAL_STATUSES = (
    m.Order.Status.CANCELLED,
    m.Order.Status.RETURNED,
    m.Order.Status.REFUNDED,
)


def successful_referral_orders(influencer):
    return m.Order.objects.filter(
        influencer=influencer,
        payments__status=m.Payment.Status.PAID,
    ).exclude(status__in=INVALID_REFERRAL_STATUSES).distinct()


def valid_commissions(influencer):
    return influencer.commissions.exclude(
        status__in=(m.InfluencerCommission.Status.REJECTED, m.InfluencerCommission.Status.REVERSED)
    )


def finalize_paid_order(order, payment, transaction_id, provider_response):
    payment.transaction_id = transaction_id
    payment.gateway_response = provider_response
    payment.status = m.Payment.Status.PAID
    payment.paid_at = timezone.now()
    payment.save()
    order.status = m.Order.Status.CONFIRMED
    order.save(update_fields=("status", "updated_at"))
    m.Invoice.objects.get_or_create(order=order, defaults={"billing_details": order.billing_address})
    if order.influencer_id:
        influencer = order.influencer
        commission_settings = m.SiteSettings.load()
        eligible = max(order.subtotal - order.discount_total, Decimal("0"))
        if commission_settings.commission_type == m.SiteSettings.CommissionType.FIXED:
            rate, amount = Decimal("0"), commission_settings.commission_fixed_amount
        else:
            rate = commission_settings.commission_rate
            amount = (eligible * rate / Decimal("100")).quantize(Decimal("0.01"))
        commission, _ = m.InfluencerCommission.objects.get_or_create(order=order, defaults={
            "influencer": influencer, "eligible_amount": eligible, "rate": rate, "commission_amount": amount,
        })
        commission.full_clean()
        commission.save()
    m.CartItem.objects.filter(cart__customer=order.customer).delete()


@extend_schema(tags=["Authentication"])
class RegisterView(generics.CreateAPIView):
    permission_classes = (permissions.AllowAny,)
    serializer_class = RegistrationSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = default_token_generator.make_token(user)
        separator = "&" if "?" in settings.FRONTEND_VERIFY_EMAIL_URL else "?"
        verification_url = f"{settings.FRONTEND_VERIFY_EMAIL_URL}{separator}{urlencode({'uid': uid, 'token': token})}"
        send_mail(
            "Verify your FABRIQX email address",
            (
                f"Hello {user.get_full_name() or user.get_username()},\n\n"
                "Thank you for creating your FABRIQX account. Verify your email address to activate your account and log in:\n"
                f"{verification_url}\n\n"
                "If you did not create this account, you can safely ignore this email.\n\n"
                "Team FABRIQX"
            ),
            settings.DEFAULT_FROM_EMAIL,
            [user.email],
            html_message=render_to_string(
                "fabriqx/emails/verify_email.html",
                {"display_name": user.get_full_name() or user.get_username(), "verification_url": verification_url},
            ),
        )
        return Response(
            {
                "detail": "Account created. Verify your email address before logging in.",
                "user": UserSerializer(user).data,
            },
            status=status.HTTP_201_CREATED,
        )


@extend_schema(tags=["Authentication"], request=VerifyEmailSerializer)
class VerifyEmailView(APIView):
    permission_classes = (permissions.AllowAny,)
    authentication_classes = ()
    serializer_class = VerifyEmailSerializer

    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            user_id = force_str(urlsafe_base64_decode(serializer.validated_data["uid"]))
            user = get_user_model().objects.get(pk=user_id)
        except (TypeError, ValueError, OverflowError, get_user_model().DoesNotExist):
            return Response({"detail": "Invalid or expired verification link."}, status=status.HTTP_400_BAD_REQUEST)

        if not hasattr(user, "customer_profile") or not default_token_generator.check_token(user, serializer.validated_data["token"]):
            return Response({"detail": "Invalid or expired verification link."}, status=status.HTTP_400_BAD_REQUEST)
        if not user.customer_profile.email_verified:
            user.customer_profile.email_verified = True
            user.customer_profile.save(update_fields=("email_verified", "updated_at"))
        return Response({"detail": "Email verified successfully. You can now log in."})


@extend_schema(tags=["Authentication"], request=LoginSerializer)
class LoginView(APIView):
    permission_classes = (permissions.AllowAny,)
    serializer_class = LoginSerializer

    def post(self, request):
        serializer = LoginSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        refresh = RefreshToken.for_user(user)
        return Response({"access": str(refresh.access_token), "refresh": str(refresh), "user": UserSerializer(user, context={"request": request}).data})


@extend_schema(tags=["Authentication"])
class LogoutView(APIView):
    serializer_class = LogoutSerializer

    def post(self, request):
        serializer = LogoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            RefreshToken(serializer.validated_data["refresh"]).blacklist()
        except TokenError:
            return Response({"detail": "Invalid or expired refresh token."}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"detail": "Logged out successfully."})


@extend_schema(tags=["Authentication"], request=ForgotPasswordSerializer)
class ForgotPasswordView(APIView):
    permission_classes = (permissions.AllowAny,)
    authentication_classes = ()
    serializer_class = ForgotPasswordSerializer
    throttle_classes = (ScopedRateThrottle,)
    throttle_scope = "password_reset_request"

    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = get_user_model().objects.filter(email__iexact=serializer.validated_data["email"], is_active=True).first()
        if user and (hasattr(user, "customer_profile") or hasattr(user, "influencer_profile")):
            uid = urlsafe_base64_encode(force_bytes(user.pk))
            token = default_token_generator.make_token(user)
            account = "influencer" if hasattr(user, "influencer_profile") else "customer"
            separator = "&" if "?" in settings.FRONTEND_RESET_PASSWORD_URL else "?"
            reset_url = f"{settings.FRONTEND_RESET_PASSWORD_URL}{separator}{urlencode({'uid': uid, 'token': token, 'account': account})}"
            display_name = user.get_full_name() or user.get_username()
            send_mail(
                "Reset your FABRIQX password",
                (
                    f"Hello {display_name},\n\n"
                    "We received a request to reset your FABRIQX password.\n\n"
                    f"Use this secure link to choose a new password:\n{reset_url}\n\n"
                    "If you did not request this, you can safely ignore this email.\n\n"
                    "FABRIQX Team"
                ),
                settings.DEFAULT_FROM_EMAIL,
                [user.email],
                html_message=render_to_string(
                    "fabriqx/emails/password_reset.html",
                    {"display_name": display_name, "reset_url": reset_url},
                ),
            )
        return Response({"detail": "If a matching account exists, a password reset link has been sent."})


@extend_schema(tags=["Authentication"], request=ResetPasswordSerializer)
class ResetPasswordView(APIView):
    permission_classes = (permissions.AllowAny,)
    authentication_classes = ()
    serializer_class = ResetPasswordSerializer
    throttle_classes = (ScopedRateThrottle,)
    throttle_scope = "password_reset_confirm"

    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            user_id = force_str(urlsafe_base64_decode(serializer.validated_data["uid"]))
            user = get_user_model().objects.get(pk=user_id, is_active=True)
        except (ValueError, TypeError, OverflowError, get_user_model().DoesNotExist):
            user = None
        has_supported_profile = user and (hasattr(user, "customer_profile") or hasattr(user, "influencer_profile"))
        account = serializer.validated_data.get("account")
        account_matches = (
            not account
            or (account == "customer" and user and hasattr(user, "customer_profile"))
            or (account == "influencer" and user and hasattr(user, "influencer_profile"))
        )
        if not has_supported_profile or not account_matches or not default_token_generator.check_token(user, serializer.validated_data["token"]):
            return Response({"detail": "The reset link is invalid or has expired."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            validate_password(serializer.validated_data["new_password"], user=user)
        except DjangoValidationError as exc:
            return Response({"new_password": list(exc.messages)}, status=status.HTTP_400_BAD_REQUEST)
        user.set_password(serializer.validated_data["new_password"])
        user.save(update_fields=("password",))
        return Response({"detail": "Password reset successfully. You can now log in with the new password."})


@extend_schema(tags=["Storefront"])
class HomepageView(APIView):
    permission_classes = (permissions.AllowAny,)
    serializer_class = HomepageResponseSerializer

    def get(self, request):
        products = m.Product.objects.filter(status=m.Product.Status.ACTIVE).prefetch_related("images", "variants").select_related("category")
        banners = m.Banner.objects.filter(is_active=True).order_by("display_order", "id")
        gift_sections = m.GiftSection.objects.filter(is_active=True).prefetch_related("features", "statistics").order_by("display_order", "id")
        brand_logo_section = m.BrandLogoSection.objects.filter(is_active=True).prefetch_related("logos").first()
        offer_banners = m.OfferBanner.objects.filter(is_active=True).order_by("display_order", "id")
        offer_grid = m.OfferGridSection.objects.filter(is_active=True).prefetch_related("items").first()
        footer_social = m.FooterSocialSection.objects.filter(is_active=True).prefetch_related("links").first()
        newsletter_settings = m.NewsletterSettings.objects.first()
        section_settings = {
            section.section_type: {
                "id": section.id,
                "title": section.title,
                "content": section.content,
                "editor_content": section.editor_content,
                "display_order": section.display_order,
            }
            for section in m.HomepageSection.objects.filter(is_active=True)
        }
        return Response({
            "banners": [{"image": request.build_absolute_uri(x.image.url) if x.image else None, "link": x.link} for x in banners],
            "gift_sections": GiftSectionSerializer(gift_sections, many=True, context={"request": request}).data,
            "brand_logo_section": BrandLogoSectionSerializer(brand_logo_section, context={"request": request}).data if brand_logo_section else None,
            "offer_banners": OfferBannerSerializer(offer_banners, many=True, context={"request": request}).data,
            "offer_grid": OfferGridSectionSerializer(offer_grid, context={"request": request}).data if offer_grid else None,
            "footer_social": FooterSocialSectionSerializer(footer_social, context={"request": request}).data if footer_social else None,
            "newsletter_settings": NewsletterSettingsSerializer(newsletter_settings).data if newsletter_settings else None,
            "categories": CategorySerializer(m.Category.objects.filter(is_active=True, parent__isnull=True), many=True, context={"request": request}).data,
            "trending_products": ProductListSerializer(products.filter(is_trending=True), many=True, context={"request": request}).data,
            "new_arrivals": ProductListSerializer(products.filter(is_new_arrival=True), many=True, context={"request": request}).data,
            "featured_products": ProductListSerializer(products.filter(is_featured=True), many=True, context={"request": request}).data,
            "sections": list(m.HomepageSection.objects.filter(is_active=True).values(
                "id", "title", "section_type", "content", "editor_content", "display_order"
            )),
            "section_settings": section_settings,
            "testimonials": list(
                m.Testimonial.objects.filter(is_active=True).values(
                    "id",
                    "customer_name",
                    "image",
                    "sub_text",
                    "content",
                    "rating",
                )
            ),
        })


@extend_schema(tags=["Storefront"])
class NavigationView(APIView):
    permission_classes = (permissions.AllowAny,)
    serializer_class = CategorySerializer

    def get(self, request):
        roots = m.Category.objects.filter(is_active=True, parent__isnull=True).prefetch_related("children")
        return Response(CategorySerializer(roots, many=True, context={"request": request}).data)


@extend_schema(tags=["Storefront"])
class SubcategoryListView(APIView):
    """Public subcategory listing for a category landing page."""

    permission_classes = (permissions.AllowAny,)
    serializer_class = CategorySerializer

    def get(self, request, slug):
        parent = get_object_or_404(
            m.Category.objects.filter(is_active=True).prefetch_related("children"),
            slug=slug,
        )
        subcategories = [
            category for category in parent.children.all()
            if category.is_active
        ]
        return Response({
            "category": CategorySerializer(parent, context={"request": request}).data,
            "subcategories": CategorySerializer(subcategories, many=True, context={"request": request}).data,
        })


@extend_schema(tags=["Storefront"])
class HomepageCategoryListView(APIView):
    """Public category cards used by the homepage Shop By Category section."""

    permission_classes = (permissions.AllowAny,)
    serializer_class = CategorySerializer

    def get(self, request):
        categories = m.Category.objects.filter(
            is_active=True,
            parent__isnull=True,
        ).prefetch_related("children")
        return Response(CategorySerializer(categories, many=True, context={"request": request}).data)


@extend_schema(tags=["Storefront"])
class HomepageBannerListView(APIView):
    permission_classes = (permissions.AllowAny,)

    def get(self, request):
        banners = m.Banner.objects.filter(is_active=True).order_by("display_order", "id")
        return Response([
            {"image": request.build_absolute_uri(banner.image.url) if banner.image else None, "link": banner.link}
            for banner in banners
        ])


@extend_schema(tags=["Storefront"], parameters=[OpenApiParameter("collection", str, required=True, enum=["new_arrivals", "trending", "featured"])])
class HomepageProductCollectionView(APIView):
    permission_classes = (permissions.AllowAny,)
    serializer_class = ProductListSerializer

    collection_filters = {
        "new_arrivals": {"is_new_arrival": True},
        "trending": {"is_trending": True},
        "featured": {"is_featured": True},
    }

    def get(self, request):
        collection = request.query_params.get("collection")
        filters = self.collection_filters.get(collection)
        if filters is None:
            return Response({"collection": ["Use one of: new_arrivals, trending, featured."]}, status=status.HTTP_400_BAD_REQUEST)
        products = m.Product.objects.filter(status=m.Product.Status.ACTIVE, **filters).select_related("category").prefetch_related("images", "variants")
        return Response(ProductListSerializer(products, many=True, context={"request": request}).data)


@extend_schema(tags=["Storefront"])
class HomepageOffersView(APIView):
    permission_classes = (permissions.AllowAny,)

    def get(self, request):
        banners = m.OfferBanner.objects.filter(is_active=True).order_by("display_order", "id")
        grid = m.OfferGridSection.objects.filter(is_active=True).prefetch_related("items").first()
        return Response({
            "banners": OfferBannerSerializer(banners, many=True, context={"request": request}).data,
            "grid": OfferGridSectionSerializer(grid, context={"request": request}).data if grid else None,
        })


@extend_schema(tags=["Storefront"])
class HomepageBrandListView(APIView):
    """Public logo strip for the homepage brand carousel."""

    permission_classes = (permissions.AllowAny,)
    serializer_class = BrandLogoSerializer

    def get(self, request):
        logos = m.BrandLogo.objects.filter(
            is_active=True,
            section__is_active=True,
        ).select_related("section").order_by("display_order", "id")
        return Response(BrandLogoSerializer(logos, many=True, context={"request": request}).data)


@extend_schema(tags=["Storefront"])
class FooterSocialLinksView(APIView):
    """Public footer social-link block."""

    permission_classes = (permissions.AllowAny,)
    serializer_class = FooterSocialSectionSerializer

    def get(self, request):
        section = m.FooterSocialSection.objects.filter(is_active=True).prefetch_related("links").first()
        return Response(
            FooterSocialSectionSerializer(section, context={"request": request}).data if section else None
        )


@extend_schema(tags=["Storefront"])
class HomepageContentView(APIView):
    """Small CMS-only homepage blocks: section labels, testimonials and footer."""

    permission_classes = (permissions.AllowAny,)

    def get(self, request):
        sections = m.HomepageSection.objects.filter(is_active=True)
        settings_by_type = {
            section.section_type: {
                "id": section.id, "title": section.title, "content": section.content,
                "editor_content": section.editor_content, "display_order": section.display_order,
            }
            for section in sections
        }
        footer = m.FooterSocialSection.objects.filter(is_active=True).prefetch_related("links").first()
        newsletter = m.NewsletterSettings.objects.first()
        gift_sections = m.GiftSection.objects.filter(is_active=True).prefetch_related("features", "statistics").order_by("display_order", "id")
        brand_logos = m.BrandLogoSection.objects.filter(is_active=True).prefetch_related("logos").first()
        return Response({
            "section_settings": settings_by_type,
            "gift_sections": GiftSectionSerializer(gift_sections, many=True, context={"request": request}).data,
            "brand_logo_section": BrandLogoSectionSerializer(brand_logos, context={"request": request}).data if brand_logos else None,
            "testimonials": list(m.Testimonial.objects.filter(is_active=True).values("id", "customer_name", "image", "sub_text", "content", "rating")),
            "footer_social": FooterSocialSectionSerializer(footer, context={"request": request}).data if footer else None,
            "newsletter_settings": NewsletterSettingsSerializer(newsletter).data if newsletter else None,
        })


@extend_schema_view(list=extend_schema(tags=["Products"]), retrieve=extend_schema(tags=["Products"]))
class ProductViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = (permissions.AllowAny,)
    pagination_class = ProductPagination
    lookup_field = "slug"
    search_fields = ("name", "brand", "description", "category__name", "variants__sku")
    ordering_fields = ("name", "regular_price", "sale_price", "created_at")
    filterset_fields = ("category__slug", "category__audience", "brand", "is_trending", "is_new_arrival")

    @staticmethod
    def _query_values(query_params, *names):
        """Return unique values from repeated and comma-separated parameters."""
        values = []
        for name in names:
            for raw_value in query_params.getlist(name):
                values.extend(value.strip() for value in raw_value.split(",") if value.strip())
        return list(dict.fromkeys(values))

    @staticmethod
    def _category_ids_including_descendants(slugs):
        """A selected parent category also includes every child category."""
        categories = list(m.Category.objects.filter(slug__in=slugs, is_active=True).values("id", "parent_id"))
        category_ids = {category["id"] for category in categories}
        frontier = set(category_ids)
        while frontier:
            child_ids = set(
                m.Category.objects.filter(parent_id__in=frontier, is_active=True).values_list("id", flat=True)
            ) - category_ids
            category_ids.update(child_ids)
            frontier = child_ids
        return category_ids

    def get_queryset(self):
        queryset = m.Product.objects.filter(status=m.Product.Status.ACTIVE).select_related("category").prefetch_related("images", "variants", "reviews__customer__user", "faqs", "related_products__images", "related_products__variants", "related_products__category").distinct()
        query_params = self.request.query_params
        category_slugs = self._query_values(query_params, "categories", "category")
        colors = self._query_values(query_params, "colors", "color")
        sizes = self._query_values(query_params, "sizes", "size")
        query = query_params.get("q", "").strip()
        minimum = query_params.get("min_price", query_params.get("price_min"))
        maximum = query_params.get("max_price", query_params.get("price_max"))
        in_stock = self.request.query_params.get("in_stock")

        if query:
            queryset = queryset.filter(
                Q(name__icontains=query)
                | Q(brand__icontains=query)
                | Q(description__icontains=query)
                | Q(short_description__icontains=query)
                | Q(category__name__icontains=query)
                | Q(variants__sku__icontains=query)
            )
        if category_slugs:
            queryset = queryset.filter(category_id__in=self._category_ids_including_descendants(category_slugs))
        variant_filter = Q()
        has_variant_filter = False
        if colors:
            color_filter = Q()
            for color in colors:
                color_filter |= Q(variants__color__iexact=color)
            variant_filter &= color_filter
            has_variant_filter = True
        if sizes:
            size_filter = Q()
            for size in sizes:
                size_filter |= Q(variants__size__iexact=size)
            variant_filter &= size_filter
            has_variant_filter = True
        if has_variant_filter:
            # Apply color and size in one relation join, so Black + M means a
            # purchasable Black/M variant rather than two unrelated variants.
            queryset = queryset.filter(variant_filter, variants__is_active=True)

        try:
            minimum_value = Decimal(minimum) if minimum not in (None, "") else None
            maximum_value = Decimal(maximum) if maximum not in (None, "") else None
        except (InvalidOperation, ValueError):
            raise DRFValidationError({"price": "min_price and max_price must be valid numbers."})
        if minimum_value is not None and maximum_value is not None and minimum_value > maximum_value:
            raise DRFValidationError({"price": "min_price cannot be greater than max_price."})
        if minimum_value is not None:
            queryset = queryset.filter(Q(sale_price__gte=minimum_value) | Q(sale_price__isnull=True, regular_price__gte=minimum_value))
        if maximum_value is not None:
            queryset = queryset.filter(Q(sale_price__lte=maximum_value) | Q(sale_price__isnull=True, regular_price__lte=maximum_value))
        if in_stock in ("true", "1"):
            queryset = queryset.filter(variants__stock_quantity__gt=0, variants__is_active=True)
        return queryset

    def get_serializer_class(self):
        return ProductDetailSerializer if self.action == "retrieve" else ProductListSerializer

    @extend_schema(
        tags=["Products"],
        parameters=[
            OpenApiParameter("q", str, description="Search name, brand, category, description, or SKU."),
            OpenApiParameter("categories", str, description="Comma-separated category slugs. Parent categories include their children."),
            OpenApiParameter("colors", str, description="Comma-separated variant colors, e.g. Black,Blue."),
            OpenApiParameter("sizes", str, description="Comma-separated variant sizes, e.g. XS,S,M."),
            OpenApiParameter("min_price", Decimal, description="Inclusive lowest effective product price."),
            OpenApiParameter("max_price", Decimal, description="Inclusive highest effective product price."),
            OpenApiParameter("in_stock", bool, description="Set true to return products with available variants."),
        ],
    )
    @action(detail=False, methods=("get",), url_path="search")
    def search(self, request):
        """Filtered product listing for the storefront search-and-filter UI."""
        return self.list(request)

    @extend_schema(tags=["Products"])
    @action(detail=False, methods=("get",), url_path="filter-options")
    def filter_options(self, request):
        """Dynamic filter values used by the storefront product listing page."""
        products = m.Product.objects.filter(status=m.Product.Status.ACTIVE)
        variants = m.ProductVariant.objects.filter(product__status=m.Product.Status.ACTIVE, is_active=True)
        prices = [
            sale if sale is not None and sale < regular else regular
            for regular, sale in products.values_list("regular_price", "sale_price")
        ]
        roots = m.Category.objects.filter(is_active=True, parent__isnull=True).prefetch_related("children")
        return Response({
            "categories": CategorySerializer(roots, many=True, context={"request": request}).data,
            "colors": [
                {"name": row[0], "code": row[1] or ""}
                for row in variants.exclude(color="").values_list("color", "color_code").distinct().order_by("color")
            ],
            "sizes": list(variants.exclude(size="").values_list("size", flat=True).distinct().order_by("size")),
            "price": {
                "min": float(min(prices)) if prices else 0,
                "max": float(max(prices)) if prices else 0,
            },
        })

    @extend_schema(tags=["Products"], parameters=[OpenApiParameter("q", str, required=True)])
    @action(detail=False, methods=("get",), url_path="suggestions")
    def suggestions(self, request):
        query = request.query_params.get("q", "").strip()
        if len(query) < 2:
            return Response([])
        products = self.get_queryset().filter(Q(name__icontains=query) | Q(brand__icontains=query) | Q(category__name__icontains=query))[:10]
        return Response([{"name": item.name, "slug": item.slug, "brand": item.brand, "category": item.category.name} for item in products])

    @extend_schema(tags=["Products"], parameters=[OpenApiParameter("pincode", str, required=True)])
    @action(detail=True, methods=("get",), url_path="delivery-availability")
    def delivery_availability(self, request, slug=None):
        pincode = request.query_params.get("pincode", "")
        service = m.ServiceablePincode.objects.filter(pincode=pincode, is_active=True).first()
        if not service:
            return Response({"serviceable": False, "pincode": pincode})
        return Response({"serviceable": True, "pincode": pincode, "city": service.city, "state": service.state, "cash_on_delivery": service.cash_on_delivery, "estimated_days": service.estimated_days})

    @extend_schema(tags=["Products"], responses=ProductFAQSerializer(many=True))
    @action(detail=True, methods=("get",), url_path="faqs")
    def faqs(self, request, slug=None):
        product = self.get_object()
        faqs = product.faqs.filter(is_active=True)
        return Response(ProductFAQSerializer(faqs, many=True, context={"request": request}).data)


@extend_schema(tags=["Customer Account"], request=CustomerProfileSerializer)
class ProfileView(generics.RetrieveUpdateAPIView):
    permission_classes = (IsCustomer,)
    serializer_class = CustomerProfileSerializer
    parser_classes = (parsers.MultiPartParser, parsers.FormParser, parsers.JSONParser)

    def get_object(self):
        return self.request.user.customer_profile


@extend_schema(tags=["Customer Account"], responses=CustomerDashboardSerializer)
class CustomerDashboardView(APIView):
    permission_classes = (IsCustomer,)

    def get(self, request):
        customer = request.user.customer_profile
        orders = (
            m.Order.objects.filter(customer=customer)
            .prefetch_related("items__variant__product__images")
            .order_by("-placed_at")[:3]
        )
        recent_orders = []
        for order in orders:
            item = next(iter(order.items.all()), None)
            image = next(iter(item.variant.product.images.all()), None) if item else None
            recent_orders.append({
                "id": order.id,
                "order_number": order.number,
                "created_at": order.placed_at.date().isoformat(),
                "status": order.status,
                "status_display": order.get_status_display(),
                "product_name": item.product_name if item else None,
                "product_image": image.image.url if image else None,
                "total": str(order.grand_total),
            })

        now = timezone.now()
        available_coupons = m.Coupon.objects.filter(is_active=True, starts_at__lte=now, expires_at__gte=now)
        return Response({
            "customer": {
                "name": request.user.get_full_name() or request.user.get_username(),
                "email": request.user.email,
                "phone": customer.phone,
                "email_verified": customer.email_verified,
                "member_since": request.user.date_joined.strftime("%b %Y"),
                "profile_image": (
                    request.build_absolute_uri(customer.profile_image.url)
                    if customer.profile_image else None
                ),
            },
            "summary": {
                "orders_count": customer.orders.count(),
                "wishlist_count": customer.wishlist_items.count(),
                "addresses_count": customer.addresses.count(),
                "reviews_count": customer.reviews.count(),
                "coupons_count": sum(
                    coupon.usages.filter(customer=customer).count() < coupon.per_customer_limit
                    for coupon in available_coupons
                ),
            },
            "recent_orders": recent_orders,
        })


@extend_schema(tags=["Customer Account"])
class CustomerCouponListView(generics.ListAPIView):
    permission_classes = (IsCustomer,)
    serializer_class = CustomerCouponSerializer

    def get_queryset(self):
        now = timezone.now()
        return m.Coupon.objects.filter(is_active=True, starts_at__lte=now, expires_at__gte=now).order_by("expires_at", "code")


@extend_schema_view(list=extend_schema(tags=["Customer Account"]), retrieve=extend_schema(tags=["Customer Account"]), partial_update=extend_schema(tags=["Customer Account"]), destroy=extend_schema(tags=["Customer Account"]))
class CustomerReviewViewSet(viewsets.ModelViewSet):
    permission_classes = (IsCustomer,)
    serializer_class = CustomerReviewSerializer
    http_method_names = ("get", "patch", "delete", "head", "options")

    def get_queryset(self):
        return m.Review.objects.filter(customer=self.request.user.customer_profile).select_related("product").prefetch_related("product__images")


@extend_schema_view(list=extend_schema(tags=["Customer Account"]), create=extend_schema(tags=["Customer Account"]), retrieve=extend_schema(tags=["Customer Account"]), update=extend_schema(tags=["Customer Account"]), partial_update=extend_schema(tags=["Customer Account"]), destroy=extend_schema(tags=["Customer Account"]))
class AddressViewSet(viewsets.ModelViewSet):
    permission_classes = (IsCustomer,)
    serializer_class = AddressSerializer
    queryset = m.Address.objects.none()

    def get_queryset(self):
        queryset = m.Address.objects.filter(customer=self.request.user.customer_profile)
        address_type = self.request.query_params.get("address_type")
        if address_type:
            if address_type not in m.Address.Type.values:
                raise DRFValidationError({"address_type": "Use 'shipping' or 'billing'."})
            queryset = queryset.filter(address_type=address_type)
        return queryset.order_by("-is_default", "-updated_at")

    @transaction.atomic
    def perform_create(self, serializer):
        customer = self.request.user.customer_profile
        address = serializer.save(customer=customer)
        if address.is_default:
            customer.addresses.filter(address_type=address.address_type).exclude(pk=address.pk).update(is_default=False)

    @transaction.atomic
    def perform_update(self, serializer):
        address = serializer.save()
        if address.is_default:
            address.customer.addresses.filter(address_type=address.address_type).exclude(pk=address.pk).update(is_default=False)


@extend_schema_view(list=extend_schema(tags=["Wishlist"]), create=extend_schema(tags=["Wishlist"]), destroy=extend_schema(tags=["Wishlist"]))
class WishlistViewSet(viewsets.ModelViewSet):
    permission_classes = (IsCustomer,)
    serializer_class = WishlistSerializer
    queryset = m.WishlistItem.objects.none()
    http_method_names = ("get", "post", "delete", "head", "options")

    def get_queryset(self):
        return m.WishlistItem.objects.filter(customer=self.request.user.customer_profile).select_related("product", "variant", "product__category").prefetch_related("product__images", "product__variants")

    def perform_create(self, serializer):
        serializer.save(customer=self.request.user.customer_profile)


@extend_schema(tags=["Cart"])
class CartView(APIView):
    permission_classes = (IsCustomer,)
    serializer_class = CartSerializer

    def get_cart(self, request):
        return m.Cart.objects.prefetch_related("items__variant__product__images", "items__variant__product__variants").get_or_create(customer=request.user.customer_profile)[0]

    def get(self, request):
        return Response(CartSerializer(self.get_cart(request), context={"request": request}).data)

    @extend_schema(request=CartItemSerializer)
    def post(self, request):
        cart = self.get_cart(request)
        serializer = CartItemSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        item, created = m.CartItem.objects.get_or_create(cart=cart, variant=serializer.validated_data["variant"], defaults={"quantity": serializer.validated_data.get("quantity", 1)})
        if not created:
            item.quantity += serializer.validated_data.get("quantity", 1)
            if item.quantity > item.variant.stock_quantity:
                return Response({"quantity": ["Requested quantity is not available."]}, status=status.HTTP_400_BAD_REQUEST)
            item.save()
        return Response(CartSerializer(cart, context={"request": request}).data, status=status.HTTP_201_CREATED)


@extend_schema(tags=["Cart"])
class CartItemView(APIView):
    permission_classes = (IsCustomer,)
    serializer_class = CartItemSerializer

    def get_item(self, request, pk):
        return get_object_or_404(m.CartItem, pk=pk, cart__customer=request.user.customer_profile)

    @extend_schema(request=CartItemSerializer)
    def patch(self, request, pk):
        item = self.get_item(request, pk)
        serializer = CartItemSerializer(item, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def delete(self, request, pk):
        self.get_item(request, pk).delete()
        return Response({"detail": "Cart item removed successfully."})


@extend_schema(tags=["Cart"], request=CouponApplySerializer)
class CartCouponView(APIView):
    permission_classes = (IsCustomer,)
    serializer_class = CouponApplySerializer

    def post(self, request):
        serializer = CouponApplySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        now = timezone.now()
        coupon = get_object_or_404(m.Coupon, code__iexact=serializer.validated_data["code"], is_active=True, starts_at__lte=now, expires_at__gte=now)
        customer = request.user.customer_profile
        if coupon.usages.filter(customer=customer).count() >= coupon.per_customer_limit:
            return Response({"detail": "Coupon usage limit reached."}, status=status.HTTP_400_BAD_REQUEST)
        cart, _ = m.Cart.objects.get_or_create(customer=customer)
        cart.coupon = coupon
        cart.save(update_fields=("coupon", "updated_at"))
        return Response(CartSerializer(cart, context={"request": request}).data)

    def delete(self, request):
        cart, _ = m.Cart.objects.get_or_create(customer=request.user.customer_profile)
        cart.coupon = None
        cart.save(update_fields=("coupon", "updated_at"))
        return Response(CartSerializer(cart, context={"request": request}).data)


@extend_schema(tags=["Checkout"], request=AffiliateValidationSerializer)
class AffiliateValidationView(APIView):
    permission_classes = (permissions.AllowAny,)
    serializer_class = AffiliateValidationSerializer

    def post(self, request):
        serializer = AffiliateValidationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        influencer = m.InfluencerProfile.objects.filter(affiliate_id__iexact=serializer.validated_data["affiliate_id"].strip(), is_active=True).first()
        if not influencer:
            return Response({"valid": False, "detail": "Invalid or inactive affiliate ID."}, status=status.HTTP_400_BAD_REQUEST)
        now = timezone.now()
        coupon = influencer.coupons.filter(is_active=True, starts_at__lte=now, expires_at__gte=now).order_by("-discount_value").first()
        coupon_data = None
        if coupon:
            coupon_data = {"code": coupon.code, "discount_type": coupon.discount_type, "discount_value": coupon.discount_value, "maximum_discount": coupon.maximum_discount}
        return Response({"valid": True, "affiliate_id": influencer.affiliate_id, "coupon": coupon_data})


@extend_schema(tags=["Checkout"], request=CheckoutSerializer, responses={201: OrderSerializer})
class CheckoutView(APIView):
    permission_classes = (IsCustomer,)

    def post(self, request):
        serializer = CheckoutSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        order = serializer.save()
        return Response(OrderSerializer(order).data, status=status.HTTP_201_CREATED)


@extend_schema_view(list=extend_schema(tags=["Orders"]), retrieve=extend_schema(tags=["Orders"]))
class OrderViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = (IsCustomer,)
    serializer_class = OrderSerializer
    queryset = m.Order.objects.none()

    def get_queryset(self):
        return m.Order.objects.filter(customer=self.request.user.customer_profile).prefetch_related("items__variant__product__images", "payments").select_related("invoice")

    @extend_schema(tags=["Checkout"], request=None)
    @action(detail=True, methods=("post",), url_path="initialize-payment")
    def initialize_payment(self, request, pk=None):
        order = self.get_object()
        payment = order.payments.order_by("-created_at").first()
        if not payment:
            return Response({"detail": "Payment was not initialized."}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"order_number": order.number, "amount": payment.amount, "currency": "INR", "provider": payment.provider, "access_key": settings.MARKETPLACE_ACCESS_KEY, "checkout_url": settings.MARKETPLACE_CHECKOUT_URL})

    @extend_schema(tags=["Orders"], request=OrderReasonSerializer, responses=OrderSerializer)
    @action(detail=True, methods=("post",), url_path="cancel")
    @transaction.atomic
    def cancel(self, request, pk=None):
        order = self.get_object()
        serializer = OrderReasonSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if order.status not in (m.Order.Status.PENDING, m.Order.Status.CONFIRMED):
            return Response({"detail": "This order can no longer be cancelled."}, status=status.HTTP_400_BAD_REQUEST)
        for item in order.items.all():
            m.ProductVariant.objects.filter(pk=item.variant_id).update(stock_quantity=F("stock_quantity") + item.quantity)
        previous = order.status
        order.status = m.Order.Status.CANCELLED
        order.customer_note = serializer.validated_data["reason"]
        order.save(update_fields=("status", "customer_note", "updated_at"))
        m.OrderStatusHistory.objects.create(order=order, from_status=previous, to_status=order.status, note=order.customer_note, changed_by=request.user)
        return Response(OrderSerializer(order).data)

    @extend_schema(tags=["Orders"], request=OrderReasonSerializer, responses=OrderSerializer)
    @action(detail=True, methods=("post",), url_path="request-return")
    def request_return(self, request, pk=None):
        order = self.get_object()
        serializer = OrderReasonSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if order.status != m.Order.Status.DELIVERED:
            return Response({"detail": "Only delivered orders can be returned."}, status=status.HTTP_400_BAD_REQUEST)
        order.status = m.Order.Status.RETURN_REQUESTED
        order.customer_note = serializer.validated_data["reason"]
        order.save(update_fields=("status", "customer_note", "updated_at"))
        return Response(OrderSerializer(order).data)

    @extend_schema(tags=["Orders"], request=RefundRequestSerializer)
    @action(detail=True, methods=("post",), url_path="request-refund")
    def request_refund(self, request, pk=None):
        order = self.get_object()
        serializer = RefundRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payment = order.payments.filter(status=m.Payment.Status.PAID).order_by("-paid_at").first()
        if not payment or order.status not in (m.Order.Status.RETURNED, m.Order.Status.DELIVERED):
            return Response({"detail": "A paid delivered or returned order is required."}, status=status.HTTP_400_BAD_REQUEST)
        amount = serializer.validated_data.get("amount", payment.amount)
        if amount > payment.amount:
            return Response({"amount": ["Refund cannot exceed the paid amount."]}, status=status.HTTP_400_BAD_REQUEST)
        refund = m.Refund.objects.create(payment=payment, amount=amount, reason=serializer.validated_data["reason"])
        return Response({"id": refund.pk, "status": refund.status, "amount": refund.amount}, status=status.HTTP_201_CREATED)


@extend_schema(tags=["Checkout"], request=MarketplaceWebhookSerializer)
class MarketplaceWebhookView(APIView):
    permission_classes = (permissions.AllowAny,)
    authentication_classes = ()
    serializer_class = MarketplaceWebhookSerializer

    @transaction.atomic
    def post(self, request):
        secret = settings.MARKETPLACE_WEBHOOK_SECRET
        signature = request.headers.get("X-Marketplace-Signature", "")
        expected = hmac.new(secret.encode(), request.body, hashlib.sha256).hexdigest() if secret else ""
        if not secret or not hmac.compare_digest(signature, expected):
            return Response({"detail": "Invalid webhook signature."}, status=status.HTTP_401_UNAUTHORIZED)
        serializer = MarketplaceWebhookSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        order = get_object_or_404(m.Order.objects.select_for_update(), number=serializer.validated_data["order_number"])
        payment = order.payments.select_for_update().order_by("-created_at").first()
        if not payment or serializer.validated_data["amount"] != payment.amount:
            return Response({"detail": "Payment amount mismatch."}, status=status.HTTP_400_BAD_REQUEST)
        if serializer.validated_data["status"] == "paid":
            finalize_paid_order(order, payment, serializer.validated_data["transaction_id"], request.data)
        else:
            payment.status = m.Payment.Status.FAILED
            payment.gateway_response = request.data
            payment.save(update_fields=("status", "gateway_response", "updated_at"))
        return Response({"accepted": True})


@extend_schema(tags=["Reviews & Ratings"])
class ReviewCreateView(generics.ListCreateAPIView):
    """List approved storefront reviews and let signed-in customers submit one."""

    serializer_class = PublicReviewSerializer

    def get_permissions(self):
        return (IsCustomer(),) if self.request.method == "POST" else (permissions.AllowAny(),)

    def get_queryset(self):
        queryset = m.Review.objects.filter(status=m.Review.Status.APPROVED).select_related(
            "product", "customer__user"
        )
        product_id = self.request.query_params.get("product")
        product_slug = self.request.query_params.get("product_slug")
        if product_id:
            try:
                product_id = int(product_id)
            except (TypeError, ValueError):
                raise DRFValidationError({"product": "product must be a valid product ID."})
            queryset = queryset.filter(product_id=product_id)
        if product_slug:
            queryset = queryset.filter(product__slug=product_slug)
        return queryset

    def get_serializer_class(self):
        return ReviewCreateSerializer if self.request.method == "POST" else PublicReviewSerializer

    def perform_create(self, serializer):
        # The customer profile links the review to request.user; reviewer
        # identity is never accepted from request data.
        serializer.save(customer=self.request.user.customer_profile, is_verified_purchase=False)


@extend_schema(tags=["Storefront"])
class NewsletterView(generics.CreateAPIView):
    permission_classes = (permissions.AllowAny,)
    serializer_class = NewsletterSerializer

    def perform_create(self, serializer):
        subscription = serializer.save()
        send_mail(
            "Welcome to FABRIQX!",
            (
                "Welcome to FABRIQX!\n\n"
                "Thank you for subscribing to our newsletter.\n\n"
                "You're now on the list to receive updates about our latest arrivals, "
                "exclusive offers, special promotions, and exciting collections.\n\n"
                "Stay tuned — something special is always coming your way.\n\n"
                "Best regards,\nTeam FABRIQX"
            ),
            settings.DEFAULT_FROM_EMAIL,
            [subscription.email],
            html_message=render_to_string("fabriqx/emails/newsletter_welcome.html"),
        )


@extend_schema(tags=["Storefront"])
class PageDetailView(generics.RetrieveAPIView):
    permission_classes = (permissions.AllowAny,)
    authentication_classes = ()
    serializer_class = PageSerializer
    lookup_field = "slug"
    queryset = m.Page.objects.filter(is_active=True)


@extend_schema(tags=["Storefront"], request=ContactSubmissionSerializer)
class ContactSubmissionView(generics.CreateAPIView):
    """Public contact form. Submitted messages are managed in CMS → Contacts."""

    permission_classes = (permissions.AllowAny,)
    authentication_classes = ()
    serializer_class = ContactSubmissionSerializer
    throttle_classes = (ScopedRateThrottle,)
    throttle_scope = "contact_submission"


@extend_schema(tags=["Influencer Dashboard"])
class InfluencerProfileView(generics.RetrieveUpdateAPIView):
    permission_classes = (IsInfluencer,)
    serializer_class = InfluencerProfileSerializer

    def get_object(self):
        return self.request.user.influencer_profile


@extend_schema(tags=["Influencer Dashboard"])
class InfluencerOrdersView(generics.ListAPIView):
    permission_classes = (IsInfluencer,)
    serializer_class = InfluencerOrderSerializer
    queryset = m.Order.objects.none()

    def get_queryset(self):
        return m.Order.objects.filter(influencer=self.request.user.influencer_profile).prefetch_related("items", "payments").select_related("invoice")


@extend_schema(tags=["Influencer Dashboard"])
class InfluencerOrderDetailView(generics.RetrieveAPIView):
    permission_classes = (IsInfluencer,)
    serializer_class = InfluencerOrderSerializer
    queryset = m.Order.objects.none()

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return self.queryset
        return m.Order.objects.filter(influencer=self.request.user.influencer_profile).prefetch_related("items", "payments")


@extend_schema(tags=["Influencer Dashboard"])
class InfluencerCommissionListView(generics.ListAPIView):
    permission_classes = (IsInfluencer,)
    serializer_class = InfluencerCommissionSerializer
    queryset = m.InfluencerCommission.objects.none()

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return self.queryset
        return self.request.user.influencer_profile.commissions.select_related("order").order_by("-created_at")


@extend_schema(tags=["Influencer Dashboard"])
class InfluencerDashboardView(APIView):
    permission_classes = (IsInfluencer,)
    serializer_class = InfluencerDashboardSerializer

    def get(self, request):
        influencer = request.user.influencer_profile
        all_orders = m.Order.objects.filter(influencer=influencer)
        successful = successful_referral_orders(influencer)
        commission_summary = valid_commissions(influencer).aggregate(
            total=Sum("commission_amount"),
            pending=Sum("commission_amount", filter=Q(status=m.InfluencerCommission.Status.PENDING)),
            approved=Sum("commission_amount", filter=Q(status=m.InfluencerCommission.Status.APPROVED)),
            paid=Sum("commission_amount", filter=Q(status=m.InfluencerCommission.Status.PAID)),
        )
        recent_orders = all_orders.prefetch_related("items", "payments")[:5]
        return Response({
            "affiliate_id": influencer.affiliate_id,
            "total_referred_orders": all_orders.count(),
            "successful_orders": successful.count(),
            "total_sales": successful.aggregate(value=Sum("grand_total"))["value"] or Decimal("0"),
            "total_commission": commission_summary["total"] or Decimal("0"),
            "pending_commission": commission_summary["pending"] or Decimal("0"),
            "approved_commission": commission_summary["approved"] or Decimal("0"),
            "paid_commission": commission_summary["paid"] or Decimal("0"),
            "recent_orders": InfluencerOrderSerializer(recent_orders, many=True).data,
        })


@extend_schema(tags=["Influencer Dashboard"])
class InfluencerSalesView(APIView):
    permission_classes = (IsInfluencer,)
    serializer_class = InfluencerSalesSerializer

    def get(self, request):
        influencer = request.user.influencer_profile
        commission_settings = m.SiteSettings.load()
        period = request.query_params.get("period", "month")
        truncation = {"day": TruncDay, "week": TruncWeek, "month": TruncMonth}.get(period)
        if truncation is None:
            return Response({"period": ["Choose day, week, or month."]}, status=status.HTTP_400_BAD_REQUEST)
        orders = successful_referral_orders(influencer)
        summary = orders.aggregate(total_orders=Count("id"), total_sales=Sum("grand_total"))
        commissions = valid_commissions(influencer).aggregate(
            total_commission=Sum("commission_amount"),
            pending_commission=Sum("commission_amount", filter=Q(status=m.InfluencerCommission.Status.PENDING)),
            approved_commission=Sum("commission_amount", filter=Q(status=m.InfluencerCommission.Status.APPROVED)),
            paid_commission=Sum("commission_amount", filter=Q(status=m.InfluencerCommission.Status.PAID)),
        )
        series = list(orders.annotate(period=truncation("placed_at")).values("period").annotate(
            orders=Count("id", distinct=True), sales=Sum("grand_total"),
        ).order_by("period"))
        recent_cutoff = timezone.now() - timezone.timedelta(days=30)
        return Response({
            "affiliate_id": influencer.affiliate_id,
            "total_orders": summary["total_orders"] or 0,
            "successful_orders": summary["total_orders"] or 0,
            "total_sales": summary["total_sales"] or Decimal("0"),
            "recent_sales": orders.filter(placed_at__gte=recent_cutoff).aggregate(value=Sum("grand_total"))["value"] or Decimal("0"),
            "commission_type": commission_settings.commission_type,
            "commission_rate": commission_settings.commission_rate,
            "commission_fixed_amount": commission_settings.commission_fixed_amount,
            "total_commission": commissions["total_commission"] or Decimal("0"),
            "pending_commission": commissions["pending_commission"] or Decimal("0"),
            "approved_commission": commissions["approved_commission"] or Decimal("0"),
            "paid_commission": commissions["paid_commission"] or Decimal("0"),
            "period": period,
            "series": series,
        })


@extend_schema(tags=["Admin Reports"], responses=SalesReportPointSerializer(many=True), parameters=[OpenApiParameter("period", str, enum=("day", "week", "month"))])
class SalesAggregationView(APIView):
    permission_classes = (permissions.IsAdminUser,)
    serializer_class = SalesReportPointSerializer

    def get(self, request):
        period = request.query_params.get("period", "day")
        trunc = {"day": TruncDay, "week": TruncWeek, "month": TruncMonth}.get(period)
        if not trunc:
            return Response({"period": ["Choose day, week or month."]}, status=status.HTTP_400_BAD_REQUEST)
        rows = m.Order.objects.exclude(status=m.Order.Status.CANCELLED).annotate(period=trunc("placed_at")).values("period").annotate(
            orders=Count("id"), gross_sales=Sum("subtotal"), discounts=Sum("discount_total"), net_sales=Sum("grand_total")
        ).order_by("period")
        return Response(list(rows))
