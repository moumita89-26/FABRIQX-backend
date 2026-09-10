from decimal import Decimal
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
from rest_framework import generics, permissions, status, viewsets
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken, TokenError

from . import models as m
from .api_serializers import (
    AddressSerializer, AffiliateValidationSerializer, BrandLogoSectionSerializer, CartItemSerializer, CartSerializer,
    CategorySerializer, ChangePasswordSerializer, CheckoutSerializer, CouponApplySerializer, CustomerProfileSerializer,
    EmptySerializer, FooterSocialSectionSerializer, ForgotPasswordSerializer, GiftSectionSerializer, HomepageResponseSerializer, InfluencerCommissionSerializer, InfluencerDashboardSerializer, InfluencerOrderSerializer, InfluencerProfileSerializer, InfluencerSalesSerializer, LoginSerializer, LogoutSerializer, MarketplaceWebhookSerializer, NewsletterSerializer, NewsletterSettingsSerializer, OfferBannerSerializer, OfferGridSectionSerializer, OrderReasonSerializer, OrderSerializer,
    PageSerializer, PaymentConfirmationSerializer, ProductDetailSerializer, ProductListSerializer, RefundRequestSerializer, SalesReportPointSerializer,
    ProductQuestionSerializer, RegistrationSerializer, ResetPasswordSerializer, ReviewCreateSerializer,
    ReviewSerializer, UserSerializer, WishlistSerializer,
)


class IsCustomer(permissions.BasePermission):
    message = "A customer account is required."

    def has_permission(self, request, view):
        return request.user.is_authenticated and hasattr(request.user, "customer_profile")


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
        refresh = RefreshToken.for_user(user)
        return Response({"access": str(refresh.access_token), "refresh": str(refresh), "user": UserSerializer(user).data}, status=status.HTTP_201_CREATED)


@extend_schema(tags=["Authentication"], request=LoginSerializer)
class LoginView(APIView):
    permission_classes = (permissions.AllowAny,)
    serializer_class = LoginSerializer

    def post(self, request):
        serializer = LoginSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        refresh = RefreshToken.for_user(user)
        return Response({"access": str(refresh.access_token), "refresh": str(refresh), "user": UserSerializer(user).data})


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


