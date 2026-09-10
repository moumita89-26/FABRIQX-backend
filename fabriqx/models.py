import uuid
from decimal import Decimal
import re

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator, MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Sum
from django.utils import timezone
from django.utils.text import slugify


BANNER_LOGO_IMAGE_HELP_TEXT = "Allowed formats: JPG, JPEG, PNG, GIF. Maximum file size: 5 MB."


def validate_banner_logo_image(image):
    if not image or getattr(image, "_committed", False):
        return
    FileExtensionValidator(allowed_extensions=["jpg", "jpeg", "png", "gif"])(image)
    from PIL import Image, UnidentifiedImageError
    position = image.tell()
    try:
        image.seek(0)
        detected = Image.open(image)
        if detected.format not in {"JPEG", "PNG", "GIF"}:
            raise ValidationError("Only JPG, JPEG, PNG, and GIF images are allowed.")
        detected.verify()
    except (UnidentifiedImageError, OSError, SyntaxError) as error:
        raise ValidationError("Upload a valid JPG, JPEG, PNG, or GIF image.") from error
    finally:
        image.seek(position)


MAX_IMAGE_FILE_SIZE = 5 * 1024 * 1024
IMAGE_FILE_SIZE_HELP_TEXT = "Maximum file size: 5 MB."


def validate_image_file_size(image):
    # Existing FieldFile values were already checked when uploaded; avoid
    # reopening storage merely to validate unrelated edits.
    if image and not getattr(image, "_committed", False) and image.size > MAX_IMAGE_FILE_SIZE:
        raise ValidationError("Image file size must not exceed 5 MB.")


def validate_customer_name(value):
    if re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", value.strip()):
        raise ValidationError("Customer name cannot contain only a numeric value.")


def reference(prefix):
    return f"{prefix}-{timezone.now():%Y%m%d}-{uuid.uuid4().hex[:8].upper()}"


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Category(TimeStampedModel):
    class Audience(models.TextChoices):
        WOMEN = "women", "Women"
        MEN = "men", "Men"
        UNISEX = "unisex", "Unisex"
        KIDS = "kids", "Kids"

    parent = models.ForeignKey("self", blank=True, null=True, on_delete=models.PROTECT, related_name="children")
    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=140, unique=True, blank=True)
    audience = models.CharField(max_length=20, choices=Audience.choices, default=Audience.WOMEN)
    description = models.TextField(blank=True)
    image = models.ImageField(upload_to="categories/", blank=True, validators=[validate_image_file_size], help_text=IMAGE_FILE_SIZE_HELP_TEXT)
    display_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    seo_title = models.CharField(max_length=70, blank=True)
    seo_description = models.CharField(max_length=170, blank=True)

    class Meta:
        verbose_name_plural = "Categories"
        ordering = ("display_order", "name")
        constraints = [models.UniqueConstraint(fields=("parent", "name"), name="unique_category_per_parent")]

    def __str__(self):
        return f"{self.parent} / {self.name}" if self.parent else self.name

    def save(self, *args, **kwargs):
        self.slug = slugify(self.name)
        super().save(*args, **kwargs)


class Product(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        ARCHIVED = "archived", "Archived"

    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="products")
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True, blank=True)
    brand = models.CharField(max_length=120, blank=True)
    short_description = models.CharField(max_length=300, blank=True)
    description = models.TextField(blank=True)
    regular_price = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0"))])
    sale_price = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True, validators=[MinValueValidator(Decimal("0"))])
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    is_featured = models.BooleanField(default=False)
    is_trending = models.BooleanField(default=False)
    is_new_arrival = models.BooleanField(default=False)
    related_products = models.ManyToManyField("self", blank=True)
    seo_title = models.CharField(max_length=70, blank=True)
    seo_description = models.CharField(max_length=170, blank=True)
    published_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return self.name

    def clean(self):
        if self.sale_price is not None and self.sale_price >= self.regular_price:
            raise ValidationError({"sale_price": "Sale price must be lower than regular price."})

    @property
    def total_stock(self):
        return self.variants.aggregate(total=Sum("stock_quantity"))["total"] or 0

    def save(self, *args, **kwargs):
        self.slug = self.slug or slugify(self.name)
        if self.status == self.Status.ACTIVE and not self.published_at:
            self.published_at = timezone.now()
        super().save(*args, **kwargs)


