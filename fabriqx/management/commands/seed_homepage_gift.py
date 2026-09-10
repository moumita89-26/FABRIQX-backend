from django.core.management.base import BaseCommand
from django.db import transaction

from fabriqx.models import GiftSection, GiftSectionFeature, GiftSectionStatistic


class Command(BaseCommand):
    help = "Create or refresh the complete homepage free-gift campaign."

    @transaction.atomic
    def handle(self, *args, **options):
        section, _ = GiftSection.objects.update_or_create(
            internal_name="Shop More, Get More Joy!",
            defaults={
                "badge_eyebrow": "With every order",
                "badge_title": "Free gift",
                "badge_icon": "gift-sections/badges/cat-12.png",
                "accent_heading": "Shop More,",
                "heading": "Get More Joy!",
                "description": "Every Purchase Comes With A Free Gift, Just For You!",
                "main_image": "gift-sections/main/about-img.webp",
                "gift_image": "gift-sections/gifts/free-gift.png",
                "thank_you_title": "Thank you for choosing Fabriqx.",
                "thank_you_text": "Your love inspires us to keep creating styles that make every moment special.",
                "cta_label": "Shop now & get your free gift",
                "cta_url": "/products/",
                "display_order": 0,
                "is_active": True,
            },
        )

        # Refresh only this campaign's child rows, making this command safe to
        # run repeatedly while leaving other gift campaigns untouched.
        section.features.all().delete()
        section.statistics.all().delete()

        for order, text in enumerate((
            "Free Gift With Every Purchase",
            "Premium Products",
            "Made For You",
            "Made With Love",
            "Crafted for every moment",
        )):
            GiftSectionFeature.objects.create(
                section=section, text=text, display_order=order, is_active=True,
            )

        for order, (eyebrow, value, label) in enumerate((
            ("", "12K+", "Happy Customers"),
            ("", "840+", "Artisan Partners"),
            ("", "32", "States Delivered"),
        )):
            GiftSectionStatistic.objects.create(
                section=section,
                eyebrow=eyebrow,
                value=value,
                label=label,
                display_order=order,
                is_active=True,
            )

        self.stdout.write(self.style.SUCCESS(
            f"Homepage gift campaign ready (id={section.pk}, 5 features, 3 statistics)."
        ))
