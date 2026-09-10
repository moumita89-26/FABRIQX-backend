from decimal import Decimal
from pathlib import Path
from zipfile import ZipFile

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from fabriqx import models as m


PRODUCTS = (
    ("Royal Teal Embroidered Saree", "Apparel", "6400.00", None, "img-4.webp", True, False),
    ("Golden Embroidered Jutti", "Footwear", "440.00", None, "img-5.webp", True, False),
    ("Ivory & Black Embroidered Kurta Set", "Apparel", "1200.00", None, "img-6.webp", True, False),
    ("Midnight Blue Embroidered Suit Set", "Apparel", "1750.00", "1350.00", "img-7.webp", True, False),
    ("Champagne Embroidered Saree", "Apparel", "35000.00", None, "img-8.webp", False, True),
    ("Crimson Anarkali Suit Set", "Apparel", "13500.00", "12400.00", "img-9.webp", False, True),
    ("Olive Green Printed Kurta Set", "Apparel", "2500.00", None, "img-10.webp", False, True),
    ("Lavender Embroidered Saree", "Apparel", "28500.00", "25500.00", "img-11.webp", False, True),
)

TESTIMONIALS = (
    (
        "Priya Sharma",
        "Mumbai · Banarasi Silk Saree",
        "Fabriqx delivered exactly what they promise — heritage craftsmanship at its finest. "
        "My Banarasi saree for my daughter's wedding was absolutely breathtaking. The quality is unmatched.",
        "user-1.jpg",
    ),
    (
        "Ananya Krishnan",
        "Bengaluru · Resham Lehenga",
        "I've ordered from Libas and Biba before, but Fabriqx's packaging experience alone sets them apart. "
        "The fabric swatch seal inside my parcel made me feel truly special. Will never shop anywhere else.",
        "user-2.jpg",
    ),
    (
        "Meera Agarwal",
        "Delhi · Mirror Work Chaniya Choli",
        "Fabriqx delivered exactly what they promise — heritage craftsmanship at its finest. "
        "The quality is unmatched.",
        "user-1.jpg",
    ),
)


