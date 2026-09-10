from decimal import Decimal

from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
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
    profile_image = serializers.SerializerMethodField()

    class Meta:
        model = get_user_model()
        fields = ("id", "username", "email", "first_name", "last_name", "role", "profile_image")

    @extend_schema_field(serializers.URLField(allow_null=True))
    def get_profile_image(self, obj):
        """Return a customer avatar URL when this user has one, otherwise null."""
        customer = getattr(obj, "customer_profile", None)
        if not customer or not customer.profile_image:
            return None
        url = customer.profile_image.url
        request = self.context.get("request")
        return request.build_absolute_uri(url) if request else url


class RegistrationSerializer(serializers.Serializer):
    email = serializers.EmailField()
    phone = serializers.CharField(max_length=30)
    # ``full_name`` is the storefront field.  The split fields remain accepted
    # for existing API consumers while they migrate.
    full_name = serializers.CharField(max_length=300, required=False, write_only=True)
    first_name = serializers.CharField(max_length=150, required=False)
    last_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    password = serializers.CharField(write_only=True, min_length=8)
    confirm_password = serializers.CharField(write_only=True, min_length=8)
    terms_accepted = serializers.BooleanField(write_only=True)

    def validate_email(self, value):
        existing_user = get_user_model().objects.filter(email__iexact=value).first()
        if existing_user:
            if hasattr(existing_user, "influencer_profile"):
                raise serializers.ValidationError(
                    "This email is already registered as an influencer and cannot be used for a customer account."
                )
            raise serializers.ValidationError("An account with this email already exists.")
        return value.lower()

    def validate(self, attrs):
        full_name = attrs.pop("full_name", "").strip()
        if full_name:
            name_parts = full_name.split(maxsplit=1)
            attrs["first_name"] = name_parts[0]
            attrs["last_name"] = name_parts[1] if len(name_parts) == 2 else ""

        if not attrs.get("first_name", "").strip():
            raise serializers.ValidationError({"full_name": "Enter your full name."})
        if attrs["password"] != attrs.pop("confirm_password"):
            raise serializers.ValidationError({"confirm_password": "Passwords do not match."})
        if not attrs.pop("terms_accepted"):
            raise serializers.ValidationError({"terms_accepted": "You must agree to the Terms & Conditions and Privacy Policy."})
        try:
            validate_password(attrs["password"])
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"password": list(exc.messages)})
        return attrs

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


class VerifyEmailSerializer(serializers.Serializer):
    uid = serializers.CharField()
    token = serializers.CharField()


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
        if hasattr(authenticated, "customer_profile") and not authenticated.customer_profile.email_verified:
            raise serializers.ValidationError({"email": "Please verify your email address before logging in."})
        attrs["user"] = authenticated
        return attrs


class ForgotPasswordSerializer(serializers.Serializer):
    email = serializers.EmailField()


class ResetPasswordSerializer(serializers.Serializer):
    uid = serializers.CharField()
    token = serializers.CharField()
    account = serializers.ChoiceField(choices=("customer", "influencer"), required=False)
    new_password = serializers.CharField(write_only=True, min_length=8)
    confirm_password = serializers.CharField(write_only=True, min_length=8)

    def validate(self, attrs):
        if attrs["new_password"] != attrs["confirm_password"]:
            raise serializers.ValidationError({"confirm_password": "Passwords do not match."})
        return attrs


class CategorySerializer(serializers.ModelSerializer):
    children = serializers.SerializerMethodField()
    has_subcategories = serializers.SerializerMethodField()

    class Meta:
        model = m.Category
        fields = ("id", "name", "slug", "audience", "description", "image", "has_subcategories", "children")

    @extend_schema_field(serializers.BooleanField())
    def get_has_subcategories(self, obj):
        """Whether this category has at least one active child category."""
        prefetched_children = getattr(obj, "_prefetched_objects_cache", {}).get("children")
        if prefetched_children is not None:
            return any(child.is_active for child in prefetched_children)
        return obj.children.filter(is_active=True).exists()

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
    # A review belongs to the signed-in customer's profile, which is a
    # one-to-one extension of the user.  Expose that user consistently in all
    # storefront review lists without accepting any reviewer identity from the
    # client.
    user_id = serializers.IntegerField(source="customer.user_id", read_only=True)
    customer_name = serializers.SerializerMethodField()
    customer_photo = serializers.ImageField(source="customer.profile_image", read_only=True)

    class Meta:
        model = m.Review
        fields = ("id", "user_id", "customer_name", "customer_photo", "rating", "title", "body", "is_verified_purchase", "admin_response", "created_at")
        read_only_fields = ("is_verified_purchase", "admin_response")

    @extend_schema_field(serializers.CharField())
    def get_customer_name(self, obj):
        return obj.customer.user.get_full_name() or obj.customer.user.get_username()


