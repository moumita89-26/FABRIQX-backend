from fabriqx import models as base


class Category(base.Category):
    class Meta:
        proxy = True
        verbose_name_plural = "Categories"


class Product(base.Product):
    class Meta:
        proxy = True


class ProductImage(base.ProductImage):
    class Meta:
        proxy = True


class ProductVariant(base.ProductVariant):
    class Meta:
        proxy = True


class Coupon(base.Coupon):
    class Meta:
        proxy = True
        verbose_name = "Manage coupon"
        verbose_name_plural = "Manage coupons"


class InventoryMovement(base.InventoryMovement):
    class Meta:
        proxy = True


class InventoryReport(base.InventoryReport):
    class Meta:
        proxy = True
        verbose_name = "Inventory report"
        verbose_name_plural = "Inventory reports"
