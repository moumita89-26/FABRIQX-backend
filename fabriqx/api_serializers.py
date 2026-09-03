from decimal import Decimal

from django.contrib.auth import authenticate, get_user_model
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from rest_framework import serializers
from drf_spectacular.utils import extend_schema_field

from . import models as m


class EmptySerializer(serializers.Serializer):
    pass


class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField(write_only=True)


class InfluencerSalesSerializer(serializers.Serializer):
    affiliate_id = serializers.CharField()
    total_orders = serializers.IntegerField()
    total_sales = serializers.DecimalField(max_digits=14, decimal_places=2)
    commission_type = serializers.CharField()
    commission_rate = serializers.DecimalField(max_digits=5, decimal_places=2)
    commission_fixed_amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    total_commission = serializers.DecimalField(max_digits=14, decimal_places=2)
    pending_commission = serializers.DecimalField(max_digits=14, decimal_places=2)
    paid_commission = serializers.DecimalField(max_digits=14, decimal_places=2)
    approved_commission = serializers.DecimalField(max_digits=14, decimal_places=2)
    successful_orders = serializers.IntegerField()
    recent_sales = serializers.DecimalField(max_digits=14, decimal_places=2)
    period = serializers.CharField()
    series = serializers.ListField(child=serializers.DictField())


class UserSerializer(serializers.ModelSerializer):
    role = serializers.CharField(source="fabriqx_role.role", read_only=True)

    class Meta:
        model = get_user_model()
        fields = ("id", "username", "email", "first_name", "last_name", "role")


class RegistrationSerializer(serializers.Serializer):
    email = serializers.EmailField()
    phone = serializers.CharField(max_length=30)
    first_name = serializers.CharField(max_length=150)
    last_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    password = serializers.CharField(write_only=True, min_length=8)

    def validate_email(self, value):
        if get_user_model().objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("An account with this email already exists.")
        return value.lower()

    @transaction.atomic
    def create(self, validated_data):
        phone = validated_data.pop("phone")
        email = validated_data.pop("email")
        password = validated_data.pop("password")
        user = get_user_model().objects.create_user(username=email, email=email, password=password, **validated_data)
        customer = m.CustomerProfile.objects.create(user=user, phone=phone)
        m.UserRole.objects.update_or_create(user=user, defaults={"role": m.UserRole.Role.CUSTOMER})
        m.Cart.objects.get_or_create(customer=customer)
        return user


class LoginSerializer(serializers.Serializer):
    login = serializers.CharField()
    password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        login = attrs["login"]
        user_model = get_user_model()
        user = user_model.objects.filter(email__iexact=login).first()
        username = user.username if user else login
        authenticated = authenticate(request=self.context.get("request"), username=username, password=attrs["password"])
        if not authenticated or not authenticated.is_active:
            raise serializers.ValidationError("Invalid credentials.")
        attrs["user"] = authenticated
        return attrs


class ForgotPasswordSerializer(serializers.Serializer):
    email = serializers.EmailField()


class ResetPasswordSerializer(serializers.Serializer):
    uid = serializers.CharField()
    token = serializers.CharField()
    new_password = serializers.CharField(write_only=True, min_length=8)
    confirm_password = serializers.CharField(write_only=True, min_length=8)

    def validate(self, attrs):
        if attrs["new_password"] != attrs["confirm_password"]:
            raise serializers.ValidationError({"confirm_password": "Passwords do not match."})
        return attrs


class CategorySerializer(serializers.ModelSerializer):
    children = serializers.SerializerMethodField()

    class Meta:
        model = m.Category
        fields = ("id", "name", "slug", "audience", "description", "image", "children")

    @extend_schema_field(serializers.ListField(child=serializers.DictField()))
    def get_children(self, obj):
        return CategorySerializer(obj.children.filter(is_active=True), many=True, context=self.context).data


class ProductImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = m.ProductImage
        fields = ("id", "image", "alt_text", "is_primary")


class VariantSerializer(serializers.ModelSerializer):
    effective_price = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    stock_status = serializers.CharField(read_only=True)

    class Meta:
        model = m.ProductVariant
        fields = ("id", "sku", "size", "color", "color_code", "effective_price", "stock_quantity", "stock_status")


class ReviewSerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(source="customer.user.get_full_name", read_only=True)

    class Meta:
        model = m.Review
        fields = ("id", "customer_name", "rating", "title", "body", "is_verified_purchase", "admin_response", "created_at")
        read_only_fields = ("is_verified_purchase", "admin_response")


class ProductQuestionSerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(source="customer.user.get_full_name", read_only=True)

    class Meta:
        model = m.ProductQuestion
        fields = ("id", "product", "customer_name", "question", "answer", "created_at", "answered_at")
        read_only_fields = ("answer", "answered_at")


class ProductListSerializer(serializers.ModelSerializer):
    category = CategorySerializer(read_only=True)
    primary_image = serializers.SerializerMethodField()
    price = serializers.SerializerMethodField()
    total_stock = serializers.IntegerField(read_only=True)

    class Meta:
        model = m.Product
        fields = ("id", "name", "slug", "brand", "short_description", "category", "regular_price", "sale_price", "price", "total_stock", "primary_image", "is_featured", "is_trending", "is_new_arrival")

    @extend_schema_field(ProductImageSerializer(allow_null=True))
    def get_primary_image(self, obj):
        image = next(iter(obj.images.all()), None)
        return ProductImageSerializer(image, context=self.context).data if image else None

    @extend_schema_field(serializers.DecimalField(max_digits=12, decimal_places=2))
    def get_price(self, obj):
        return obj.sale_price or obj.regular_price


class ProductDetailSerializer(ProductListSerializer):
    images = ProductImageSerializer(many=True, read_only=True)
    variants = VariantSerializer(many=True, read_only=True)
    reviews = serializers.SerializerMethodField()
    questions = serializers.SerializerMethodField()

    class Meta(ProductListSerializer.Meta):
        fields = ProductListSerializer.Meta.fields + ("description", "images", "variants", "reviews", "questions", "seo_title", "seo_description")

    @extend_schema_field(ReviewSerializer(many=True))
    def get_reviews(self, obj):
        return ReviewSerializer(obj.reviews.filter(status=m.Review.Status.APPROVED), many=True).data

    @extend_schema_field(ProductQuestionSerializer(many=True))
    def get_questions(self, obj):
        return ProductQuestionSerializer(obj.questions.filter(is_published=True), many=True).data


class AddressSerializer(serializers.ModelSerializer):
    class Meta:
        model = m.Address
        exclude = ("customer",)
        read_only_fields = ("created_at", "updated_at")


class CustomerProfileSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)

    class Meta:
        model = m.CustomerProfile
        fields = ("user", "phone", "date_of_birth", "marketing_consent")

    def update(self, instance, validated_data):
        user_data = self.context["request"].data.get("user", {})
        for field in ("first_name", "last_name"):
            if field in user_data:
                setattr(instance.user, field, user_data[field])
        instance.user.save(update_fields=("first_name", "last_name"))
        return super().update(instance, validated_data)


class WishlistSerializer(serializers.ModelSerializer):
    product_detail = ProductListSerializer(source="product", read_only=True)

    class Meta:
        model = m.WishlistItem
        fields = ("id", "product", "variant", "product_detail", "created_at")
        read_only_fields = ("created_at",)


class CartItemSerializer(serializers.ModelSerializer):
    variant_detail = VariantSerializer(source="variant", read_only=True)
    product = ProductListSerializer(source="variant.product", read_only=True)
    line_total = serializers.SerializerMethodField()

    class Meta:
        model = m.CartItem
        fields = ("id", "variant", "variant_detail", "product", "quantity", "line_total")

    def validate(self, attrs):
        variant = attrs.get("variant", getattr(self.instance, "variant", None))
        quantity = attrs.get("quantity", getattr(self.instance, "quantity", 1))
        if variant and (not variant.is_active or quantity > variant.stock_quantity):
            raise serializers.ValidationError({"quantity": "Requested quantity is not available."})
        return attrs

    @extend_schema_field(serializers.DecimalField(max_digits=12, decimal_places=2))
    def get_line_total(self, obj):
        return obj.variant.effective_price * obj.quantity