class ProductFAQSerializer(serializers.ModelSerializer):
    class Meta:
        model = m.ProductFAQ
        fields = ("id", "question", "answer", "display_order")


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
    faqs = serializers.SerializerMethodField()
    average_rating = serializers.SerializerMethodField()
    review_count = serializers.SerializerMethodField()
    rating_count = serializers.SerializerMethodField()
    discount_percentage = serializers.SerializerMethodField()
    related_products = serializers.SerializerMethodField()

    class Meta(ProductListSerializer.Meta):
        fields = ProductListSerializer.Meta.fields + (
            "description", "images", "variants", "reviews", "faqs",
            "average_rating", "review_count", "rating_count", "discount_percentage",
            "related_products", "seo_title", "seo_description",
        )

    @extend_schema_field(ReviewSerializer(many=True))
    def get_reviews(self, obj):
        return ReviewSerializer(obj.reviews.filter(status=m.Review.Status.APPROVED), many=True).data

    @extend_schema_field(ProductFAQSerializer(many=True))
    def get_faqs(self, obj):
        return ProductFAQSerializer(obj.faqs.filter(is_active=True), many=True).data

    def _approved_reviews(self, obj):
        return obj.reviews.filter(status=m.Review.Status.APPROVED)

    @extend_schema_field(serializers.FloatField())
    def get_average_rating(self, obj):
        values = list(self._approved_reviews(obj).values_list("rating", flat=True))
        return round(sum(values) / len(values), 1) if values else 0.0

    @extend_schema_field(serializers.IntegerField())
    def get_review_count(self, obj):
        return self._approved_reviews(obj).count()

    @extend_schema_field(serializers.IntegerField())
    def get_rating_count(self, obj):
        return self._approved_reviews(obj).count()

    @extend_schema_field(serializers.IntegerField())
    def get_discount_percentage(self, obj):
        if obj.sale_price is None or not obj.regular_price or obj.sale_price >= obj.regular_price:
            return 0
        return max(0, round((obj.regular_price - obj.sale_price) * 100 / obj.regular_price))

    @extend_schema_field(ProductListSerializer(many=True))
    def get_related_products(self, obj):
        related = obj.related_products.filter(status=m.Product.Status.ACTIVE).select_related("category").prefetch_related("images", "variants")[:8]
        return ProductListSerializer(related, many=True, context=self.context).data


class AddressSerializer(serializers.ModelSerializer):
    class Meta:
        model = m.Address
        exclude = ("customer",)
        read_only_fields = ("created_at", "updated_at")


class CustomerProfileSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)
    first_name = serializers.CharField(source="user.first_name", required=False, allow_blank=True)
    last_name = serializers.CharField(source="user.last_name", required=False, allow_blank=True)
    current_password = serializers.CharField(write_only=True, required=False, trim_whitespace=False)
    new_password = serializers.CharField(write_only=True, required=False, min_length=8, trim_whitespace=False)
    confirm_password = serializers.CharField(write_only=True, required=False, min_length=8, trim_whitespace=False)
    email_verified = serializers.BooleanField(read_only=True)
    member_since = serializers.SerializerMethodField()
    # FileField permits safe SVG avatars; the shared model validator verifies
    # raster images and applies the project's type and 5 MB size limits.
    profile_image = serializers.FileField(
        required=False, allow_empty_file=False, validators=[m.validate_profile_image]
    )

    class Meta:
        model = m.CustomerProfile
        fields = (
            "user", "first_name", "last_name", "phone", "date_of_birth", "marketing_consent", "profile_image",
            "email_verified", "member_since",
            "current_password", "new_password", "confirm_password",
        )

    @extend_schema_field(serializers.CharField())
    def get_member_since(self, obj):
        return obj.user.date_joined.strftime("%b %Y")

    def validate(self, attrs):
        attrs = super().validate(attrs)
        current_password = attrs.get("current_password")
        new_password = attrs.get("new_password")
        confirm_password = attrs.get("confirm_password")

        password_fields_supplied = any(
            value is not None for value in (current_password, new_password, confirm_password)
        )
        if not password_fields_supplied:
            return attrs

        errors = {}
        if not current_password:
            errors["current_password"] = "Current password is required to change the password."
        if not new_password:
            errors["new_password"] = "New password is required."
        if not confirm_password:
            errors["confirm_password"] = "Confirm password is required."
        if errors:
            raise serializers.ValidationError(errors)

        user = self.context["request"].user
        if not user.check_password(current_password):
            raise serializers.ValidationError({"current_password": "Incorrect old password. Please try again."})
        if new_password != confirm_password:
            raise serializers.ValidationError({"confirm_password": "Passwords do not match."})
        try:
            validate_password(new_password, user=user)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"new_password": list(exc.messages)})
        return attrs

    @transaction.atomic
    def update(self, instance, validated_data):
        old_profile_image_name = (
            instance.profile_image.name if "profile_image" in validated_data and instance.profile_image else None
        )
        user_data = validated_data.pop("user", {})
        current_password = validated_data.pop("current_password", None)
        new_password = validated_data.pop("new_password", None)
        validated_data.pop("confirm_password", None)

        user = instance.user
        changed_user_fields = []
        for field, value in user_data.items():
            setattr(user, field, value)
            changed_user_fields.append(field)

        if new_password:
            user.set_password(new_password)
            changed_user_fields.append("password")

        if changed_user_fields:
            user.save(update_fields=tuple(dict.fromkeys(changed_user_fields)))

        profile = super().update(instance, validated_data)

        # Uploaded files receive a new storage name, so deleting the previous
        # file after the database transaction commits cannot affect the new one.
        if old_profile_image_name and old_profile_image_name != profile.profile_image.name:
            storage = profile.profile_image.storage
            transaction.on_commit(lambda: storage.delete(old_profile_image_name))

        return profile


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
    product_image = serializers.SerializerMethodField()
    status = serializers.CharField(source="order.status", read_only=True)
    status_display = serializers.CharField(source="order.get_status_display", read_only=True)

    class Meta:
        model = m.OrderItem
        fields = ("id", "product_name", "product_image", "sku", "unit_price", "quantity", "total", "status", "status_display")

    @extend_schema_field(serializers.URLField(allow_null=True))
    def get_product_image(self, obj):
        image = next(iter(obj.variant.product.images.all()), None)
        return image.image.url if image else None


class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)
    invoice_number = serializers.CharField(source="invoice.number", read_only=True, default=None)
    payment_status = serializers.SerializerMethodField()
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = m.Order
        fields = ("id", "number", "status", "status_display", "items", "subtotal", "discount_total", "shipping_total", "tax_total", "grand_total", "coupon", "affiliate_code", "shipping_address", "billing_address", "payment_status", "invoice_number", "placed_at")

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
    # The storefront review form only sends product, rating and body.  An old
    # client may still send order_item, but it is deliberately ignored: reviews
    # no longer require or claim a delivered purchase.
    order_item = serializers.PrimaryKeyRelatedField(
        queryset=m.OrderItem.objects.all(), required=False, allow_null=True
    )
    title = serializers.CharField(required=False, allow_blank=True, default="")

    class Meta:
        model = m.Review
        fields = ("product", "order_item", "rating", "title", "body")

    def validate(self, attrs):
        customer = self.context["request"].user.customer_profile
        product = attrs["product"]

        if m.Review.objects.filter(product=product, customer=customer).exists():
            raise serializers.ValidationError({"product": "You have already reviewed this product."})

        # Never trust an order item supplied by the browser.  The authenticated
        # user is set by the view, and this review is not purchase-verified.
        attrs["order_item"] = None
        return attrs


