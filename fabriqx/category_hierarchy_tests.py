from django.core.exceptions import ValidationError
from django.test import TestCase

from .models import Category


class CategoryHierarchyTests(TestCase):
    def test_only_one_subcategory_level_is_allowed(self):
        root = Category.objects.create(name="Women")
        child = Category.objects.create(name="Suits", parent=root)
        grandchild = Category(name="Party Wear", parent=child)

        with self.assertRaisesMessage(ValidationError, "A subcategory cannot have its own subcategory."):
            grandchild.full_clean()

    def test_a_category_with_children_cannot_become_a_subcategory(self):
        root = Category.objects.create(name="Women")
        other_root = Category.objects.create(name="Men")
        Category.objects.create(name="Suits", parent=root)
        root.parent = other_root

        with self.assertRaisesMessage(ValidationError, "A category with subcategories cannot be moved under another category."):
            root.full_clean()