@extend_schema(tags=["Authentication"], request=ChangePasswordSerializer)
class ChangePasswordView(APIView):
    permission_classes = (IsCustomerOrInfluencer,)
    serializer_class = ChangePasswordSerializer

    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        if not request.user.check_password(serializer.validated_data["current_password"]):
            return Response(
                {"current_password": ["The current password is incorrect."]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            validate_password(serializer.validated_data["new_password"], user=request.user)
        except DjangoValidationError as exc:
            return Response({"new_password": list(exc.messages)}, status=status.HTTP_400_BAD_REQUEST)
        request.user.set_password(serializer.validated_data["new_password"])
        request.user.save(update_fields=("password",))
        return Response({"detail": "Password changed successfully. Please log in again."})


@extend_schema(tags=["Storefront"])
class HomepageView(APIView):
    permission_classes = (permissions.AllowAny,)
    serializer_class = HomepageResponseSerializer

    def get(self, request):
        now = timezone.now()
        products = m.Product.objects.filter(status=m.Product.Status.ACTIVE).prefetch_related("images", "variants").select_related("category")
        banners = m.Banner.objects.filter(is_active=True).filter(Q(starts_at__isnull=True) | Q(starts_at__lte=now)).filter(Q(ends_at__isnull=True) | Q(ends_at__gte=now))
        gift_sections = m.GiftSection.objects.filter(is_active=True).filter(Q(starts_at__isnull=True) | Q(starts_at__lte=now)).filter(Q(ends_at__isnull=True) | Q(ends_at__gte=now)).prefetch_related("features", "statistics").order_by("display_order", "id")[:1]
        brand_logo_section = m.BrandLogoSection.objects.filter(is_active=True).prefetch_related("logos").first()
        offer_banners = m.OfferBanner.objects.filter(is_active=True).filter(Q(starts_at__isnull=True) | Q(starts_at__lte=now)).filter(Q(ends_at__isnull=True) | Q(ends_at__gte=now))
        offer_grid = m.OfferGridSection.objects.filter(is_active=True).prefetch_related("items").first()
        footer_social = m.FooterSocialSection.objects.filter(is_active=True).prefetch_related("links").first()
        newsletter_settings = m.NewsletterSettings.objects.first()
        return Response({
            "banners": [{"id": x.id, "title": x.title, "subtitle": x.subtitle, "image": request.build_absolute_uri(x.image.url) if x.image else None, "link": x.link} for x in banners],
            "gift_sections": GiftSectionSerializer(gift_sections, many=True, context={"request": request}).data,
            "brand_logo_section": BrandLogoSectionSerializer(brand_logo_section, context={"request": request}).data if brand_logo_section else None,
            "offer_banners": OfferBannerSerializer(offer_banners, many=True, context={"request": request}).data,
            "offer_grid": OfferGridSectionSerializer(offer_grid, context={"request": request}).data if offer_grid else None,
            "footer_social": FooterSocialSectionSerializer(footer_social, context={"request": request}).data if footer_social else None,
            "newsletter_settings": NewsletterSettingsSerializer(newsletter_settings).data if newsletter_settings else None,
            "categories": CategorySerializer(m.Category.objects.filter(is_active=True, parent__isnull=True), many=True, context={"request": request}).data,
            "trending_products": ProductListSerializer(products.filter(is_trending=True)[:12], many=True, context={"request": request}).data,
            "new_arrivals": ProductListSerializer(products.filter(is_new_arrival=True)[:12], many=True, context={"request": request}).data,
            "featured_products": ProductListSerializer(products.filter(is_featured=True)[:12], many=True, context={"request": request}).data,
            "sections": list(m.HomepageSection.objects.filter(is_active=True).values(
                "id", "title", "section_type", "content", "editor_content", "display_order"
            )),
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


@extend_schema_view(list=extend_schema(tags=["Products"]), retrieve=extend_schema(tags=["Products"]))
class ProductViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = (permissions.AllowAny,)
    lookup_field = "slug"
    search_fields = ("name", "brand", "description", "category__name", "variants__sku")
    ordering_fields = ("name", "regular_price", "sale_price", "created_at")
    filterset_fields = ("category__slug", "category__audience", "brand", "is_trending", "is_new_arrival")

    def get_queryset(self):
        queryset = m.Product.objects.filter(status=m.Product.Status.ACTIVE).select_related("category").prefetch_related("images", "variants", "reviews__customer__user", "questions__customer__user").distinct()
        minimum = self.request.query_params.get("min_price")
        maximum = self.request.query_params.get("max_price")
        in_stock = self.request.query_params.get("in_stock")
        if minimum:
            queryset = queryset.filter(Q(sale_price__gte=minimum) | Q(sale_price__isnull=True, regular_price__gte=minimum))
        if maximum:
            queryset = queryset.filter(Q(sale_price__lte=maximum) | Q(sale_price__isnull=True, regular_price__lte=maximum))
        if in_stock in ("true", "1"):
            queryset = queryset.filter(variants__stock_quantity__gt=0)
        return queryset

    def get_serializer_class(self):
        return ProductDetailSerializer if self.action == "retrieve" else ProductListSerializer

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

    @extend_schema(tags=["Products"], request=ProductQuestionSerializer)
    @action(detail=True, methods=("post",), permission_classes=(IsCustomer,))
    def questions(self, request, slug=None):
        product = self.get_object()
        serializer = ProductQuestionSerializer(data={**request.data, "product": product.pk}, context={"request": request})
        serializer.is_valid(raise_exception=True)
        serializer.save(customer=request.user.customer_profile)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


@extend_schema(tags=["Customer Account"])
class ProfileView(generics.RetrieveUpdateAPIView):
    permission_classes = (IsCustomer,)
    serializer_class = CustomerProfileSerializer

    def get_object(self):
        return self.request.user.customer_profile


@extend_schema_view(list=extend_schema(tags=["Customer Account"]), create=extend_schema(tags=["Customer Account"]), retrieve=extend_schema(tags=["Customer Account"]), update=extend_schema(tags=["Customer Account"]), partial_update=extend_schema(tags=["Customer Account"]), destroy=extend_schema(tags=["Customer Account"]))
class AddressViewSet(viewsets.ModelViewSet):
    permission_classes = (IsCustomer,)
    serializer_class = AddressSerializer
    queryset = m.Address.objects.none()

    def get_queryset(self):
        return m.Address.objects.filter(customer=self.request.user.customer_profile)

    def perform_create(self, serializer):
        serializer.save(customer=self.request.user.customer_profile)


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
        return m.Order.objects.filter(customer=self.request.user.customer_profile).prefetch_related("items", "payments").select_related("invoice")

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
class ReviewCreateView(generics.CreateAPIView):
    permission_classes = (IsCustomer,)
    serializer_class = ReviewCreateSerializer

    def perform_create(self, serializer):
        serializer.save(customer=self.request.user.customer_profile, is_verified_purchase=True)


@extend_schema(tags=["Storefront"])
class NewsletterView(generics.CreateAPIView):
    permission_classes = (permissions.AllowAny,)
    serializer_class = NewsletterSerializer

    def perform_create(self, serializer):
        serializer.save()


@extend_schema(tags=["Storefront"])
class PageDetailView(generics.RetrieveAPIView):
    permission_classes = (permissions.AllowAny,)
    authentication_classes = ()
    serializer_class = PageSerializer
    lookup_field = "slug"
    queryset = m.Page.objects.filter(is_active=True)


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