class ProductImage(TimeStampedModel):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="images")
    image = models.ImageField(upload_to="products/%Y/%m/", validators=[validate_image_file_size], help_text=IMAGE_FILE_SIZE_HELP_TEXT)
    alt_text = models.CharField(max_length=200, blank=True)
    display_order = models.PositiveIntegerField(default=0)
    is_primary = models.BooleanField(default=False)

    class Meta:
        ordering = ("display_order", "id")

    def __str__(self):
        return f"{self.product} image"


class ProductVariant(TimeStampedModel):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="variants")
    sku = models.CharField(max_length=80, unique=True)
    size = models.CharField(max_length=40, blank=True)
    color = models.CharField(max_length=60, blank=True)
    color_code = models.CharField(max_length=10, blank=True)
    price_override = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True, validators=[MinValueValidator(Decimal("0"))])
    stock_quantity = models.PositiveIntegerField(default=0)
    low_stock_threshold = models.PositiveIntegerField(default=5)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("product", "size", "color")
        constraints = [models.UniqueConstraint(fields=("product", "size", "color"), name="unique_product_variant")]

    def __str__(self):
        options = ", ".join(filter(None, (self.size, self.color)))
        return f"{self.product} — {options or self.sku}"

    @property
    def effective_price(self):
        return self.price_override or self.product.sale_price or self.product.regular_price

    @property
    def stock_status(self):
        if not self.stock_quantity:
            return "Out of stock"
        return "Low stock" if self.stock_quantity <= self.low_stock_threshold else "In stock"


class InventoryMovement(TimeStampedModel):
    class MovementType(models.TextChoices):
        PURCHASE = "purchase", "Purchase"
        SALE = "sale", "Sale"
        RETURN = "return", "Customer return"
        ADJUSTMENT = "adjustment", "Adjustment"
        DAMAGE = "damage", "Damage/loss"

    variant = models.ForeignKey(ProductVariant, on_delete=models.PROTECT, related_name="inventory_movements")
    movement_type = models.CharField(max_length=20, choices=MovementType.choices)
    quantity = models.IntegerField(help_text="Positive adds stock; negative removes stock.")
    stock_before = models.PositiveIntegerField()
    stock_after = models.PositiveIntegerField()
    reason = models.CharField(max_length=250)
    reference = models.CharField(max_length=100, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, blank=True, null=True, on_delete=models.SET_NULL)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.variant.sku}: {self.quantity:+d}"

    def clean(self):
        if self.stock_before is None or self.quantity is None or self.stock_after is None:
            return
        if self.stock_before + self.quantity != self.stock_after:
            raise ValidationError("Stock after must equal stock before plus quantity.")


class CustomerProfile(TimeStampedModel):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="customer_profile")
    phone = models.CharField(max_length=30, blank=True)
    date_of_birth = models.DateField(blank=True, null=True)
    marketing_consent = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.user.get_full_name() or self.user.get_username()


class UserRole(TimeStampedModel):
    class Role(models.TextChoices):
        ADMIN = "admin", "Admin"
        CUSTOMER = "customer", "Customer"
        INFLUENCER = "influencer", "Influencer"

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="fabriqx_role")
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.CUSTOMER)

    class Meta:
        verbose_name = "Role"
        verbose_name_plural = "Roles"

    def __str__(self):
        return f"{self.user.get_username()} — {self.get_role_display()}"

    def save(self, *args, **kwargs):
        if self.user.is_superuser:
            self.role = self.Role.ADMIN
        super().save(*args, **kwargs)

        from django.contrib.auth.models import Group, Permission

        role_groups = Group.objects.filter(name__in=("Admin", "Customer", "Influencer"))
        self.user.groups.remove(*role_groups)
        group, _ = Group.objects.get_or_create(name=self.get_role_display())
        # Admin/staff authorization is assigned per account. Keeping the shared
        # Admin group permission-free prevents one staff account's access from
        # leaking to every other staff account. Superusers bypass permissions.
        if self.role == self.Role.ADMIN and group.permissions.exists():
            group.permissions.clear()
        self.user.groups.add(group)

        should_be_staff = self.role == self.Role.ADMIN or self.user.is_superuser
        # Always synchronize the stored value. The related user object may be
        # cached with a stale is_staff value when a role changes during account
        # creation.
        type(self.user).objects.filter(pk=self.user_id).update(is_staff=should_be_staff)
        self.user.is_staff = should_be_staff


class Address(TimeStampedModel):
    class Type(models.TextChoices):
        SHIPPING = "shipping", "Shipping"
        BILLING = "billing", "Billing"

    customer = models.ForeignKey(CustomerProfile, on_delete=models.CASCADE, related_name="addresses")
    address_type = models.CharField(max_length=20, choices=Type.choices)
    full_name = models.CharField(max_length=150)
    phone = models.CharField(max_length=30)
    line1 = models.CharField(max_length=255)
    line2 = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=100)
    postal_code = models.CharField(max_length=20)
    country = models.CharField(max_length=100, default="India")
    is_default = models.BooleanField(default=False)

    class Meta:
        verbose_name_plural = "Addresses"

    def __str__(self):
        return f"{self.full_name}, {self.city}"