class CartSerializer(serializers.ModelSerializer):
    items = CartItemSerializer(many=True, read_only=True)
    subtotal = serializers.SerializerMethodField()
    discount = serializers.SerializerMethodField()
    total = serializers.SerializerMethodField()
    coupon_code = serializers.CharField(source="coupon.code", read_only=True)

    class Meta:
        model = m.Cart
        fields = ("id", "items", "coupon_code", "subtotal", "discount", "total", "updated_at")

    @extend_schema_field(serializers.DecimalField(max_digits=12, decimal_places=2))
    def get_subtotal(self, obj):
        return sum((item.variant.effective_price * item.quantity for item in obj.items.all()), Decimal("0"))

    @extend_schema_field(serializers.DecimalField(max_digits=12, decimal_places=2))
    def get_discount(self, obj):
        return calculate_discount(obj.coupon, self.get_subtotal(obj))

    @extend_schema_field(serializers.DecimalField(max_digits=12, decimal_places=2))
    def get_total(self, obj):
        return self.get_subtotal(obj) - self.get_discount(obj)


def calculate_discount(coupon, subtotal):
    if not coupon or subtotal < coupon.minimum_order_value:
        return Decimal("0")
    if coupon.discount_type == m.Coupon.DiscountType.PERCENTAGE:
        discount = subtotal * coupon.discount_value / Decimal("100")
        discount = min(discount, coupon.maximum_discount) if coupon.maximum_discount else discount
    else:
        discount = min(coupon.discount_value, subtotal)
    return discount.quantize(Decimal("0.01"))


class OrderItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = m.OrderItem
        fields = ("id", "product_name", "sku", "unit_price", "quantity", "total")


class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)
    invoice_number = serializers.CharField(source="invoice.number", read_only=True, default=None)
    payment_status = serializers.SerializerMethodField()

    class Meta:
        model = m.Order
        fields = ("id", "number", "status", "items", "subtotal", "discount_total", "shipping_total", "tax_total", "grand_total", "coupon", "affiliate_code", "shipping_address", "billing_address", "payment_status", "invoice_number", "placed_at")

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_payment_status(self, obj):
        payment = obj.payments.order_by("-created_at").first()
        return payment.status if payment else None


class InfluencerOrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)
    payment_status = serializers.SerializerMethodField()
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = m.Order
        fields = (
            "id", "number", "status", "status_display", "items", "subtotal",
            "discount_total", "shipping_total", "tax_total", "grand_total",
            "payment_status", "placed_at",
        )

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_payment_status(self, obj):
        payment = obj.payments.order_by("-created_at").first()
        return payment.status if payment else None


class InfluencerCommissionSerializer(serializers.ModelSerializer):
    order_number = serializers.CharField(source="order.number", read_only=True)
    order_date = serializers.DateTimeField(source="order.placed_at", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = m.InfluencerCommission
        fields = (
            "id", "order", "order_number", "order_date", "eligible_amount", "rate",
            "commission_amount", "status", "status_display", "payment_reference", "paid_at",
        )


class InfluencerDashboardSerializer(serializers.Serializer):
    affiliate_id = serializers.CharField()
    total_referred_orders = serializers.IntegerField()
    successful_orders = serializers.IntegerField()
    total_sales = serializers.DecimalField(max_digits=14, decimal_places=2)
    total_commission = serializers.DecimalField(max_digits=14, decimal_places=2)
    pending_commission = serializers.DecimalField(max_digits=14, decimal_places=2)
    approved_commission = serializers.DecimalField(max_digits=14, decimal_places=2)
    paid_commission = serializers.DecimalField(max_digits=14, decimal_places=2)
    recent_orders = InfluencerOrderSerializer(many=True)


class AffiliateValidationSerializer(serializers.Serializer):
    affiliate_id = serializers.CharField(max_length=40)


class CouponApplySerializer(serializers.Serializer):
    code = serializers.CharField(max_length=50)


class CheckoutSerializer(serializers.Serializer):
    email = serializers.EmailField()
    phone = serializers.CharField(max_length=30)
    shipping_address = serializers.JSONField()
    billing_address = serializers.JSONField(required=False)
    affiliate_id = serializers.CharField(max_length=40, required=False, allow_blank=True)
    shipping_total = serializers.DecimalField(max_digits=12, decimal_places=2, default=0, min_value=0)
    tax_total = serializers.DecimalField(max_digits=12, decimal_places=2, default=0, min_value=0)
    payment_provider = serializers.CharField(max_length=40, default="marketplace")

    @transaction.atomic
    def create(self, validated_data):
        customer = self.context["request"].user.customer_profile
        cart = m.Cart.objects.select_for_update().prefetch_related("items__variant__product").get(customer=customer)
        items = list(cart.items.all())
        if not items:
            raise serializers.ValidationError("Cart is empty.")
        subtotal = sum((item.variant.effective_price * item.quantity for item in items), Decimal("0"))
        affiliate_id = validated_data.pop("affiliate_id", "")
        coupon = cart.coupon
        if affiliate_id and not coupon:
            influencer = m.InfluencerProfile.objects.filter(affiliate_id__iexact=affiliate_id.strip(), is_active=True).first()
            if influencer:
                now = timezone.now()
                coupon = influencer.coupons.filter(is_active=True, starts_at__lte=now, expires_at__gte=now).order_by("-discount_value").first()
        discount = calculate_discount(coupon, subtotal)
        provider = validated_data.pop("payment_provider")
        billing = validated_data.pop("billing_address", validated_data["shipping_address"])
        grand_total = subtotal - discount + validated_data["shipping_total"] + validated_data["tax_total"]
        order = m.Order(customer=customer, coupon=coupon, affiliate_code=affiliate_id, subtotal=subtotal, discount_total=discount, grand_total=grand_total, billing_address=billing, **validated_data)
        order.full_clean()
        order.save()
        for item in items:
            variant = m.ProductVariant.objects.select_for_update().get(pk=item.variant_id)
            if item.quantity > variant.stock_quantity:
                raise serializers.ValidationError(f"Insufficient stock for {variant.sku}.")
            unit_price = variant.effective_price
            m.OrderItem.objects.create(order=order, variant=variant, product_name=variant.product.name, sku=variant.sku, unit_price=unit_price, quantity=item.quantity, total=unit_price * item.quantity)
            m.ProductVariant.objects.filter(pk=variant.pk).update(stock_quantity=F("stock_quantity") - item.quantity)
        if coupon:
            m.CouponUsage.objects.create(coupon=coupon, customer=customer, order=order, discount_amount=discount)
        m.Payment.objects.create(order=order, provider=provider, amount=grand_total)
        return order


class PaymentConfirmationSerializer(serializers.Serializer):
    transaction_id = serializers.CharField(max_length=150)
    provider_response = serializers.JSONField(required=False, default=dict)


class OrderReasonSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=1000)


