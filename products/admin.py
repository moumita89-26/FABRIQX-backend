from django.contrib import admin

from fabriqx import models as base
from fabriqx.admin import CategoryAdmin, CouponAdmin, InventoryAdmin, InventoryReportAdmin, ProductAdmin, SimpleAdmin, VariantAdmin

from .models import Category, Coupon, InventoryMovement, InventoryReport, Product, ProductImage, ProductVariant


for model in (base.Category, base.Product, base.ProductImage, base.ProductVariant, base.Coupon, base.InventoryMovement, base.InventoryReport):
    if admin.site.is_registered(model):
        admin.site._registry[model].hide_from_index = True


class HiddenProductImageAdmin(SimpleAdmin):
    hide_from_index = True


class HiddenProductVariantAdmin(VariantAdmin):
    hide_from_index = True


admin.site.register(Category, CategoryAdmin)
admin.site.register(Product, ProductAdmin)
admin.site.register(ProductImage, HiddenProductImageAdmin)
admin.site.register(ProductVariant, HiddenProductVariantAdmin)
admin.site.register(Coupon, CouponAdmin)
admin.site.register(InventoryMovement, InventoryAdmin)
admin.site.register(InventoryReport, InventoryReportAdmin)