class WishlistItem(TimeStampedModel):
    customer = models.ForeignKey(CustomerProfile, on_delete=models.CASCADE, related_name="wishlist_items")
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="wishlisted_by")
    variant = models.ForeignKey(ProductVariant, blank=True, null=True, on_delete=models.CASCADE)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("customer", "product", "variant"), name="unique_wishlist_item")]

    def __str__(self):
        return f"{self.customer} — {self.product}"


class Cart(TimeStampedModel):
    customer = models.OneToOneField(CustomerProfile, on_delete=models.CASCADE, related_name="cart")
    coupon = models.ForeignKey("Coupon", blank=True, null=True, on_delete=models.SET_NULL, related_name="carts")

    def __str__(self):
        return f"Cart for {self.customer}"


class CartItem(TimeStampedModel):
    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name="items")
    variant = models.ForeignKey(ProductVariant, on_delete=models.CASCADE, related_name="cart_items")
    quantity = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])

    class Meta:
        constraints = [models.UniqueConstraint(fields=("cart", "variant"), name="unique_cart_variant")]

    def __str__(self):
        return f"{self.variant} × {self.quantity}"


class Coupon(TimeStampedModel):
    class DiscountType(models.TextChoices):
        PERCENTAGE = "percentage", "Percentage"
        FIXED = "fixed", "Fixed amount"

    code = models.CharField(max_length=50, unique=True)
    description = models.CharField(max_length=250, blank=True)
    discount_type = models.CharField(max_length=20, choices=DiscountType.choices)
    discount_value = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    minimum_order_value = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    maximum_discount = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True)
    starts_at = models.DateTimeField()
    expires_at = models.DateTimeField()
    total_usage_limit = models.PositiveIntegerField(blank=True, null=True)
    per_customer_limit = models.PositiveIntegerField(default=1)
    categories = models.ManyToManyField(Category, blank=True)
    products = models.ManyToManyField(Product, blank=True)
    influencers = models.ManyToManyField("InfluencerProfile", blank=True, related_name="coupons")
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.code

    def clean(self):
        errors = {}
        if (
            self._state.adding
            and self.starts_at is not None
            and timezone.localdate(self.starts_at) < timezone.localdate()
        ):
            errors["starts_at"] = "Start date cannot be earlier than today."
        if self.starts_at is not None and self.expires_at is not None and self.expires_at <= self.starts_at:
            errors["expires_at"] = "Expiry must be after the start time."
        if (
            self.discount_type == self.DiscountType.PERCENTAGE
            and self.discount_value is not None
            and self.discount_value > 100
        ):
            errors["discount_value"] = "Percentage cannot exceed 100."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.code = self.code.strip().upper()
        super().save(*args, **kwargs)


class InfluencerProfile(TimeStampedModel):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="influencer_profile")
    affiliate_id = models.CharField(max_length=40, unique=True, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    profile_image = models.ImageField(upload_to="influencers/profiles/", blank=True, validators=[validate_image_file_size], help_text=IMAGE_FILE_SIZE_HELP_TEXT)
    address = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.user.get_full_name() or self.user.username} ({self.affiliate_id})"

    def save(self, *args, **kwargs):
        self.affiliate_id = self.affiliate_id or reference("INF")
        # Optional JSON form fields are cleaned to None when left empty, but
        # this column deliberately remains NOT NULL in the database.
        if self.address is None:
            self.address = {}
        super().save(*args, **kwargs)