class RefundRequestSerializer(OrderReasonSerializer):
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, min_value=Decimal("0.01"))


class MarketplaceWebhookSerializer(serializers.Serializer):
    order_number = serializers.CharField(max_length=40)
    transaction_id = serializers.CharField(max_length=150)
    status = serializers.ChoiceField(choices=("paid", "failed"))
    amount = serializers.DecimalField(max_digits=12, decimal_places=2)


class ReviewCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = m.Review
        fields = ("product", "order_item", "rating", "title", "body")

    def validate(self, attrs):
        customer = self.context["request"].user.customer_profile
        item = attrs.get("order_item")
        if not item or item.order.customer_id != customer.id or item.variant.product_id != attrs["product"].id or item.order.status != m.Order.Status.DELIVERED:
            raise serializers.ValidationError("Reviews are allowed only for delivered products you purchased.")
        return attrs


class NewsletterSerializer(serializers.ModelSerializer):
    class Meta:
        model = m.NewsletterSubscription
        fields = ("email", "source")


class PageSerializer(serializers.ModelSerializer):
    class Meta:
        model = m.Page
        fields = ("title", "slug", "content", "seo_title", "seo_description", "updated_at")


class InfluencerProfileSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)
    first_name = serializers.CharField(source="user.first_name", required=False)
    last_name = serializers.CharField(source="user.last_name", required=False)
    email = serializers.EmailField(source="user.email", required=False)

    class Meta:
        model = m.InfluencerProfile
        fields = ("user", "first_name", "last_name", "email", "affiliate_id", "phone", "profile_image", "address", "commission_type", "commission_rate", "commission_fixed_amount")
        read_only_fields = ("affiliate_id", "commission_type", "commission_rate", "commission_fixed_amount")

    def update(self, instance, validated_data):
        user_data = validated_data.pop("user", {})
        if "email" in user_data and get_user_model().objects.filter(email__iexact=user_data["email"]).exclude(pk=instance.user_id).exists():
            raise serializers.ValidationError({"email": "This email is already in use."})
        for field, value in user_data.items():
            setattr(instance.user, field, value)
        if user_data:
            instance.user.save(update_fields=tuple(user_data))
        return super().update(instance, validated_data)


class SalesReportPointSerializer(serializers.Serializer):
    period = serializers.DateTimeField()
    orders = serializers.IntegerField()
    gross_sales = serializers.DecimalField(max_digits=14, decimal_places=2)
    discounts = serializers.DecimalField(max_digits=14, decimal_places=2)
    net_sales = serializers.DecimalField(max_digits=14, decimal_places=2)


