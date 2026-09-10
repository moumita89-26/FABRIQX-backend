from django.contrib import admin

from fabriqx import models as base
from fabriqx.admin import (
    BannerAdmin,
    BrandLogoSectionAdmin,
    BrandLogoAdmin,
    ContactSubmissionAdmin,
    FooterSocialSectionAdmin,
    GiftSectionAdmin,
    NewsletterAdmin,
    NewsletterSettingsAdmin,
    OfferBannerAdmin,
    OfferGridItemListAdmin,
    PageAdmin,
    SectionAdmin,
    TestimonialAdmin,
)

from .models import (
    Banner,
    BrandLogoSection,
    BrandLogo,
    ContactSubmission,
    FooterSocialSection,
    GiftSection,
    HomepageSection,
    NewsletterSettings,
    NewsletterSubscription,
    OfferBanner,
    OfferGridItem,
    Page,
    Testimonial,
)


for model in (
    base.Banner,
    base.ContactSubmission,
    base.BrandLogoSection,
    base.FooterSocialSection,
    base.GiftSection,
    base.HomepageSection,
    base.NewsletterSettings,
    base.OfferBanner,
    base.OfferGridSection,
    base.OfferGridItem,
    base.Testimonial,
    base.NewsletterSubscription,
    base.Page,
):
    if admin.site.is_registered(model):
        admin.site._registry[model].hide_from_index = True


admin.site.register(Banner, BannerAdmin)
admin.site.register(HomepageSection, SectionAdmin)
admin.site.register(GiftSection, GiftSectionAdmin)
admin.site.register(BrandLogoSection, BrandLogoSectionAdmin)
admin.site.register(OfferBanner, OfferBannerAdmin)
admin.site.register(OfferGridItem, OfferGridItemListAdmin)
admin.site.register(FooterSocialSection, FooterSocialSectionAdmin)
admin.site.register(Testimonial, TestimonialAdmin)
admin.site.register(
    NewsletterSubscription,
    NewsletterAdmin,
)
admin.site.register(
    NewsletterSettings,
    NewsletterSettingsAdmin,
)
admin.site.register(Page, PageAdmin)
admin.site.register(ContactSubmission, ContactSubmissionAdmin)
admin.site.register(BrandLogo, BrandLogoAdmin)