class SiteSettings(TimeStampedModel):
    class CommissionType(models.TextChoices):
        PERCENTAGE = "percentage", "Percentage"
        FIXED = "fixed", "Fixed amount"

    commission_type = models.CharField(max_length=20, choices=CommissionType.choices, default=CommissionType.PERCENTAGE)
    commission_rate = models.DecimalField("Commission percentage (%)", max_digits=5, decimal_places=2, default=10, validators=[MinValueValidator(0), MaxValueValidator(100)])
    commission_fixed_amount = models.DecimalField("Fixed commission amount", max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    legacy_influencer_commissions = models.JSONField(default=dict, blank=True, editable=False)

    class Meta:
        verbose_name = "Site settings"
        verbose_name_plural = "Site settings"

    def __str__(self):
        return "Site settings"

    @classmethod
    def load(cls):
        settings_object, _ = cls.objects.get_or_create(pk=1)
        return settings_object

    def clean(self):
        if self.commission_type == self.CommissionType.PERCENTAGE and self.commission_rate <= 0:
            raise ValidationError({"commission_rate": "Enter a percentage greater than zero."})
        if self.commission_type == self.CommissionType.FIXED and self.commission_fixed_amount <= 0:
            raise ValidationError({"commission_fixed_amount": "Enter a fixed amount greater than zero."})

    def save(self, *args, **kwargs):
        self.pk = 1
        if self.commission_type == self.CommissionType.PERCENTAGE:
            self.commission_fixed_amount = 0
        else:
            self.commission_rate = 0
        super().save(*args, **kwargs)


class Order(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        CONFIRMED = "confirmed", "Confirmed"
        PROCESSING = "processing", "Processing"
        SHIPPED = "shipped", "Shipped"
        DELIVERED = "delivered", "Delivered"
        CANCELLED = "cancelled", "Cancelled"
        RETURN_REQUESTED = "return_requested", "Return requested"
        RETURNED = "returned", "Returned"
        REFUNDED = "refunded", "Refunded"

    number = models.CharField(max_length=40, unique=True, blank=True)
    customer = models.ForeignKey(CustomerProfile, blank=True, null=True, on_delete=models.PROTECT, related_name="orders")
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.PENDING)
    email = models.EmailField()
    phone = models.CharField(max_length=30)
    shipping_address = models.JSONField(default=dict)
    billing_address = models.JSONField(default=dict)
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    shipping_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tax_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    grand_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    coupon = models.ForeignKey(Coupon, blank=True, null=True, on_delete=models.SET_NULL, related_name="orders")
    influencer = models.ForeignKey(InfluencerProfile, blank=True, null=True, on_delete=models.SET_NULL, related_name="orders")
    affiliate_code = models.CharField(max_length=40, blank=True, db_index=True, help_text="Affiliate ID entered by the customer during checkout.")
    customer_note = models.TextField(blank=True)
    admin_note = models.TextField(blank=True)
    placed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ("-placed_at",)

    def __str__(self):
        return self.number

    def clean(self):
        total = self.subtotal - self.discount_total + self.shipping_total + self.tax_total
        if self.grand_total != total:
            raise ValidationError({"grand_total": f"Grand total must be {total}."})
        if self.affiliate_code:
            try:
                self.influencer = InfluencerProfile.objects.get(affiliate_id__iexact=self.affiliate_code.strip(), is_active=True)
            except InfluencerProfile.DoesNotExist as exc:
                raise ValidationError({"affiliate_code": "Enter a valid, active influencer affiliate ID."}) from exc
            self.affiliate_code = self.influencer.affiliate_id
        elif self.influencer_id:
            self.affiliate_code = self.influencer.affiliate_id

    def save(self, *args, **kwargs):
        self.number = self.number or reference("ORD")
        if self.affiliate_code:
            try:
                self.influencer = InfluencerProfile.objects.get(affiliate_id__iexact=self.affiliate_code.strip(), is_active=True)
            except InfluencerProfile.DoesNotExist as exc:
                raise ValidationError({"affiliate_code": "Enter a valid, active influencer affiliate ID."}) from exc
            self.affiliate_code = self.influencer.affiliate_id
        elif self.influencer_id:
            self.affiliate_code = self.influencer.affiliate_id
        super().save(*args, **kwargs)


class OrderItem(TimeStampedModel):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    variant = models.ForeignKey(ProductVariant, on_delete=models.PROTECT, related_name="order_items")
    product_name = models.CharField(max_length=200)
    sku = models.CharField(max_length=80)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    total = models.DecimalField(max_digits=12, decimal_places=2)

    def __str__(self):
        return f"{self.order} — {self.product_name}"

    def clean(self):
        expected = self.unit_price * self.quantity
        if self.total != expected:
            raise ValidationError({"total": f"Item total must be {expected}."})


class OrderStatusHistory(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="status_history")
    from_status = models.CharField(max_length=30, choices=Order.Status.choices, blank=True)
    to_status = models.CharField(max_length=30, choices=Order.Status.choices)
    note = models.CharField(max_length=250, blank=True)
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, blank=True, null=True, on_delete=models.SET_NULL)
    changed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-changed_at",)
        verbose_name_plural = "Order status histories"

    def __str__(self):
        return f"{self.order}: {self.from_status or 'New'} → {self.to_status}"