class GiftSectionFeatureSerializer(serializers.ModelSerializer):
    class Meta:
        model = m.GiftSectionFeature
        fields = ("id", "icon", "text", "display_order")


class GiftSectionStatisticSerializer(serializers.ModelSerializer):
    class Meta:
        model = m.GiftSectionStatistic
        fields = ("id", "icon", "eyebrow", "value", "label", "display_order")


class GiftSectionSerializer(serializers.ModelSerializer):
    features = serializers.SerializerMethodField()
    statistics = serializers.SerializerMethodField()

    class Meta:
        model = m.GiftSection
        fields = (
            "id", "internal_name", "badge_eyebrow", "badge_title", "badge_icon", "logo",
            "accent_heading", "heading", "description", "main_image", "gift_image",
            "background_image", "thank_you_title", "thank_you_text", "cta_label", "cta_url",
            "display_order", "features", "statistics",
        )

    @extend_schema_field(GiftSectionFeatureSerializer(many=True))
    def get_features(self, obj):
        return GiftSectionFeatureSerializer(obj.features.filter(is_active=True)[:5], many=True, context=self.context).data

    @extend_schema_field(GiftSectionStatisticSerializer(many=True))
    def get_statistics(self, obj):
        return GiftSectionStatisticSerializer(obj.statistics.filter(is_active=True)[:3], many=True, context=self.context).data


class BrandLogoSerializer(serializers.ModelSerializer):
    class Meta:
        model = m.BrandLogo
        fields = ("id", "brand_name", "logo", "alt_text", "url", "open_in_new_tab", "display_order")


class BrandLogoSectionSerializer(serializers.ModelSerializer):
    logos = serializers.SerializerMethodField()

    class Meta:
        model = m.BrandLogoSection
        fields = ("id", "heading", "background_color", "logos")

    @extend_schema_field(BrandLogoSerializer(many=True))
    def get_logos(self, obj):
        return BrandLogoSerializer(obj.logos.filter(is_active=True)[:6], many=True, context=self.context).data


class OfferBannerSerializer(serializers.ModelSerializer):
    class Meta:
        model = m.OfferBanner
        fields = ("id", "desktop_image", "mobile_image", "alt_text", "shop_now_url", "open_in_new_tab", "display_order")


class OfferGridItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = m.OfferGridItem
        fields = ("id", "desktop_image", "mobile_image", "alt_text", "shop_now_url", "open_in_new_tab", "display_order")


class OfferGridSectionSerializer(serializers.ModelSerializer):
    items = serializers.SerializerMethodField()

    class Meta:
        model = m.OfferGridSection
        fields = ("id", "items")

    @extend_schema_field(OfferGridItemSerializer(many=True))
    def get_items(self, obj):
        return OfferGridItemSerializer(obj.items.filter(is_active=True)[:3], many=True, context=self.context).data


class FooterSocialLinkSerializer(serializers.ModelSerializer):
    class Meta:
        model = m.FooterSocialLink
        fields = ("id", "platform_name", "icon", "url", "aria_label", "display_order")


class FooterSocialSectionSerializer(serializers.ModelSerializer):
    links = serializers.SerializerMethodField()

    class Meta:
        model = m.FooterSocialSection
        fields = ("id", "heading", "links")

    @extend_schema_field(FooterSocialLinkSerializer(many=True))
    def get_links(self, obj):
        return FooterSocialLinkSerializer(obj.links.filter(is_active=True)[:4], many=True, context=self.context).data


class HomepageResponseSerializer(serializers.Serializer):
    banners = serializers.ListField(child=serializers.DictField())
    gift_sections = GiftSectionSerializer(many=True)
    brand_logo_section = BrandLogoSectionSerializer(allow_null=True)
    offer_banners = OfferBannerSerializer(many=True)
    offer_grid = OfferGridSectionSerializer(allow_null=True)
    footer_social = FooterSocialSectionSerializer(allow_null=True)
    categories = CategorySerializer(many=True)
    trending_products = ProductListSerializer(many=True)
    new_arrivals = ProductListSerializer(many=True)
    featured_products = ProductListSerializer(many=True)
    sections = serializers.ListField(child=serializers.DictField())
    testimonials = serializers.ListField(child=serializers.DictField())