class CustomerReviewSerializer(serializers.ModelSerializer):
    product_id = serializers.IntegerField(source="product.id", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    product_image = serializers.SerializerMethodField()
    comment = serializers.CharField(source="body", required=False)

    class Meta:
        model = m.Review
        fields = (
            "id", "product_id", "product_name", "product_image", "rating", "title", "body", "comment",
            "created_at", "updated_at",
        )
        read_only_fields = ("id", "product_id", "product_name", "product_image", "created_at", "updated_at")

    @extend_schema_field(serializers.URLField(allow_null=True))
    def get_product_image(self, obj):
        image = next(iter(obj.product.images.all()), None)
        return image.image.url if image else None


class PublicReviewSerializer(ReviewSerializer):
    """The approved-review shape returned by the storefront reviews endpoint."""

    product_id = serializers.IntegerField(source="product.id", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)

    class Meta(ReviewSerializer.Meta):
        fields = ("id", "product_id", "product_name", "user_id", "customer_name", "customer_photo", "rating", "title", "body", "is_verified_purchase", "admin_response", "created_at")


class CustomerCouponSerializer(serializers.ModelSerializer):
    title = serializers.CharField(source="code", read_only=True)
    minimum_order_amount = serializers.DecimalField(source="minimum_order_value", max_digits=12, decimal_places=2, read_only=True)
    valid_from = serializers.DateTimeField(source="starts_at", read_only=True)
    valid_until = serializers.DateTimeField(source="expires_at", read_only=True)
    is_used = serializers.SerializerMethodField()
    is_applicable = serializers.SerializerMethodField()

    class Meta:
        model = m.Coupon
        fields = (
            "id", "code", "title", "description", "discount_type", "discount_value", "minimum_order_amount",
            "valid_from", "valid_until", "is_active", "is_used", "is_applicable",
        )

    def get_is_used(self, obj):
        customer = self.context["request"].user.customer_profile
        return obj.usages.filter(customer=customer).exists()

    def get_is_applicable(self, obj):
        customer = self.context["request"].user.customer_profile
        now = timezone.now()
        return (
            obj.is_active and obj.starts_at <= now <= obj.expires_at
            and obj.usages.filter(customer=customer).count() < obj.per_customer_limit
        )


class CustomerDashboardSerializer(serializers.Serializer):
    customer = serializers.DictField()
    summary = serializers.DictField()
    recent_orders = serializers.ListField(child=serializers.DictField())


class NewsletterSerializer(serializers.ModelSerializer):
    # The default ModelSerializer uniqueness validator runs before
    # validate_email().  Let the latter provide the useful subscriber-facing
    # message instead.
    email = serializers.EmailField(validators=[])

    def validate_email(self, value):
        if m.NewsletterSubscription.objects.filter(email__iexact=value, is_active=True).exists():
            raise serializers.ValidationError("This email is already subscribed.")
        return value.lower()

    def create(self, validated_data):
        subscription = m.NewsletterSubscription.objects.filter(
            email__iexact=validated_data["email"],
        ).first()
        if subscription:
            subscription.is_active = True
            subscription.source = validated_data.get("source", subscription.source)
            subscription.save(update_fields=("is_active", "source", "updated_at"))
            return subscription
        return super().create(validated_data)

    class Meta:
        model = m.NewsletterSubscription
        fields = ("email", "source")


class PageSerializer(serializers.ModelSerializer):
    class Meta:
        model = m.Page
        fields = ("title", "slug", "content", "seo_title", "seo_description", "updated_at")


class ContactSubmissionSerializer(serializers.ModelSerializer):
    class Meta:
        model = m.ContactSubmission
        fields = ("name", "email", "phone", "subject", "message")


class InfluencerAddressSerializer(serializers.Serializer):
    line1 = serializers.CharField(required=False, allow_blank=True, max_length=255)
    line2 = serializers.CharField(required=False, allow_blank=True, max_length=255)
    city = serializers.CharField(required=False, allow_blank=True, max_length=100)
    state = serializers.CharField(required=False, allow_blank=True, max_length=100)
    postal_code = serializers.CharField(required=False, allow_blank=True, max_length=20)
    country = serializers.CharField(required=False, allow_blank=True, max_length=100)


class VersionedImageField(serializers.ImageField):
    """Prevent clients from reusing a cached image after it is replaced."""

    def to_representation(self, value):
        url = super().to_representation(value)
        if not url:
            return url

        updated_at = getattr(getattr(value, "instance", None), "updated_at", None)
        if not updated_at:
            return url

        separator = "&" if "?" in url else "?"
        version = int(updated_at.timestamp() * 1_000_000)
        return f"{url}{separator}v={version}"


class InfluencerProfileSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)
    first_name = serializers.CharField(source="user.first_name", required=False)
    last_name = serializers.CharField(source="user.last_name", required=False)
    email = serializers.EmailField(source="user.email", required=False)
    photo = VersionedImageField(source="profile_image", required=False, allow_null=True)
    address = InfluencerAddressSerializer(required=False)
    current_password = serializers.CharField(write_only=True, required=False, trim_whitespace=False)
    new_password = serializers.CharField(write_only=True, required=False, min_length=8, trim_whitespace=False)
    confirm_password = serializers.CharField(write_only=True, required=False, min_length=8, trim_whitespace=False)

    class Meta:
        model = m.InfluencerProfile
        fields = (
            "user", "first_name", "last_name", "email", "affiliate_id", "phone", "photo", "address",
            "current_password", "new_password", "confirm_password",
        )
        read_only_fields = ("affiliate_id",)

    def validate_email(self, value):
        user = self.instance.user if self.instance is not None else None
        # Legacy accounts may share an email. Keeping that address must not
        # prevent edits to unrelated profile fields.
        if user is not None and value.casefold() == user.email.strip().casefold():
            return value
        matches = get_user_model().objects.filter(email__iexact=value)
        if user is not None:
            matches = matches.exclude(pk=user.pk)
        if matches.exists():
            raise serializers.ValidationError("This email is already in use.")
        return value

    def validate(self, attrs):
        attrs = super().validate(attrs)
        current_password = attrs.get("current_password")
        new_password = attrs.get("new_password")
        confirm_password = attrs.get("confirm_password")

        password_fields_supplied = any(
            value is not None for value in (current_password, new_password, confirm_password)
        )
        if not password_fields_supplied:
            return attrs

        errors = {}
        if not current_password:
            errors["current_password"] = "Current password is required to change the password."
        if not new_password:
            errors["new_password"] = "New password is required."
        if not confirm_password:
            errors["confirm_password"] = "Confirm password is required."
        if errors:
            raise serializers.ValidationError(errors)

        user = self.context["request"].user
        if not user.check_password(current_password):
            raise serializers.ValidationError({"current_password": "Incorrect old password. Please try again."})
        if new_password != confirm_password:
            raise serializers.ValidationError({"confirm_password": "Passwords do not match."})
        try:
            validate_password(new_password, user=user)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"new_password": list(exc.messages)})
        return attrs

    @transaction.atomic
    def update(self, instance, validated_data):
        user_data = validated_data.pop("user", {})
        address_data = validated_data.pop("address", None)
        validated_data.pop("current_password", None)
        new_password = validated_data.pop("new_password", None)
        validated_data.pop("confirm_password", None)

        user = instance.user
        changed_user_fields = []
        for field, value in user_data.items():
            setattr(user, field, value)
            changed_user_fields.append(field)
        if new_password:
            user.set_password(new_password)
            changed_user_fields.append("password")
        if changed_user_fields:
            user.save(update_fields=tuple(dict.fromkeys(changed_user_fields)))

        if address_data is not None:
            address = dict(instance.address) if isinstance(instance.address, dict) else {}
            for field, value in address_data.items():
                if value:
                    address[field] = value
                else:
                    address.pop(field, None)
            validated_data["address"] = address
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
        return GiftSectionFeatureSerializer(obj.features.filter(is_active=True).order_by("display_order", "id"), many=True, context=self.context).data

    @extend_schema_field(GiftSectionStatisticSerializer(many=True))
    def get_statistics(self, obj):
        return GiftSectionStatisticSerializer(obj.statistics.filter(is_active=True).order_by("display_order", "id"), many=True, context=self.context).data


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
        return BrandLogoSerializer(obj.logos.filter(is_active=True).order_by("display_order", "id"), many=True, context=self.context).data


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
        return OfferGridItemSerializer(obj.items.filter(is_active=True).order_by("display_order", "id"), many=True, context=self.context).data


