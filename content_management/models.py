from fabriqx import models as base


class Banner(base.Banner):
    class Meta:
        proxy = True
        verbose_name = "Hero banner"
        verbose_name_plural = "Hero banners"


class HomepageSection(base.HomepageSection):
    class Meta:
        proxy = True
        verbose_name = "Homepage section setting"
        verbose_name_plural = "Homepage section settings"


class GiftSection(base.GiftSection):
    class Meta:
        proxy = True
        verbose_name = "Homepage gift section"
        verbose_name_plural = "Homepage gift sections"


class BrandLogoSection(base.BrandLogoSection):
    class Meta:
        proxy = True
        verbose_name = "Brand logo section"
        verbose_name_plural = "Brand logo section"


class OfferBanner(base.OfferBanner):
    class Meta:
        proxy = True
        verbose_name = "Offer banner"
        verbose_name_plural = "Offer banners"


class OfferGridSection(base.OfferGridSection):
    class Meta:
        proxy = True
        verbose_name = "Offer grid"
        verbose_name_plural = "Offer grid"


class FooterSocialSection(base.FooterSocialSection):
    class Meta:
        proxy = True
        verbose_name = "Footer social links"
        verbose_name_plural = "Footer social links"


class Testimonial(base.Testimonial):
    class Meta:
        proxy = True


class NewsletterSubscription(base.NewsletterSubscription):
    class Meta:
        proxy = True
        verbose_name = "Newsletter subscription"
        verbose_name_plural = "Newsletter subscriptions"


class NewsletterSettings(base.NewsletterSettings):
    class Meta:
        proxy = True
        verbose_name = "Newsletter settings"
        verbose_name_plural = "Newsletter settings"


class Page(base.Page):
    class Meta:
        proxy = True


class BrandLogo(base.BrandLogo):
    class Meta:
        proxy = True
        verbose_name = "Brand logo"
        verbose_name_plural = "Brand Logo Section"
