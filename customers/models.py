from fabriqx import models as base


class CustomerProfile(base.CustomerProfile):
    class Meta:
        proxy = True
        verbose_name = "Customer"
        verbose_name_plural = "Manage Customers"


class Address(base.Address):
    class Meta:
        proxy = True
        verbose_name_plural = "Addresses"


class WishlistItem(base.WishlistItem):
    class Meta:
        proxy = True
        verbose_name = "Wishlist item"
        verbose_name_plural = "Wishlist items"