class FooterSocialLinkSerializer(serializers.ModelSerializer):
    platform = serializers.CharField(source="platform_name", read_only=True)
    platform_name = serializers.CharField(source="get_platform_name_display", read_only=True)

    class Meta:
        model = m.FooterSocialLink
        fields = ("id", "platform", "platform_name", "url", "aria_label", "display_order")


class FooterSocialSectionSerializer(serializers.ModelSerializer):
    links = serializers.SerializerMethodField()

    class Meta:
        model = m.FooterSocialSection
        fields = ("id", "heading", "links")

    @extend_schema_field(FooterSocialLinkSerializer(many=True))
    def get_links(self, obj):
        return FooterSocialLinkSerializer(obj.links.filter(is_active=True), many=True, context=self.context).data


class NewsletterSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = m.NewsletterSettings
        fields = ("id", "title", "description")


class HomepageResponseSerializer(serializers.Serializer):
    banners = serializers.ListField(child=serializers.DictField())
    gift_sections = GiftSectionSerializer(many=True)
    brand_logo_section = BrandLogoSectionSerializer(allow_null=True)
    offer_banners = OfferBannerSerializer(many=True)
    offer_grid = OfferGridSectionSerializer(allow_null=True)
    footer_social = FooterSocialSectionSerializer(allow_null=True)
    newsletter_settings = NewsletterSettingsSerializer(allow_null=True)
    categories = CategorySerializer(many=True)
    trending_products = ProductListSerializer(many=True)
    new_arrivals = ProductListSerializer(many=True)
    featured_products = ProductListSerializer(many=True)
    sections = serializers.ListField(child=serializers.DictField())
    section_settings = serializers.DictField(child=serializers.DictField())
    testimonials = serializers.ListField(child=serializers.DictField())
