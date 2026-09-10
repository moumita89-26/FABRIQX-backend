from django.contrib import admin
from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase
from unfold.templatetags.unfold_list import result_headers

from .models import Category, Product, ProductVariant
from .templatetags.table_sort import column_sort_url


class AdminColumnSortingTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(username="sort-admin", password=None)
        category = Category.objects.create(name="Sorting")
        self.first = Product.objects.create(name="First", category=category, regular_price=100)
        self.second = Product.objects.create(name="Second", category=category, regular_price=200)
        ProductVariant.objects.create(product=self.first, sku="first-a", size="S", stock_quantity=2)
        ProductVariant.objects.create(product=self.first, sku="first-b", size="M", stock_quantity=20)
        ProductVariant.objects.create(product=self.second, sku="second", stock_quantity=10)

    def changelist(self, model, params=None):
        request = RequestFactory().get("/admin/", params or {})
        request.user = self.user
        return admin.site._registry[model].get_changelist_instance(request)

    def test_stock_sorts_by_total_without_duplicate_products(self):
        cl = self.changelist(Product)
        index = cl.list_display.index("stock")
        ascending = self.changelist(Product, {"o": str(index)})
        descending = self.changelist(Product, {"o": f"-{index}"})
        self.assertEqual(list(ascending.queryset), [self.second, self.first])
        self.assertEqual(list(descending.queryset), [self.first, self.second])

    def test_admin_lists_default_to_newest_record_first(self):
        self.assertEqual(list(self.changelist(Product).queryset), [self.second, self.first])

        first_category = Category.objects.create(name="First category")
        second_category = Category.objects.create(name="Second category")
        self.assertEqual(
            list(self.changelist(Category).queryset)[:2],
            [second_category, first_category],
        )

    def test_effective_price_and_stock_status_are_sortable(self):
        cl = self.changelist(ProductVariant)
        headers = list(result_headers(cl))
        for column in ("effective_price", "stock_status"):
            index = cl.list_display.index(column)
            self.assertTrue(headers[index]["sortable"])
            ascending = list(self.changelist(ProductVariant, {"o": str(index)}).queryset)
            values = [getattr(obj, column) for obj in ascending]
            self.assertEqual(values, sorted(values))

    def test_headers_offer_both_directions_and_preserve_search_filters(self):
        cl = self.changelist(Product, {"q": "First", "status__exact": "draft"})
        headers = list(result_headers(cl))
        html = render_to_string("unfold/helpers/change_list_headers.html", {"cl": cl, "result_headers": headers})
        for header in headers:
            if header["sortable"]:
                self.assertIn(f'Sort {header["text"]} ascending', html)
                self.assertIn(f'Sort {header["text"]} descending', html)
        url = column_sort_url(cl, cl.list_display.index("name"), "desc")
        self.assertIn("q=First", url)
        self.assertIn("status__exact=draft", url)
        self.assertIn("o=-", url)