class CouponUsage(models.Model):
    coupon = models.ForeignKey(Coupon, on_delete=models.PROTECT, related_name="usages")
    customer = models.ForeignKey(CustomerProfile, blank=True, null=True, on_delete=models.SET_NULL)
    order = models.OneToOneField(Order, on_delete=models.CASCADE, related_name="coupon_usage")
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2)
    used_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.coupon} on {self.order}"


class Payment(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PAID = "paid", "Paid"
        FAILED = "failed", "Failed"
        REFUNDED = "refunded", "Refunded"
        PARTIALLY_REFUNDED = "partially_refunded", "Partially refunded"

    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="payments")
    provider = models.CharField(max_length=40)
    transaction_id = models.CharField(max_length=150, blank=True, db_index=True)
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.PENDING)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    paid_at = models.DateTimeField(blank=True, null=True)
    gateway_response = models.JSONField(default=dict, blank=True)
    reconciliation_note = models.TextField(blank=True)

    def __str__(self):
        return f"{self.order} — {self.provider} ({self.status})"


class Refund(TimeStampedModel):
    class Status(models.TextChoices):
        REQUESTED = "requested", "Requested"
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    payment = models.ForeignKey(Payment, on_delete=models.PROTECT, related_name="refunds")
    amount = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    reason = models.TextField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.REQUESTED)
    provider_reference = models.CharField(max_length=150, blank=True)
    processed_at = models.DateTimeField(blank=True, null=True)

    def __str__(self):
        return f"Refund {self.amount} for {self.payment.order}"


class Invoice(TimeStampedModel):
    order = models.OneToOneField(Order, on_delete=models.PROTECT, related_name="invoice")
    number = models.CharField(max_length=40, unique=True, blank=True)
    issued_at = models.DateTimeField(default=timezone.now)
    billing_details = models.JSONField(default=dict)
    tax_identifier = models.CharField(max_length=60, blank=True)

    def __str__(self):
        return self.number

    def save(self, *args, **kwargs):
        self.number = self.number or reference("INV")
        super().save(*args, **kwargs)