class Command(BaseCommand):
    help = "Seed catalog and homepage CMS records from the supplied Fabriqx frontend ZIP."

    def add_arguments(self, parser):
        parser.add_argument("--archive", required=True, help="Path to the Fabriqx frontend ZIP archive.")

    @staticmethod
    def _asset(archive, asset_name):
        member = f"images/{asset_name}"
        try:
            return ContentFile(archive.read(member), name=asset_name)
        except KeyError as exc:
            raise CommandError(f"Archive is missing required asset: {member}") from exc

    def _set_image(self, obj, field_name, archive, asset_name):
        field = getattr(obj, field_name)
        if field and field.name.endswith(f"/{asset_name}"):
            return
        field.save(asset_name, self._asset(archive, asset_name), save=False)

    def handle(self, *args, **options):
        archive_path = Path(options["archive"])
        if not archive_path.is_file():
            raise CommandError(f"ZIP archive not found: {archive_path}")

        with ZipFile(archive_path) as archive, transaction.atomic():
            category_assets = (("Apparel", "img-1.webp"), ("Footwear", "img-2.webp"), ("Accessories", "img-3.webp"))
            categories = {}
            for position, (name, asset) in enumerate(category_assets):
                category, _ = m.Category.objects.get_or_create(name=name, parent=None)
                category.display_order = position
                category.is_active = True
                self._set_image(category, "image", archive, asset)
                category.save()
                categories[name] = category

            for position, (name, category_name, regular_price, sale_price, asset, is_new, is_trending) in enumerate(PRODUCTS):
                product, _ = m.Product.objects.get_or_create(
                    name=name,
                    defaults={
                        "category": categories[category_name],
                        "regular_price": Decimal(regular_price),
                        "status": m.Product.Status.ACTIVE,
                    },
                )
                product.category = categories[category_name]
                product.brand = "FABRIQX"
                product.short_description = name
                product.description = f"{name} from the FABRIQX festive collection."
                product.regular_price = Decimal(regular_price)
                product.sale_price = Decimal(sale_price) if sale_price else None
                product.status = m.Product.Status.ACTIVE
                product.is_new_arrival = is_new
                product.is_trending = is_trending
                product.save()

                sku = f"ZIP-{product.slug.upper()[:68]}"
                variant, _ = m.ProductVariant.objects.update_or_create(
                    sku=sku,
                    defaults={"product": product, "stock_quantity": 25, "is_active": True},
                )
                image, created = m.ProductImage.objects.get_or_create(
                    product=product,
                    image__endswith=f"/{asset}",
                    defaults={"alt_text": name, "is_primary": True, "display_order": position},
                )
                if created:
                    image.image.save(asset, self._asset(archive, asset), save=False)
                    image.save()
                elif not image.image:
                    image.image.save(asset, self._asset(archive, asset), save=True)

            brand_section, _ = m.BrandLogoSection.objects.get_or_create(internal_name="Homepage brand logos")
            brand_section.is_active = True
            brand_section.save()
            for position in range(1, 7):
                asset = f"cp-{position}.png"
                logo, _ = m.BrandLogo.objects.get_or_create(
                    section=brand_section,
                    brand_name=f"Homepage partner {position}",
                    defaults={"display_order": position, "is_active": True},
                )
                logo.display_order = position
                logo.is_active = True
                self._set_image(logo, "logo", archive, asset)
                logo.save()

            offer, _ = m.OfferBanner.objects.get_or_create(
                internal_name="Wear It Every Day",
                defaults={"shop_now_url": "/products/", "display_order": 0},
            )
            offer.alt_text = "Wear It Every Day promotion"
            offer.shop_now_url = "/products/"
            offer.is_active = True
            self._set_image(offer, "desktop_image", archive, "middle-banner.webp")
            offer.save()

            offer_grid, _ = m.OfferGridSection.objects.get_or_create(internal_name="Homepage offer grid")
            offer_grid.is_active = True
            offer_grid.save()
            for position in range(1, 4):
                asset = f"offer-{position}.webp"
                item, _ = m.OfferGridItem.objects.get_or_create(
                    section=offer_grid,
                    internal_name=f"Homepage offer {position}",
                    defaults={"shop_now_url": "/products/", "display_order": position},
                )
                item.shop_now_url = "/products/"
                item.display_order = position
                item.is_active = True
                self._set_image(item, "desktop_image", archive, asset)
                item.save()

            gift, _ = m.GiftSection.objects.get_or_create(
                internal_name="Shop More, Get More Joy!",
                defaults={"main_image": self._asset(archive, "about-img.webp")},
            )
            gift.badge_eyebrow = "With every order"
            gift.badge_title = "Free gift"
            gift.accent_heading = "Shop More,"
            gift.heading = "Get More Joy!"
            gift.description = "Every Purchase Comes With A Free Gift, Just For You!"
            gift.thank_you_title = "Thank you for choosing Fabriqx."
            gift.thank_you_text = "Your love inspires us to keep creating styles that make every moment special."
            gift.cta_label = "Shop now & get your free gift"
            gift.cta_url = "/products/"
            gift.is_active = True
            self._set_image(gift, "main_image", archive, "about-img.webp")
            self._set_image(gift, "gift_image", archive, "free-gift.png")
            self._set_image(gift, "badge_icon", archive, "cat-12.png")
            gift.save()

            for name, sub_text, content, asset in TESTIMONIALS:
                testimonial, _ = m.Testimonial.objects.get_or_create(customer_name=name)
                testimonial.sub_text = sub_text
                testimonial.content = content
                testimonial.rating = 5
                testimonial.is_active = True
                self._set_image(testimonial, "image", archive, asset)
                testimonial.save()

            social_section, _ = m.FooterSocialSection.objects.get_or_create(internal_name="Footer social links")
            social_section.heading = "Social"
            social_section.is_active = True
            social_section.save()
            for position, (platform, url) in enumerate((
                ("facebook", "https://www.facebook.com/"),
                ("x", "https://x.com/"),
                ("linkedin", "https://www.linkedin.com/"),
                ("instagram", "https://www.instagram.com/"),
            )):
                link, _ = m.FooterSocialLink.objects.get_or_create(
                    section=social_section,
                    platform_name=platform,
                    defaults={"url": url, "display_order": position, "is_active": True},
                )
                link.url = url
                link.display_order = position
                link.is_active = True
                link.save()

        self.stdout.write(self.style.SUCCESS(
            "Seeded ZIP content: 3 categories, 8 products, 6 brand logos, 1 offer banner, "
            "3 offer tiles, 1 gift section, 3 testimonials and 4 social links."
        ))
