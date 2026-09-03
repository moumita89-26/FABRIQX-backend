from django.contrib import admin

from fabriqx import models as base
from fabriqx.admin import CommissionAdmin, InfluencerAdmin, InfluencerReportAdmin

from .models import InfluencerCommission, InfluencerProfile, InfluencerReport


for model in (base.InfluencerProfile, base.InfluencerCommission, base.InfluencerReport):
    if admin.site.is_registered(model):
        admin.site._registry[model].hide_from_index = True


admin.site.register(InfluencerProfile, InfluencerAdmin)
admin.site.register(InfluencerCommission, CommissionAdmin)
admin.site.register(InfluencerReport, InfluencerReportAdmin)