class InfluencerCommission(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        PAID = "paid", "Paid"
        REJECTED = "rejected", "Rejected"
        REVERSED = "reversed", "Reversed"

    influencer = models.ForeignKey(InfluencerProfile, on_delete=models.PROTECT, related_name="commissions")
    order = models.OneToOneField(Order, on_delete=models.PROTECT, related_name="commission")
    rate = models.DecimalField(max_digits=5, decimal_places=2, validators=[MinValueValidator(0), MaxValueValidator(100)])
    eligible_amount = models.DecimalField(max_digits=12, decimal_places=2)
    commission_amount = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    payment_reference = models.CharField(max_length=150, blank=True)
    paid_at = models.DateTimeField(blank=True, null=True)
    note = models.TextField(blank=True)

    def __str__(self):
        return f"{self.influencer} — {self.commission_amount}"

    def clean(self):
        if self.commission_amount < 0:
            raise ValidationError({"commission_amount": "Commission cannot be negative."})


class Review(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="reviews")
    customer = models.ForeignKey(CustomerProfile, on_delete=models.PROTECT, related_name="reviews")
    order_item = models.OneToOneField(OrderItem, blank=True, null=True, on_delete=models.SET_NULL)
    rating = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    title = models.CharField(max_length=150)
    body = models.TextField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    is_verified_purchase = models.BooleanField(default=False)
    admin_response = models.TextField(blank=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [models.UniqueConstraint(fields=("product", "customer"), name="one_review_per_customer_product")]

    def __str__(self):
        return f"{self.product} — {self.rating}/5"


class ProductQuestion(TimeStampedModel):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="questions")
    customer = models.ForeignKey(CustomerProfile, on_delete=models.PROTECT, related_name="product_questions")
    question = models.TextField()
    answer = models.TextField(blank=True)
    is_published = models.BooleanField(default=False)
    answered_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return f"Question about {self.product}"


class Banner(TimeStampedModel):
    title = models.CharField(max_length=150)
    subtitle = models.CharField(max_length=250, blank=True)
    image = models.ImageField(upload_to="banners/", validators=[validate_image_file_size, validate_banner_logo_image], help_text=BANNER_LOGO_IMAGE_HELP_TEXT)
    link = models.CharField(max_length=300, blank=True)
    display_order = models.PositiveIntegerField(default=0)
    starts_at = models.DateTimeField(blank=True, null=True)
    ends_at = models.DateTimeField(blank=True, null=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("display_order", "-created_at")

    def __str__(self):
        return self.title

    def clean_fields(self, exclude=None):
        super().clean_fields(exclude=exclude)
        excluded = set(exclude or ())
        errors = {}
        if (
            "starts_at" not in excluded
            and self._state.adding
            and self.starts_at
            and timezone.localdate(self.starts_at) < timezone.localdate()
        ):
            errors["starts_at"] = "Start date cannot be earlier than today."
        if (
            not {"starts_at", "ends_at"} & excluded
            and self.starts_at and self.ends_at
            and self.ends_at < self.starts_at
        ):
            errors["ends_at"] = "End date cannot be earlier than the start date."
        if errors:
            raise ValidationError(errors)


class HomepageSection(TimeStampedModel):
    class Type(models.TextChoices):
        CATEGORY = "categories", "Category tiles"
        FEATURED = "featured", "Featured products"
        TRENDING = "trending", "Trending products"
        OFFERS = "offers", "Offers strip"
        NEW = "new", "New arrivals"
        TESTIMONIALS = "testimonials", "Testimonials"
        NEWSLETTER = "newsletter", "Newsletter"

    title = models.CharField(max_length=150)
    section_type = models.CharField(max_length=30, choices=Type.choices)
    content = models.JSONField(default=dict, blank=True)
    editor_content = models.TextField(
        "Introduction / custom text",
        blank=True,
        help_text="Optional formatted text displayed with this section.",
    )
    display_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("display_order",)

    def __str__(self):
        return self.title


class GiftSection(TimeStampedModel):
    """A fully editable promotional gift block for the storefront homepage."""

    internal_name = models.CharField(max_length=150, help_text="Only used to identify this campaign in admin.")
    badge_eyebrow = models.CharField(max_length=100, blank=True, default="With every order")
    badge_title = models.CharField(max_length=100, blank=True, default="Free gift")
    badge_icon = models.ImageField(upload_to="gift-sections/badges/", blank=True, validators=[validate_image_file_size], help_text=IMAGE_FILE_SIZE_HELP_TEXT)
    logo = models.ImageField(upload_to="gift-sections/logos/", blank=True, validators=[validate_image_file_size], help_text=IMAGE_FILE_SIZE_HELP_TEXT)
    accent_heading = models.CharField(max_length=150, blank=True, default="Shop More,")
    heading = models.CharField(max_length=200, default="Get More Joy!")
    description = models.TextField(blank=True, default="Every purchase comes with a free gift, just for you!")
    main_image = models.ImageField(upload_to="gift-sections/main/", validators=[validate_image_file_size], help_text=IMAGE_FILE_SIZE_HELP_TEXT)
    gift_image = models.ImageField(upload_to="gift-sections/gifts/", blank=True, validators=[validate_image_file_size], help_text=IMAGE_FILE_SIZE_HELP_TEXT)
    background_image = models.ImageField(upload_to="gift-sections/backgrounds/", blank=True, validators=[validate_image_file_size], help_text=IMAGE_FILE_SIZE_HELP_TEXT)
    thank_you_title = models.CharField(max_length=200, blank=True, default="Thank you for choosing Fabriqx.")
    thank_you_text = models.TextField(blank=True, default="Your love inspires us to keep creating styles that make every moment special.")
    cta_label = models.CharField(max_length=150, blank=True, default="Shop now & get your free gift")
    cta_url = models.CharField(max_length=300, blank=True)
    display_order = models.PositiveIntegerField(default=0)
    starts_at = models.DateTimeField(blank=True, null=True)
    ends_at = models.DateTimeField(blank=True, null=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("display_order", "-created_at")

    def __str__(self):
        return self.internal_name

    def clean(self):
        errors = {}
        if (
            self._state.adding
            and self.starts_at
            and timezone.localdate(self.starts_at) < timezone.localdate()
        ):
            errors["starts_at"] = "Start date cannot be earlier than today."
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            errors["ends_at"] = "End time must be after the start time."
        if errors:
            raise ValidationError(errors)


class GiftSectionFeature(TimeStampedModel):
    section = models.ForeignKey(GiftSection, on_delete=models.CASCADE, related_name="features")
    icon = models.ImageField(upload_to="gift-sections/features/", blank=True, validators=[validate_image_file_size], help_text=IMAGE_FILE_SIZE_HELP_TEXT)
    text = models.CharField(max_length=150)
    display_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("display_order", "id")

    def __str__(self):
        return self.text


class GiftSectionStatistic(TimeStampedModel):
    section = models.ForeignKey(GiftSection, on_delete=models.CASCADE, related_name="statistics")
    icon = models.ImageField(upload_to="gift-sections/statistics/", blank=True, validators=[validate_image_file_size], help_text=IMAGE_FILE_SIZE_HELP_TEXT)
    eyebrow = models.CharField(max_length=100, blank=True)
    value = models.CharField(max_length=50)
    label = models.CharField(max_length=100)
    display_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("display_order", "id")

    def __str__(self):
        return f"{self.value} {self.label}"


class BrandLogoSection(TimeStampedModel):
    internal_name = models.CharField(max_length=150, default="Homepage brand logos", help_text="Only used to identify this section in admin.")
    heading = models.CharField(max_length=150, blank=True)
    background_color = models.CharField(max_length=20, blank=True, default="#f7e8d8")
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.internal_name


class BrandLogo(TimeStampedModel):
    section = models.ForeignKey(BrandLogoSection, on_delete=models.CASCADE, related_name="logos")
    brand_name = models.CharField(max_length=120)
    logo = models.ImageField(upload_to="brand-logos/", validators=[validate_image_file_size, validate_banner_logo_image], help_text=BANNER_LOGO_IMAGE_HELP_TEXT)
    alt_text = models.CharField(max_length=180, blank=True)
    url = models.CharField(max_length=300, blank=True)
    open_in_new_tab = models.BooleanField(default=False)
    display_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("display_order", "id")

    def __str__(self):
        return self.brand_name

    def save(self, *args, **kwargs):
        self.alt_text = self.alt_text or self.brand_name
        super().save(*args, **kwargs)


class OfferBanner(TimeStampedModel):
    internal_name = models.CharField(max_length=150, help_text="Only used to identify this banner in admin.")
    desktop_image = models.ImageField(upload_to="offers/banners/desktop/", validators=[validate_image_file_size], help_text=IMAGE_FILE_SIZE_HELP_TEXT)
    mobile_image = models.ImageField(upload_to="offers/banners/mobile/", blank=True, validators=[validate_image_file_size], help_text=IMAGE_FILE_SIZE_HELP_TEXT)
    alt_text = models.CharField(max_length=180, blank=True)
    shop_now_url = models.CharField("Shop now URL", max_length=300)
    open_in_new_tab = models.BooleanField(default=False)
    display_order = models.PositiveIntegerField(default=0)
    starts_at = models.DateTimeField(blank=True, null=True)
    ends_at = models.DateTimeField(blank=True, null=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("display_order", "id")

    def __str__(self):
        return self.internal_name

    def clean(self):
        errors = {}
        if (
            self._state.adding
            and self.starts_at
            and timezone.localdate(self.starts_at) < timezone.localdate()
        ):
            errors["starts_at"] = "Start date cannot be earlier than today."
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            errors["ends_at"] = "End time must be after the start time."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.alt_text = self.alt_text or self.internal_name
        super().save(*args, **kwargs)


class OfferGridSection(TimeStampedModel):
    internal_name = models.CharField(max_length=150, default="Homepage offer grid", help_text="Only used to identify this section in admin.")
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.internal_name


class OfferGridItem(TimeStampedModel):
    section = models.ForeignKey(OfferGridSection, on_delete=models.CASCADE, related_name="items")
    internal_name = models.CharField(max_length=150)
    desktop_image = models.ImageField(upload_to="offers/grid/desktop/", validators=[validate_image_file_size], help_text=IMAGE_FILE_SIZE_HELP_TEXT)
    mobile_image = models.ImageField(upload_to="offers/grid/mobile/", blank=True, validators=[validate_image_file_size], help_text=IMAGE_FILE_SIZE_HELP_TEXT)
    alt_text = models.CharField(max_length=180, blank=True)
    shop_now_url = models.CharField("Shop now URL", max_length=300)
    open_in_new_tab = models.BooleanField(default=False)
    display_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("display_order", "id")

    def __str__(self):
        return self.internal_name

    def save(self, *args, **kwargs):
        self.alt_text = self.alt_text or self.internal_name
        super().save(*args, **kwargs)


class FooterSocialSection(TimeStampedModel):
    internal_name = models.CharField(max_length=150, default="Footer social links", help_text="Only used to identify this section in admin.")
    heading = models.CharField(max_length=100, default="SOCIAL")
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.internal_name


class FooterSocialLink(TimeStampedModel):
    section = models.ForeignKey(FooterSocialSection, on_delete=models.CASCADE, related_name="links")
    platform_name = models.CharField(max_length=80)
    icon = models.ImageField(upload_to="footer/social-icons/", validators=[validate_image_file_size], help_text=IMAGE_FILE_SIZE_HELP_TEXT)
    url = models.URLField(max_length=300)
    aria_label = models.CharField("Accessibility label", max_length=120, blank=True)
    display_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("display_order", "id")

    def __str__(self):
        return self.platform_name

    def save(self, *args, **kwargs):
        self.aria_label = self.aria_label or self.platform_name
        super().save(*args, **kwargs)


class Testimonial(TimeStampedModel):
    customer_name = models.CharField(
        max_length=150,
        validators=[validate_customer_name],
    )
    image = models.ImageField(
        upload_to="testimonials/",
        blank=True,
        validators=[validate_image_file_size],
        help_text=IMAGE_FILE_SIZE_HELP_TEXT,
    )
    sub_text = models.CharField(
        max_length=200,
        blank=True,
        help_text="Optional short text shown below the customer name.",
    )
    content = models.TextField()
    rating = models.SmallIntegerField(
        default=5,
        validators=[
            MinValueValidator(1),
            MaxValueValidator(5),
        ],
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return self.customer_name


class NewsletterSubscription(TimeStampedModel):
    email = models.EmailField(unique=True)
    is_active = models.BooleanField(default=True)
    source = models.CharField(max_length=80, blank=True)

    def __str__(self):
        return self.email


class NewsletterSettings(TimeStampedModel):
    title = models.CharField(max_length=150, default="Join our newsletter")
    description = models.TextField(blank=True)

    class Meta:
        verbose_name_plural = "Newsletter settings"

    def __str__(self):
        return self.title


class Page(TimeStampedModel):
    title = models.CharField(max_length=180)
    slug = models.SlugField(max_length=200, unique=True, blank=True)
    content = models.TextField()
    seo_title = models.CharField(max_length=70, blank=True)
    seo_description = models.CharField(max_length=170, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("title",)

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        self.slug = self.slug or slugify(self.title)
        super().save(*args, **kwargs)


class Shipment(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        READY = "ready", "Ready to ship"
        SHIPPED = "shipped", "Shipped"
        IN_TRANSIT = "in_transit", "In transit"
        DELIVERED = "delivered", "Delivered"
        RTO = "rto", "Returned to origin"
        CANCELLED = "cancelled", "Cancelled"

    order = models.OneToOneField(Order, on_delete=models.PROTECT, related_name="shipment")
    provider = models.CharField(max_length=50, default="Shiprocket")
    shipment_id = models.CharField(max_length=100, blank=True)
    courier = models.CharField(max_length=100, blank=True)
    tracking_number = models.CharField(max_length=120, blank=True)
    tracking_url = models.URLField(blank=True)
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.PENDING)
    shipping_charge = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    dispatched_at = models.DateTimeField(blank=True, null=True)
    delivered_at = models.DateTimeField(blank=True, null=True)
    provider_response = models.JSONField(default=dict, blank=True)

    def __str__(self):
        return f"{self.order} — {self.status}"


class ServiceablePincode(TimeStampedModel):
    pincode = models.CharField(max_length=12, unique=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=100, blank=True)
    cash_on_delivery = models.BooleanField(default=False)
    estimated_days = models.PositiveSmallIntegerField(default=5)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.pincode


class IntegrationEvent(TimeStampedModel):
    class Direction(models.TextChoices):
        OUTBOUND = "outbound", "Outbound"
        INBOUND = "inbound", "Inbound/webhook"

    provider = models.CharField(max_length=50)
    event_type = models.CharField(max_length=100)
    direction = models.CharField(max_length=20, choices=Direction.choices)
    external_id = models.CharField(max_length=150, blank=True, db_index=True)
    payload = models.JSONField(default=dict)
    response = models.JSONField(default=dict, blank=True)
    status_code = models.PositiveSmallIntegerField(blank=True, null=True)
    succeeded = models.BooleanField(default=False)
    error_message = models.TextField(blank=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.provider}: {self.event_type}"


class AuditLog(models.Model):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, blank=True, null=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=50)
    model_name = models.CharField(max_length=100)
    object_id = models.CharField(max_length=100)
    object_repr = models.CharField(max_length=250)
    changes = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.actor or 'System'} {self.action} {self.object_repr}"


class SalesReport(Order):
    class Meta:
        proxy = True
        verbose_name = "Sales report"
        verbose_name_plural = "Sales reports"


class InventoryReport(ProductVariant):
    class Meta:
        proxy = True
        verbose_name = "Inventory report"
        verbose_name_plural = "Inventory reports"


class InfluencerReport(InfluencerCommission):
    class Meta:
        proxy = True
        verbose_name = "Influencer report"
        verbose_name_plural = "Influencer reports"
