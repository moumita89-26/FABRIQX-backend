from django.contrib import admin

from fabriqx import models as base
from fabriqx.admin import (
    CategoryAdmin,
    CouponAdmin,
    InventoryAdmin,
    InventoryReportAdmin,
    ProductAdmin,
    ProductFAQAdmin,
    ReviewAdmin,
    SimpleAdmin,
    VariantAdmin,
)

from .models import (
    Category,
    Coupon,
    InventoryMovement,
    InventoryReport,
    Product,
    ProductFAQ,
    ProductImage,
    ProductVariant,
    Review,
)


# Hide original fabriqx models from admin sidebar because proxy models
# from the products application are being used instead.
for model in (
    base.Category,
    base.Product,
    base.ProductImage,
    base.ProductVariant,
    base.Coupon,
    base.InventoryMovement,
    base.InventoryReport,
    base.ProductFAQ,
    base.Review,
):
    if admin.site.is_registered(model):
        admin.site._registry[model].hide_from_index = True


class HiddenProductImageAdmin(SimpleAdmin):
    hide_from_index = True


class HiddenProductVariantAdmin(VariantAdmin):
    hide_from_index = True


class ReadOnlyInventoryAdmin(InventoryAdmin):
    """
    Inventory movements are system-generated records.

    Admin users can:
    - View the inventory movement list
    - Search inventory movements
    - Filter inventory movements

    Admin users cannot:
    - Add inventory movements
    - Edit inventory movements
    - Delete inventory movements

    Because both change and delete permissions return False,
    the global BaseAdmin Action column will also not be displayed.
    """

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


admin.site.register(Category, CategoryAdmin)

admin.site.register(
    Product,
    ProductAdmin,
)

admin.site.register(
    ProductImage,
    HiddenProductImageAdmin,
)

admin.site.register(
    ProductVariant,
    HiddenProductVariantAdmin,
)

admin.site.register(
    Coupon,
    CouponAdmin,
)

admin.site.register(
    InventoryMovement,
    ReadOnlyInventoryAdmin,
)

admin.site.register(
    InventoryReport,
    InventoryReportAdmin,
)

admin.site.register(ProductFAQ, ProductFAQAdmin)
admin.site.register(Review, ReviewAdmin)
