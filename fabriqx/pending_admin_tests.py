from io import BytesIO

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, RequestFactory
from django.urls import reverse
from django.http import QueryDict
from openpyxl import load_workbook

from . import models as m
from .admin import FabriqxUserAdmin, NewsletterAdmin
from .product_import import import_products


class PendingAdminTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser('tester', 'tester@example.com', 'pass')
        self.client.force_login(self.user)

    def test_staff_names_on_both_forms(self):
        for form_class in (FabriqxUserAdmin.AdminAccountCreationForm, FabriqxUserAdmin.UserWithRoleForm):
            kwargs = {'instance': self.user} if form_class is FabriqxUserAdmin.UserWithRoleForm else {}
            for name in ('Jane123', 'Jane@Doe', '123'):
                data = QueryDict('', mutable=True)
                data.update({'first_name': name, 'last_name': name})
                form = form_class(data=data, **kwargs)
                self.assertFalse(form.is_valid())
                self.assertIn('first_name', form.errors)
                self.assertIn('last_name', form.errors)
            data = QueryDict('', mutable=True)
            data.update({'first_name': 'Jane Mary', 'last_name': 'Doe'})
            form = form_class(data=data, **kwargs)
            form.is_valid()
            self.assertNotIn('first_name', form.errors)
            self.assertNotIn('last_name', form.errors)

    def test_inventory_excel_with_related_objects_and_dates(self):
        category = m.Category.objects.create(name='Clothes')
        product = m.Product.objects.create(name='Dress', category=category, regular_price=10)
        variant = m.ProductVariant.objects.create(product=product, sku='DRESS')
        movement = m.InventoryMovement.objects.create(variant=variant, movement_type='purchase', quantity=1, stock_before=0, stock_after=1, created_by=self.user)
        response = admin.site._registry[m.InventoryMovement].export_excel(RequestFactory().get('/'), m.InventoryMovement.objects.filter(pk=movement.pk))
        workbook = load_workbook(BytesIO(response.content))
        row = dict(zip(next(workbook.active.values), list(workbook.active.values)[1]))
        self.assertEqual(row['variant'], str(variant))
        self.assertIsNone(row['created_at'].tzinfo)
        workbook.close()

    def test_invalid_workbooks_have_actionable_errors(self):
        with self.assertRaisesMessage(ValueError, 'Invalid Excel file'):
            import_products(SimpleUploadedFile('products.xlsx', b'not an Excel workbook'))

    def test_newsletter_query_is_literal_and_email_only(self):
        m.NewsletterSubscription.objects.create(email='alice@example.com', source='bob')
        model_admin = NewsletterAdmin(m.NewsletterSubscription, admin.site)
        for query, count in [(' ALICE@EXAMPLE.COM ', 1), ('example.com', 1), ('bob', 0), ('"alice"', 0), ('alice example', 0)]:
            results, _ = model_admin.get_search_results(RequestFactory().get('/'), m.NewsletterSubscription.objects.all(), query)
            self.assertEqual(results.count(), count)

    def test_coupon_invalid_numeric_values_do_not_crash(self):
        for value in ('NaN', 'Infinity', '-1', '99999999999999999999', 'abc', ''):
            response = self.client.post(reverse('admin:products_coupon_add'), {'code': 'EDGE', 'discount_type': 'percentage', 'discount_value': value, '_save': 'Save'})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context['adminform'].form.errors)
        self.assertFalse(m.Coupon.objects.exists())
