from fabriqx import models as base


class InfluencerProfile(base.InfluencerProfile):
    class Meta:
        proxy = True
        verbose_name = "Influencer"
        verbose_name_plural = "Manage Influencers"


class InfluencerCommission(base.InfluencerCommission):
    class Meta:
        proxy = True
        verbose_name = "Influencer commission"
        verbose_name_plural = "Influencer commissions"


class InfluencerReport(base.InfluencerReport):
    class Meta:
        proxy = True
        verbose_name = "Influencer report"
        verbose_name_plural = "Influencer reports"
