from django.core.management.base import BaseCommand

from fabriqx.models import Page


PAGES = (
    ("About us", "about-us", "<h1>About FABRIQX</h1><p>FABRIQX brings thoughtfully curated fashion and celebration essentials together in one place.</p>"),
    ("Terms of service", "terms-of-service", "<h1>Terms of Service</h1><p>These terms govern the use of the FABRIQX website and services.</p>"),
    ("Privacy policy", "privacy-policy", "<h1>Privacy Policy</h1><p>Learn how FABRIQX collects, uses, and protects personal information.</p>"),
    ("Contact us", "contact-us", "<h1>Contact Us</h1><p>Contact the FABRIQX team for product, order, and account support.</p>"),
)


class Command(BaseCommand):
    help = "Create the standard CMS pages used in the site footer."

    def handle(self, *args, **options):
        created = 0
        for title, slug, content in PAGES:
            _, was_created = Page.objects.get_or_create(
                slug=slug,
                defaults={"title": title, "content": content, "is_active": True},
            )
            created += was_created
        self.stdout.write(self.style.SUCCESS(f"Standard CMS pages ready ({created} created)."))
