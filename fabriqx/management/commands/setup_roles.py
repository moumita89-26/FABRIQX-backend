from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand
class Command(BaseCommand):
    help = "Create/update the Admin, Customer, and Influencer roles."

    def handle(self, *args, **options):
        for role in ("Admin", "Customer", "Influencer"):
            group, _ = Group.objects.get_or_create(name=role)
            group.permissions.clear()
            self.stdout.write(self.style.SUCCESS(f"{role}: permissions are assigned per user"))
