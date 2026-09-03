from django.contrib import admin

from fabriqx import models as base
from fabriqx.admin import CustomerAdmin, SimpleAdmin

from .models import Address, CustomerProfile, WishlistItem


for model in (base.CustomerProfile, base.Address, base.WishlistItem):
    if admin.site.is_registered(model):
        admin.site._registry[model].hide_from_index = True


admin.site.register(CustomerProfile, CustomerAdmin)
admin.site.register(Address, SimpleAdmin)
admin.site.register(WishlistItem, SimpleAdmin)

# Addresses are edited inline inside Manage Customers.
admin.site._registry[Address].hide_from_index = True
