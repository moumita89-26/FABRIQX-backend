import csv
import json
from datetime import datetime
from uuid import uuid4
from io import BytesIO
from urllib.parse import urlencode

from django.contrib import admin, messages
from django import forms
from django.forms.models import BaseInlineFormSet
from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.tokens import default_token_generator
from django.contrib.auth.password_validation import validate_password, password_validators_help_text_html
from django.contrib.auth.models import Group, Permission
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.db import transaction
from django.db import models as django_models
from django.db.models import Q, Case, When, Value, F, Count, Sum, OuterRef, Subquery
from django.db.models.functions import Coalesce, Concat, TruncDay
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.mail import send_mail
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.templatetags.static import static
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html, format_html_join
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django.utils.text import slugify
from openpyxl import Workbook
from unfold.admin import ModelAdmin, StackedInline, TabularInline
from unfold.contrib.forms.widgets import WysiwygWidget
from unfold.decorators import action
from unfold.sites import UnfoldAdminSite
from unfold.widgets import INPUT_CLASSES

from . import models as m
from .dashboard import DATE_RANGES, DEFAULT_RANGE, _period
from .product_import import SAMPLE_COLUMNS, SAMPLE_ROW, import_products as run_product_import


# Only models represented by a visible, delegable admin-sidebar item belong in
# the staff access matrix. Admin-account management stays superuser-only.
STAFF_MENU_MODELS = {
    "content_management": {"banner", "brandlogo", "contactsubmission", "footersocialsection", "giftsection", "homepagesection", "newslettersettings", "newslettersubscription", "offerbanner", "offergriditem", "page", "testimonial"},
    "customers": {"customerprofile"},
    "influencers": {"influencerprofile", "influencerreport"},
    "products": {"category", "coupon", "inventorymovement", "inventoryreport", "productimage", "productvariant", "product", "productfaq", "review"},
}
STAFF_MENU_LABELS = {
    "content_management": "Content Management System",
    "customers": "Customers",
    "influencers": "Influencers & Affiliates",
    "products": "Products & Inventory",
}
STAFF_MENU_ORDER = {app_label: position for position, app_label in enumerate(STAFF_MENU_MODELS)}


class FabriqxWysiwygWidget(WysiwygWidget):
    """Trix widget with separate IDs for its input and editable surface."""

    template_name = "fabriqx/widgets/wysiwyg.html"

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        widget = context["widget"]
        widget["input_id"] = f"wysiwyg-{name}"
        widget["editor_id"] = widget["attrs"].get("id", f"id_{name}")
        return context


def staff_permission_queryset():
    allowed = Q()
    for app_label, model_names in STAFF_MENU_MODELS.items():
        allowed |= Q(content_type__app_label=app_label, content_type__model__in=model_names)
    permissions = Permission.objects.filter(allowed)
    unavailable = {
        ("content_management", "newslettersubscription"): ("add",),
        ("customers", "customerprofile"): ("add", "delete"),
        ("products", "inventorymovement"): ("add", "change", "delete"),
        ("products", "inventoryreport"): ("add", "change", "delete"),
        ("influencers", "influencerreport"): ("add", "change", "delete"),
    }
    for (app_label, model_name), actions in unavailable.items():
        permissions = permissions.exclude(content_type__app_label=app_label, content_type__model=model_name,
                                          codename__in=[f"{action}_{model_name}" for action in actions])
    return permissions


def normalize_unique_user_email(email, exclude_user_id=None):
    email = email.strip().lower()
    users = get_user_model().objects.filter(email__iexact=email)
    if exclude_user_id is not None:
        users = users.exclude(pk=exclude_user_id)
    if users.exists():
        raise forms.ValidationError("An account with this email address already exists.")
    return email


def image_list_preview(image, alt):
    """Return a compact thumbnail for records that have an image."""
    if not image:
        return "—"
    try:
        url = image.url
    except (AttributeError, ValueError):
        return "—"
    return format_html(
        '<img class="admin-list-image" src="{}" alt="{}" loading="lazy" '
        'style="display:block!important;width:60px!important;min-width:60px!important;'
        'max-width:60px!important;height:46px!important;min-height:46px!important;'
        'max-height:46px!important;object-fit:cover!important;">',
        url,
        alt,
    )


class ExportMixin:
    # Bulk exports are intentionally not exposed in any admin changelist.
    # Classes that append custom actions to this tuple retain only those
    # custom actions (for example, status updates or approvals).
    actions = ()
    export_fields = None

    def get_export_fields(self):
        return self.export_fields or [field.name for field in self.model._meta.fields]

    @staticmethod
    def _export_value(value):
        """Return audit dates in the configured local time for every export."""
        # openpyxl accepts scalar values, but not Django model instances.  A
        # ForeignKey is returned as its related object by getattr(), which
        # previously made exports such as Inventory Movement fail at save time.
        if isinstance(value, django_models.Model):
            return str(value)
        if isinstance(value, datetime):
            if timezone.is_aware(value):
                return timezone.localtime(value).replace(tzinfo=None)
        return value

    @admin.action(description="Export selected rows as CSV")
    def export_csv(self, request, queryset):
        fields = self.get_export_fields()
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="{self.model._meta.model_name}.csv"'
        writer = csv.writer(response)
        writer.writerow(fields)
        for obj in queryset.iterator():
            writer.writerow([self._export_value(getattr(obj, field, "")) for field in fields])
        return response

    @admin.action(description="Export selected rows as Excel")
    def export_excel(self, request, queryset):
        fields = self.get_export_fields()
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = self.model._meta.verbose_name_plural.title()[:31]
        sheet.append(fields)
        for obj in queryset.iterator():
            row = [self._export_value(getattr(obj, field, "")) for field in fields]
            sheet.append(["" if value is None else value for value in row])
        for row in sheet.iter_rows(min_row=2):
            for cell in row:
                if isinstance(cell.value, datetime):
                    cell.number_format = "yyyy-mm-dd hh:mm:ss"
        stream = BytesIO()
        workbook.save(stream)
        response = HttpResponse(stream.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        response["Content-Disposition"] = f'attachment; filename="{self.model._meta.model_name}.xlsx"'
        return response

class AdminListToolsMixin:
    list_filter_submit = True

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        for field in form.base_fields.values():
            widget = field.widget
            if isinstance(widget, forms.widgets.Input) and widget.input_type in {
                "text", "email", "password", "number", "url", "tel", "search",
            }:
                classes = widget.attrs.get("class", "").split()
                widget.attrs["class"] = " ".join(dict.fromkeys([*classes, *INPUT_CLASSES]))
        return form

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        columns = self.get_list_display(request)
        annotations = {}
        if "stock" in columns:
            annotations["_sort_stock"] = Coalesce(Sum("variants__stock_quantity"), 0)
        if "usage_count" in columns:
            annotations["_sort_usage_count"] = Count("usages", distinct=True)
        if "referred_orders" in columns:
            annotations["_sort_referred_orders"] = Count("orders", distinct=True)
        if "payment_state" in columns:
            latest = m.Payment.objects.filter(order_id=OuterRef("pk")).order_by("-created_at", "-pk")
            annotations["_sort_payment_state"] = Coalesce(Subquery(latest.values("status")[:1]), Value("Not recorded"))
        return queryset.annotate(**annotations) if annotations else queryset

    @admin.display(description="Effective price", ordering=Case(
        When(price_override__gt=0, then=F("price_override")),
        When(product__sale_price__gt=0, then=F("product__sale_price")),
        default=F("product__regular_price"),
    ))
    def effective_price(self, obj):
        return obj.effective_price

    @admin.display(description="Stock status", ordering=Case(
        When(stock_quantity=0, then=Value("Out of stock")),
        When(stock_quantity__lte=F("low_stock_threshold"), then=Value("Low stock")),
        default=Value("In stock"),
    ))
    def stock_status(self, obj):
        return obj.stock_status

    def get_list_filter(self, request):
        return ()

    def get_search_fields(self, request):
        configured = super().get_search_fields(request)
        return configured or tuple(
            field.name
            for field in self.model._meta.fields
            if isinstance(field, (django_models.CharField, django_models.TextField))
            and field.name not in {"password", "token", "secret"}
        )


class BaseAdmin(AdminListToolsMixin, ExportMixin, ModelAdmin):
    # Show 10 records per admin list page. Django/Unfold renders numbered
    # pagination at the bottom when more than 10 records are available.
    list_per_page = 10
    # Admin lists should show the most recently created record first.  This
    # deliberately overrides a model's public/display ordering while retaining
    # sortable column headers for staff who need a different view.
    ordering = ("-pk",)
    save_on_top = True
    hide_from_index = False
    # Models that are intentionally read-only can opt into a View link while
    # retaining just the row actions that make sense for their workflow.
    row_actions = ("change", "delete")

    formfield_overrides = {
        django_models.TextField: {"widget": FabriqxWysiwygWidget},
    }

    # Hide audit timestamp fields from all normal admin forms.
    hidden_audit_fields = {
        "created_at",
        "updated_at",
    }

    def render_change_form(
        self,
        request,
        context,
        add=False,
        change=False,
        form_url="",
        obj=None,
    ):
        context["show_save_and_continue"] = False
        context["show_save_and_add_another"] = False

        return super().render_change_form(
            request,
            context,
            add=add,
            change=change,
            form_url=form_url,
            obj=obj,
        )

    @property
    def media(self):
        return super().media + forms.Media(
            css={
                "all": (
                    "fabriqx/admin/css/custom_admin.css",
                )
            },

        )

    def get_model_perms(self, request):
        if self.hide_from_index:
            return {}
        return super().get_model_perms(request)

    def get_fields(self, request, obj=None):
        fields = super().get_fields(request, obj)
        return tuple(
            field
            for field in fields
            if field not in self.hidden_audit_fields
        )

    def get_fieldsets(self, request, obj=None):
        fieldsets = super().get_fieldsets(request, obj)
        cleaned_fieldsets = []

        for title, options in fieldsets:
            # Remove any fieldset explicitly named "Audit".
            if title and str(title).strip().lower() == "audit":
                continue

            cleaned_fields = []

            for field in options.get("fields", ()):
                if isinstance(field, str):
                    if field not in self.hidden_audit_fields:
                        cleaned_fields.append(field)

                elif isinstance(field, (tuple, list)):
                    group = tuple(
                        item
                        for item in field
                        if item not in self.hidden_audit_fields
                    )
                    if group:
                        cleaned_fields.append(group)

                else:
                    cleaned_fields.append(field)

            if not cleaned_fields:
                continue

            new_options = options.copy()
            new_options["fields"] = tuple(cleaned_fields)
            cleaned_fieldsets.append((title, new_options))

        return tuple(cleaned_fieldsets)

    def get_list_display(self, request):
        columns = list(super().get_list_display(request))
        can_view = self.has_view_permission(request)
        can_edit = self.has_change_permission(request)
        can_delete = self.has_delete_permission(request)
        show_view = "view" in self.row_actions and can_view
        show_edit = "change" in self.row_actions and can_edit
        show_delete = "delete" in self.row_actions and can_delete

        if show_view or show_edit or show_delete:
            @admin.display(description="Action")
            def row_actions(obj):
                links = []

                if show_view or show_edit:
                    change_url = reverse(
                        f"admin:{obj._meta.app_label}_{obj._meta.model_name}_change",
                        args=(obj.pk,),
                    )
                if show_view:
                    links.append(
                        format_html(
                            '<a class="inline-flex items-center rounded-default bg-primary-600 px-3 py-1.5 font-medium text-white hover:bg-primary-700" href="{}">View</a>',
                            change_url,
                        )
                    )

                if show_edit:
                    links.append(
                        format_html(
                            '<a class="inline-flex items-center rounded-default bg-primary-600 px-3 py-1.5 font-medium text-white hover:bg-primary-700" href="{}">Edit</a>',
                            change_url,
                        )
                    )

                if can_delete:
                    delete_url = reverse(
                        f"admin:{obj._meta.app_label}_{obj._meta.model_name}_delete",
                        args=(obj.pk,),
                    )
                    links.append(
                        format_html(
                            '<a class="inline-flex items-center rounded-default bg-red-600 px-3 py-1.5 font-medium text-white hover:bg-red-700" href="{}">Delete</a>',
                            delete_url,
                        )
                    )

                return format_html(
                    '<div class="flex items-center gap-2">{}</div>',
                    format_html_join("", "{}", ((link,) for link in links)),
                )

            columns.append(row_actions)

        return columns


class ProductImageInline(TabularInline):
    model = m.ProductImage
    extra = 1


class ProductVariantInline(TabularInline):
    model = m.ProductVariant
    extra = 0
    min_num = 1

    def get_formset(self, request, obj=None, **kwargs):
        kwargs["validate_min"] = True
        return super().get_formset(request, obj, **kwargs)


@admin.register(m.Category)
class CategoryAdmin(BaseAdmin):
    class CategoryForm(forms.ModelForm):
        class Meta:
            model = m.Category
            exclude = ("audience", "seo_title", "seo_description", "display_order")

        def clean(self):
            cleaned = super().clean()
            name = cleaned.get("name")
            if name:
                # Never trust a stale value left behind by prepopulation JS.
                cleaned["slug"] = slugify(name)
                self.instance.slug = cleaned["slug"]
            return cleaned

    form = CategoryForm
    list_display = ("image_thumbnail", "name", "parent", "is_active", "updated_at")
    list_filter = ("is_active", "parent")
    search_fields = ("name", "slug", "description")
    prepopulated_fields = {"slug": ("name",)}
    list_editable = ("is_active",)
    exclude = ("audience", "seo_title", "seo_description", "display_order")

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        # The storefront supports root categories and one subcategory level.
        if "parent" in form.base_fields:
            parents = m.Category.objects.filter(parent__isnull=True)
            if obj:
                parents = parents.exclude(pk=obj.pk)
            form.base_fields["parent"].queryset = parents
        return form

    class Media:
        css = {"all": ("fabriqx/admin/category_sort.css",)}
        js = ("fabriqx/admin/category_sort.js",)

    @admin.display(description="Sort")
    def drag_handle(self, obj):
        reorder_url = reverse("admin:products_category_reorder")
        return format_html(
            '<span class="category-drag-handle material-symbols-outlined" draggable="true" '
            'data-category-id="{}" data-reorder-url="{}" title="Drag to reorder" '
            'aria-label="Drag {} to reorder">drag_indicator</span>',
            obj.pk,
            reorder_url,
            obj.name,
        )

    @admin.display(description="Image")
    def image_thumbnail(self, obj):
        return image_list_preview(obj.image, obj.name)

    def get_urls(self):
        custom_urls = [
            path("reorder/", self.admin_site.admin_view(self.reorder_view), name="products_category_reorder"),
            path("<int:pk>/toggle-active/", self.admin_site.admin_view(self.toggle_active_view), name="products_category_toggle_active"),
        ]
        return custom_urls + super().get_urls()

    def save_model(self, request, obj, form, change):
        if not change:
            highest_order = self.model.objects.aggregate(value=django_models.Max("display_order"))["value"]
            obj.display_order = 0 if highest_order is None else highest_order + 1
        super().save_model(request, obj, form, change)

    def reorder_view(self, request):
        if request.method != "POST" or not self.has_change_permission(request):
            return JsonResponse({"error": "You do not have permission to reorder categories."}, status=403)

        try:
            ordered_ids = [int(category_id) for category_id in json.loads(request.body).get("ordered_ids", [])]
        except (TypeError, ValueError, json.JSONDecodeError):
            return JsonResponse({"error": "Invalid category order."}, status=400)

        categories = {category.pk: category for category in self.model.objects.filter(pk__in=ordered_ids)}
        all_ids = set(self.model.objects.values_list("pk", flat=True))
        if len(ordered_ids) != len(set(ordered_ids)) or set(ordered_ids) != all_ids:
            return JsonResponse({"error": "Reload the complete category list before sorting."}, status=400)

        with transaction.atomic():
            for position, category_id in enumerate(ordered_ids):
                categories[category_id].display_order = position
            self.model.objects.bulk_update(categories.values(), ("display_order",))

        return JsonResponse({"success": True})

    def toggle_active_view(self, request, pk):
        if request.method != "POST" or not self.has_change_permission(request):
            return JsonResponse({"error": "You do not have permission to update categories."}, status=403)
        try:
            is_active = json.loads(request.body)["is_active"]
        except (KeyError, TypeError, json.JSONDecodeError):
            return JsonResponse({"error": "Invalid active status."}, status=400)
        if not isinstance(is_active, bool):
            return JsonResponse({"error": "Active status must be true or false."}, status=400)

        category = get_object_or_404(self.model, pk=pk)
        category.is_active = is_active
        category.save(update_fields=("is_active", "updated_at"))
        return JsonResponse({"success": True, "id": category.pk, "is_active": category.is_active})


@admin.register(m.Product)
class ProductAdmin(BaseAdmin):
    actions_list = ("import_products", "download_product_sample")
    list_display = ("image_thumbnail", "name", "category", "regular_price", "sale_price", "stock", "status", "is_featured", "is_trending")
    list_filter = ("status", "category", "is_featured", "is_trending", "is_new_arrival", "brand")
    search_fields = ("name", "slug", "brand", "variants__sku")
    autocomplete_fields = ("category", "related_products")
    prepopulated_fields = {"slug": ("name",)}
    inlines = (ProductImageInline, ProductVariantInline)
    readonly_fields = ("published_at", "created_at", "updated_at")
    fieldsets = (
        ("Product", {"fields": ("category", "name", "slug", "brand", "short_description", "description")}),
        ("Pricing", {"fields": ("regular_price", "sale_price")}),
        ("Merchandising", {"fields": ("status", "is_featured", "is_trending", "is_new_arrival", "related_products", "published_at")}),
        ("SEO", {"fields": ("seo_title", "seo_description"), "classes": ("collapse",)}),
        ("Audit", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )

    @action(description="Import Products", permissions=("add",), icon="upload_file", url_path="import-products")
    def import_products(self, request):
        if request.method == "POST":
            upload = request.FILES.get("import_file")
            if not upload:
                self.message_user(request, "Choose a CSV or XLSX file.", messages.ERROR)
            else:
                try:
                    counts, errors = run_product_import(upload)
                except Exception as exc:
                    self.message_user(request, f"Import could not start: {exc}", messages.ERROR)
                else:
                    level = messages.WARNING if counts["failed"] else messages.SUCCESS
                    summary = (
                        f"Processed {counts['rows']} rows: {counts['succeeded']} succeeded; "
                        f"{counts['products']} products, {counts['variants']} variants and "
                        f"{counts['images']} images created; {counts['failed']} rows failed."
                    )
                    self.message_user(request, summary, level)
                    if errors:
                        visible_errors = errors[:5]
                        hidden_count = len(errors) - len(visible_errors)
                        error_summary = "Import issues: " + " | ".join(visible_errors)
                        if hidden_count:
                            error_summary += f" | Plus {hidden_count} more distinct error type(s)."
                        self.message_user(request, error_summary, messages.ERROR)
                    return redirect("admin:products_product_changelist")
        context = {**self.admin_site.each_context(request), "title": "Import products", "opts": self.model._meta}
        return render(request, "fabriqx/product_import.html", context)

    @action(description="Download Sample", permissions=("add",), icon="download", url_path="download-product-sample")
    def download_product_sample(self, request):
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Products"
        sheet.append(SAMPLE_COLUMNS)
        sheet.append([SAMPLE_ROW[column] for column in SAMPLE_COLUMNS])
        instructions = workbook.create_sheet("Instructions")
        instructions.append(("Column", "Instructions"))
        instructions.append(("One row per variant", "Repeat the product name for additional size/color/SKU variants."))
        instructions.append(("sale", "Sets whether the product appears in the sale/trending collection."))
        instructions.append(("boolean fields", "Use true/false, yes/no or 1/0."))
        stream = BytesIO()
        workbook.save(stream)
        response = HttpResponse(stream.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        response["Content-Disposition"] = 'attachment; filename="fabriqx-product-import-sample.xlsx"'
        return response

    @admin.display(description="Stock", ordering="_sort_stock")
    def stock(self, obj):
        return obj.total_stock

    @admin.display(description="Image")
    def image_thumbnail(self, obj):
        product_image = next(iter(obj.images.all()), None)
        return image_list_preview(product_image.image if product_image else None, obj.name)

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("images")


@admin.register(m.ProductVariant)
class VariantAdmin(BaseAdmin):
    list_display = ("sku", "product", "size", "color", "effective_price", "stock_quantity", "stock_status", "is_active")
    list_filter = ("is_active", "size", "color", "product__category")
    search_fields = ("sku", "product__name", "size", "color")
    autocomplete_fields = ("product",)
    list_editable = ("is_active",)


@admin.register(m.InventoryMovement)
class InventoryAdmin(BaseAdmin):
    list_display = ("variant", "movement_type", "quantity", "stock_before", "stock_after", "reference", "created_by", "created_at")
    list_filter = ("movement_type", "created_at")
    search_fields = ("variant__sku", "variant__product__name", "reference", "reason")
    autocomplete_fields = ("variant",)
    readonly_fields = tuple(field.name for field in m.InventoryMovement._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False if obj else super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return False

class AddressInline(TabularInline):
    model = m.Address
    extra = 0


class WishlistInline(TabularInline):
    model = m.WishlistItem
    extra = 0
    autocomplete_fields = ("product", "variant")


@admin.register(m.CustomerProfile)
class CustomerAdmin(BaseAdmin):
    class CustomerChangeForm(forms.ModelForm):
        username = forms.CharField(disabled=True)
        first_name = forms.CharField(max_length=150, required=True)
        last_name = forms.CharField(max_length=150, required=False)
        email = forms.EmailField(required=True)
        phone = forms.CharField(max_length=30, required=True)

        class Meta:
            model = m.CustomerProfile
            fields = ("username", "first_name", "last_name", "email", "phone", "date_of_birth")

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            if self.instance.pk:
                self.initial.update({
                    "username": self.instance.user.username,
                    "first_name": self.instance.user.first_name,
                    "last_name": self.instance.user.last_name,
                    "email": self.instance.user.email,
                })

        def clean_email(self):
            current_user_id = self.instance.user_id if self.instance.pk else None
            return normalize_unique_user_email(self.cleaned_data["email"], current_user_id)

    form = CustomerChangeForm
    list_display = ("user", "phone", "created_at")
    list_filter = ("created_at",)
    search_fields = ("user__username", "user__first_name", "user__last_name", "user__email", "phone")
    inlines = (AddressInline,)
    fields = ("username", "first_name", "last_name", "email", "phone", "date_of_birth", "created_at", "updated_at")
    readonly_fields = ("created_at", "updated_at")
    actions = ExportMixin.actions + ("send_password_reset",)
    row_actions = ("view", "delete")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return super().has_delete_permission(request, obj)

    def delete_model(self, request, obj):
        # A customer profile is owned by its auth user. Deleting only the
        # profile leaves that user's unique email behind and blocks sign-up.
        obj.user.delete()

    def delete_queryset(self, request, queryset):
        for customer in queryset.select_related("user"):
            customer.user.delete()

    @transaction.atomic
    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        user = obj.user
        user.first_name = form.cleaned_data["first_name"]
        user.last_name = form.cleaned_data["last_name"]
        user.email = form.cleaned_data["email"]
        user.save(update_fields=("first_name", "last_name", "email"))

    @admin.action(description="Email secure password reset link", permissions=("view",))
    def send_password_reset(self, request, queryset):
        sent = 0
        skipped = 0
        for customer in queryset.select_related("user"):
            user = customer.user
            if not user.email or not user.is_active:
                skipped += 1
                continue
            uid = urlsafe_base64_encode(force_bytes(user.pk))
            token = default_token_generator.make_token(user)
            separator = "&" if "?" in settings.FRONTEND_RESET_PASSWORD_URL else "?"
            reset_url = f"{settings.FRONTEND_RESET_PASSWORD_URL}{separator}{urlencode({'uid': uid, 'token': token})}"
            send_mail(
                "Reset your FABRIQX password",
                (
                    f"Hello {user.get_full_name() or user.get_username()},\n\n"
                    "An administrator requested a secure password reset for your FABRIQX account.\n\n"
                    f"Set a new password here:\n{reset_url}\n\n"
                    "If you were not expecting this email, contact FABRIQX support."
                ),
                settings.DEFAULT_FROM_EMAIL,
                [user.email],
            )
            sent += 1
        level = messages.SUCCESS if sent else messages.WARNING
        self.message_user(request, f"Sent {sent} password reset email(s); skipped {skipped} account(s).", level)

@admin.register(m.UserRole)
class UserRoleAdmin(BaseAdmin):
    hide_from_index = True
    class PermissionMatrixWidget(forms.Widget):
        template_name = "fabriqx/widgets/permission_matrix.html"

        def __init__(self, queryset, attrs=None):
            super().__init__(attrs)
            self.queryset = queryset

        def value_from_datadict(self, data, files, name):
            return data.getlist(name)

        def get_context(self, name, value, attrs):
            context = super().get_context(name, value, attrs)
            selected = {str(getattr(item, "pk", item)) for item in (value or [])}
            # Preserve existing section grants when staff access is edited in the new logo matrix.
            legacy_actions = Permission.objects.filter(
                pk__in=selected, content_type__app_label="content_management",
                content_type__model="brandlogosection",
            ).values_list("codename", flat=True)
            logo_codes = [f"{code.split('_', 1)[0]}_brandlogo" for code in legacy_actions]
            selected.update(str(pk) for pk in self.queryset.filter(
                content_type__app_label="content_management", codename__in=logo_codes,
            ).values_list("pk", flat=True))
            groups = {}
            allowed_actions = {"add", "view", "change", "delete"}
            for permission in self.queryset.select_related("content_type").order_by("content_type__app_label", "content_type__model", "codename"):
                action = permission.codename.split("_", 1)[0]
                if action not in allowed_actions:
                    continue
                content_type = permission.content_type
                app_label = content_type.app_label
                app_name = STAFF_MENU_LABELS.get(app_label, app_label.replace("_", " ").title())
                model_class = content_type.model_class()
                model_name = model_class._meta.verbose_name_plural.title() if model_class else content_type.model.replace("_", " ").title()
                group = groups.setdefault(app_label, {"name": app_name, "models": {}})
                row = group["models"].setdefault(model_name, {"model": model_name, "permissions": {}})
                row["permissions"][action] = {
                    "id": permission.pk,
                    "checked": str(permission.pk) in selected,
                }
            matrix_groups = []
            for app_label, group in sorted(groups.items(), key=lambda item: STAFF_MENU_ORDER.get(item[0], 99)):
                rows = []
                for row in group["models"].values():
                    read_permission = row["permissions"].get("view")
                    write_permissions = [
                        row["permissions"][action]
                        for action in ("add", "change", "delete")
                        if action in row["permissions"]
                    ]
                    row["read_permissions"] = [read_permission] if read_permission else []
                    row["write_permissions"] = write_permissions
                    row["read_checked"] = bool(read_permission and read_permission["checked"])
                    row["write_checked"] = bool(write_permissions) and all(permission["checked"] for permission in write_permissions)
                    row["cells"] = [
                        {"label": label, "permission": row["permissions"].get(action)}
                        for action, label in (("view", "View"), ("add", "Add"), ("change", "Edit"), ("delete", "Delete"))
                    ]
                    rows.append(row)
                matrix_groups.append({"name": group["name"], "models": rows})
            context["widget"]["matrix_groups"] = matrix_groups
            return context

    class UserRoleForm(forms.ModelForm):
        permissions = forms.ModelMultipleChoiceField(
            queryset=Permission.objects.exclude(
                content_type__app_label__in=("admin", "contenttypes", "sessions", "token_blacklist")
            ).exclude(content_type__model__in=("group", "permission", "auditlog")),
            required=False,
        )

        class Meta:
            model = m.UserRole
            fields = ("user", "role", "permissions")

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            # Staff accounts created here always use a temporary password, so
            # the optional usable/unusable password switch is not applicable.
            self.fields.pop("usable_password", None)
            self.fields["permissions"].widget = UserRoleAdmin.PermissionMatrixWidget(self.fields["permissions"].queryset)

        def save(self, commit=True):
            user = super().save(commit=False)
            user.email = self.cleaned_data["email"]
            if commit:
                user.save()
            return user
            role = self.data.get("role") if self.is_bound else getattr(self.instance, "role", m.UserRole.Role.CUSTOMER)
            if role:
                group = Group.objects.filter(name=dict(m.UserRole.Role.choices).get(role, role.title())).first()
                if group:
                    self.initial["permissions"] = list(group.permissions.values_list("pk", flat=True))

        def save(self, commit=True):
            instance = super().save(commit=commit)
            if commit:
                group, _ = Group.objects.get_or_create(name=instance.get_role_display())
                group.permissions.set(self.cleaned_data["permissions"])
            return instance

    form = UserRoleForm
    list_display = ("user", "role", "staff_access", "updated_at")
    list_filter = ("role",)
    search_fields = ("user__username", "user__email", "user__first_name", "user__last_name")
    autocomplete_fields = ("user",)
    fieldsets = (
        ("Role details", {"fields": ("user", "role")}),
        ("Permissions", {"fields": ("permissions",)}),
    )

    @admin.display(boolean=True, description="Admin access", ordering=Case(
        When(Q(role="admin") | Q(user__is_superuser=True), then=Value(True)),
        default=Value(False),
    ))
    def staff_access(self, obj):
        return obj.role == m.UserRole.Role.ADMIN or obj.user.is_superuser


class UserRoleInline(StackedInline):
    model = m.UserRole
    can_delete = False
    extra = 0
    max_num = 1
    fields = ("role",)


class StaffNameValidationMixin:
    def clean_first_name(self):
        return self._clean_name("first_name")

    def clean_last_name(self):
        return self._clean_name("last_name")

    def _clean_name(self, field):
        value = self.cleaned_data.get(field, "").strip()
        if value and not all(character.isalpha() or character == " " for character in value):
            raise forms.ValidationError("Name may contain letters and spaces only.")
        return value


class FabriqxUserAdmin(AdminListToolsMixin, BaseUserAdmin, ModelAdmin):
    permission_queryset = staff_permission_queryset()
    list_per_page = 10
    ordering = ("-pk",)
    readonly_fields = ("role_display",)
    list_filter = ()

    class AdminAccountCreationForm(StaffNameValidationMixin, UserCreationForm):
        class Meta(UserCreationForm.Meta):
            fields = ("first_name", "last_name", "email")

        def save(self, commit=True):
            self.instance.username = "staff_" + uuid4().hex
            return super().save(commit=commit)

        email = forms.EmailField(
            required=True,
            widget=forms.EmailInput(attrs={"class": " ".join(INPUT_CLASSES)}),
        )
        permissions = forms.ModelMultipleChoiceField(
            queryset=staff_permission_queryset(),
            required=True,
            error_messages={"required": "Select at least one permission."},
        )

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            password_input_classes = " ".join(INPUT_CLASSES)
            for field_name in ("password1", "password2"):
                self.fields[field_name].widget.attrs["class"] = password_input_classes
            self.fields["permissions"].widget = UserRoleAdmin.PermissionMatrixWidget(self.fields["permissions"].queryset)

        def clean_email(self):
            return normalize_unique_user_email(self.cleaned_data["email"])

    class UserWithRoleForm(StaffNameValidationMixin, BaseUserAdmin.form):
        password = None
        password1 = forms.CharField(
            label="Password", required=False, strip=False,
            widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
            help_text="Leave blank to keep the current password.",
        )
        password2 = forms.CharField(
            label="Confirm password", required=False, strip=False,
            widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        )
        permissions = forms.ModelMultipleChoiceField(
            queryset=staff_permission_queryset(),
            required=False,
        )

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.fields["permissions"].widget = UserRoleAdmin.PermissionMatrixWidget(self.fields["permissions"].queryset)
            # Account status fields (including is_superuser) are intentionally
            # hidden from this form, so do not access them here.
            if "username" in self.fields:
                self.fields["username"].help_text = ""
            self.fields["password1"].help_text = format_html(
                "{}<p>{}</p>", password_validators_help_text_html(),
                "Leave blank to keep the current password.",
            )
            self.fields["password2"].help_text = "Enter the same password again. Leave blank if you are not changing it."
            self.initial["permissions"] = list(self.instance.user_permissions.values_list("pk", flat=True))

        def clean_email(self):
            email = self.cleaned_data["email"].strip().lower()

            # When editing an existing staff account, keeping the same email
            # must never fail because of legacy duplicate rows in the database.
            current_email = (self.instance.email or "").strip().lower() if self.instance and self.instance.pk else ""
            if self.instance and self.instance.pk and email == current_email:
                return email

            return normalize_unique_user_email(email, self.instance.pk)

        def clean(self):
            cleaned_data = super().clean()
            password = cleaned_data.get("password1")
            confirmation = cleaned_data.get("password2")
            if password != confirmation:
                self.add_error("password2", "Passwords do not match.")
            return cleaned_data

        def _post_clean(self):
            super()._post_clean()
            password = self.cleaned_data.get("password1")
            if password:
                try:
                    validate_password(password, self.instance)
                except ValidationError as error:
                    self.add_error("password1", error)

        def save(self, commit=True):
            user = super().save(commit=False)
            password = self.cleaned_data.get("password1")
            if password:
                user.set_password(password)
            if commit:
                user.save()
                self.save_m2m()
            return user

    form = UserWithRoleForm
    add_form = AdminAccountCreationForm
    list_display = (
        "username",
        "email",
        "first_name",
        "last_name",
        "role_display",
        "is_staff",
        "account_actions",
    )
    list_filter = BaseUserAdmin.list_filter + ("fabriqx_role__role",)
    inlines = ()
    # Keep the edit screen focused on account details and permissions.
    # Account status and Important dates are intentionally hidden from the UI.
    fieldsets = (
        (None, {"fields": ("username", "password1", "password2")}),
        ("Personal info", {"fields": ("first_name", "last_name", "email")}),
        ("Access permissions", {"fields": ("permissions",)}),
    )
    add_fieldsets = (
        (None, {"fields": ("first_name", "last_name", "email", "password1", "password2")}),
        ("Access permissions", {"fields": ("permissions",)}),
    )

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        if not request.user.is_superuser:
            return False
        return obj is None or not obj.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def get_actions(self, request):
        actions = super().get_actions(request)
        # Row-level deletion gives clear protection feedback for superusers and
        # avoids accidentally including one in a bulk delete operation.
        actions.pop("delete_selected", None)
        return actions

    @admin.display(description="Action")
    def account_actions(self, obj):
        change_url = reverse("admin:auth_user_change", args=(obj.pk,))
        edit_button = format_html(
            '<a class="inline-flex items-center rounded-default bg-primary-600 px-3 py-1.5 font-medium text-white hover:bg-primary-700" href="{}">Edit</a>',
            change_url,
        )

        # Super Admin stays protected from deletion, but no "Protected" text
        # is shown in the table.
        if obj.is_superuser:
            return format_html(
                '<div class="flex items-center gap-2">{}</div>',
                edit_button,
            )

        delete_url = reverse("admin:auth_user_delete", args=(obj.pk,))
        delete_button = format_html(
            '<a class="inline-flex items-center rounded-default bg-red-600 px-3 py-1.5 font-medium text-white hover:bg-red-700" href="{}">Delete</a>',
            delete_url,
        )

        return format_html(
            '<div class="flex items-center gap-2">{}{}</div>',
            edit_button,
            delete_button,
        )

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if request.resolver_match and request.resolver_match.url_name == "auth_user_changelist":
            return queryset.filter(Q(is_staff=True) | Q(is_superuser=True)).distinct()
        return queryset

    def changelist_view(self, request, extra_context=None):
        extra_context = {**(extra_context or {}), "title": "Select admin or staff account to change"}
        return super().changelist_view(request, extra_context=extra_context)

    def save_model(self, request, obj, form, change):
        # Users created from "Admin & Staff Accounts" are always admin users.
        # Customer and influencer accounts are created through their dedicated
        # flows, never through Django's generic user add form.
        if not change:
            obj.is_staff = True

        initial_password = form.cleaned_data.get("password1") if not change else None

        super().save_model(request, obj, form, change)
        m.UserRole.objects.update_or_create(user=obj, defaults={"role": m.UserRole.Role.ADMIN})
        obj.user_permissions.set(form.cleaned_data.get("permissions", Permission.objects.none()))
        if change and obj.pk == request.user.pk and form.cleaned_data.get("password1"):
            update_session_auth_hash(request, obj)

        if not change and obj.email:
            recipient = obj.email
            display_name = obj.get_full_name() or obj.get_username()
            username = obj.get_username()
            login_url = request.build_absolute_uri(reverse("admin:login"))
            password_change_url = request.build_absolute_uri(reverse("admin:password_change"))
            logo_url = request.build_absolute_uri(static("branding/fabriqx-logo.jpeg"))

            def send_access_email():
                context = {
                    "display_name": display_name,
                    "logo_url": logo_url,
                    "login_url": login_url,
                    "username": username,
                    "email": recipient,
                    "temporary_password": initial_password,
                    "password_change_url": password_change_url,
                }
                send_mail(
                    "Your FABRIQX Admin / Staff account",
                    (
                        f"Hello {display_name},\n\n"
                        "Your FABRIQX Admin / Staff account has been created.\n\n"
                        f"Login URL: {login_url}\n"
                        f"Username: {username}\n"
                        f"Email: {recipient}\n"
                        f"Temporary password: {initial_password}\n\n"
                        "You can sign in using either your username or email address.\n\n"
                        "You can change or reset your password later after signing in:\n"
                        f"{password_change_url}\n\n"
                        "For security, please sign in and change your temporary password.\n\n"
                        "FABRIQX Team"
                    ),
                    settings.DEFAULT_FROM_EMAIL,
                    [recipient],
                    html_message=render_to_string("fabriqx/emails/admin_staff_access.html", context),
                )

            transaction.on_commit(send_access_email)

    @admin.display(description="Role", ordering=Case(
        When(is_superuser=True, then=Value("Super Admin")),
        When(fabriqx_role__role="admin", then=Value("Admin")),
        When(fabriqx_role__role="influencer", then=Value("Influencer")),
        default=Value("Customer"),
    ))
    def role_display(self, obj):
        if obj.is_superuser:
            return "Super Admin"
        try:
            return obj.fabriqx_role.get_role_display()
        except m.UserRole.DoesNotExist:
            return "Admin" if obj.is_superuser else "Customer"


@admin.register(m.Coupon)
class CouponAdmin(BaseAdmin):
    class CouponForm(forms.ModelForm):
        affiliate = forms.ModelChoiceField(
            queryset=m.InfluencerProfile.objects.none(),
            required=False,
            label="Affiliate",
            help_text="Optional. Link this coupon to one influencer affiliate.",
        )

        class Meta:
            model = m.Coupon
            exclude = ("categories", "influencers")

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.fields["affiliate"].queryset = m.InfluencerProfile.objects.select_related("user").order_by(
                "user__first_name", "user__last_name", "user__username", "affiliate_id"
            )
            self.fields["affiliate"].label_from_instance = lambda influencer: (
                f"{influencer.user.get_full_name() or influencer.user.get_username()} ({influencer.affiliate_id})"
            )
            if self.instance.pk:
                self.fields["affiliate"].initial = self.instance.influencers.first()
            self.fields["starts_at"].help_text = "Start date cannot be earlier than today."
            widget = self.fields["starts_at"].widget
            if hasattr(widget, "widgets") and widget.widgets:
                widget.widgets[0].attrs["min"] = timezone.localdate().isoformat()

        def clean_starts_at(self):
            starts_at = self.cleaned_data.get("starts_at")
            if (
                not self.instance.pk
                and starts_at
                and timezone.localdate(starts_at) < timezone.localdate()
            ):
                raise forms.ValidationError("Start date cannot be earlier than today.")
            return starts_at

        def clean_code(self):
            code = (self.cleaned_data.get("code") or "").strip()
            if not code:
                raise forms.ValidationError("Enter a coupon code.")
            return code.upper()

        def clean(self):
            cleaned_data = super().clean()
            minimum_order_value = cleaned_data.get("minimum_order_value")
            maximum_discount = cleaned_data.get("maximum_discount")
            per_customer_limit = cleaned_data.get("per_customer_limit")

            if minimum_order_value is not None and minimum_order_value < 0:
                self.add_error("minimum_order_value", "Minimum order value cannot be negative.")
            if maximum_discount is not None and maximum_discount < 0:
                self.add_error("maximum_discount", "Maximum discount cannot be negative.")
            if per_customer_limit is not None and per_customer_limit < 1:
                self.add_error("per_customer_limit", "Per-customer limit must be at least 1.")
            return cleaned_data

        def _save_m2m(self):
            super()._save_m2m()
            affiliate = self.cleaned_data.get("affiliate")
            self.instance.influencers.set([affiliate] if affiliate else [])

    form = CouponForm
    list_display = ("code", "affiliate", "discount_type", "discount_value", "starts_at", "expires_at", "usage_count", "is_active")
    list_filter = ("discount_type", "is_active", "starts_at", "expires_at")
    search_fields = ("code", "description")
    filter_horizontal = ("products",)
    list_editable = ("is_active",)

    @admin.display(description="Uses", ordering="_sort_usage_count")
    def usage_count(self, obj):
        return obj.usages.count()

    @admin.display(description="Affiliate")
    def affiliate(self, obj):
        influencer = obj.influencers.select_related("user").first()
        if not influencer:
            return "—"
        return f"{influencer.user.get_full_name() or influencer.user.get_username()} ({influencer.affiliate_id})"


class OrderItemInline(TabularInline):
    model = m.OrderItem
    extra = 0
    autocomplete_fields = ("variant",)


class PaymentInline(TabularInline):
    model = m.Payment
    extra = 0
    fields = ("provider", "transaction_id", "status", "amount", "paid_at", "reconciliation_note")


class OrderHistoryInline(TabularInline):
    model = m.OrderStatusHistory
    extra = 0
    can_delete = False
    fields = readonly_fields = ("from_status", "to_status", "note", "changed_by", "changed_at")

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(m.Order)
class OrderAdmin(BaseAdmin):
    list_display = ("number", "placed_at", "customer", "status", "grand_total", "payment_state", "affiliate_code", "influencer")
    list_filter = ("status", "placed_at", "payments__status", "influencer")
    search_fields = ("number", "email", "phone", "affiliate_code", "influencer__affiliate_id", "customer__user__username", "payments__transaction_id")
    autocomplete_fields = ("customer", "coupon", "influencer")
    date_hierarchy = "placed_at"
    inlines = (OrderItemInline, PaymentInline, OrderHistoryInline)
    readonly_fields = ("number", "placed_at", "created_at", "updated_at")
    actions = ExportMixin.actions + ("mark_confirmed", "mark_processing", "mark_shipped", "mark_delivered", "mark_cancelled")
    export_fields = ("number", "placed_at", "email", "phone", "status", "subtotal", "discount_total", "shipping_total", "tax_total", "grand_total")

    @admin.display(description="Payment", ordering="_sort_payment_state")
    def payment_state(self, obj):
        payment = obj.payments.order_by("-created_at", "-pk").first()
        return payment.get_status_display() if payment else "Not recorded"

    def _set_status(self, request, queryset, status):
        count = 0
        for order in queryset:
            old = order.status
            if old != status:
                order.status = status
                order.save(update_fields=("status", "updated_at"))
                m.OrderStatusHistory.objects.create(order=order, from_status=old, to_status=status, changed_by=request.user)
                count += 1
        self.message_user(request, f"Updated {count} order(s).", messages.SUCCESS)

    @admin.action(description="Mark selected orders confirmed")
    def mark_confirmed(self, request, queryset): self._set_status(request, queryset, m.Order.Status.CONFIRMED)
    @admin.action(description="Mark selected orders processing")
    def mark_processing(self, request, queryset): self._set_status(request, queryset, m.Order.Status.PROCESSING)
    @admin.action(description="Mark selected orders shipped")
    def mark_shipped(self, request, queryset): self._set_status(request, queryset, m.Order.Status.SHIPPED)
    @admin.action(description="Mark selected orders delivered")
    def mark_delivered(self, request, queryset): self._set_status(request, queryset, m.Order.Status.DELIVERED)
    @admin.action(description="Mark selected orders cancelled")
    def mark_cancelled(self, request, queryset): self._set_status(request, queryset, m.Order.Status.CANCELLED)

    def save_model(self, request, obj, form, change):
        previous = m.Order.objects.get(pk=obj.pk).status if change else ""
        super().save_model(request, obj, form, change)
        if previous != obj.status:
            m.OrderStatusHistory.objects.create(order=obj, from_status=previous, to_status=obj.status, changed_by=request.user)


@admin.register(m.InfluencerProfile)
class InfluencerAdmin(BaseAdmin):
    class InfluencerForm(forms.ModelForm):
        input_attrs = {"class": " ".join(INPUT_CLASSES)}
        username = forms.CharField(max_length=150, widget=forms.TextInput(attrs={**input_attrs, "autocomplete": "username"}))
        first_name = forms.CharField(max_length=150, required=False, widget=forms.TextInput(attrs={**input_attrs, "autocomplete": "given-name"}))
        last_name = forms.CharField(max_length=150, required=False, widget=forms.TextInput(attrs={**input_attrs, "autocomplete": "family-name"}))
        email = forms.EmailField(widget=forms.EmailInput(attrs={**input_attrs, "autocomplete": "email"}))
        password = forms.CharField(widget=forms.PasswordInput(attrs={**input_attrs, "autocomplete": "new-password"}), required=False)
        confirm_password = forms.CharField(widget=forms.PasswordInput(attrs={**input_attrs, "autocomplete": "new-password"}), required=False)
        address_line_1 = forms.CharField(label="Address line 1", max_length=255, required=False, widget=forms.TextInput(attrs=input_attrs))
        address_line_2 = forms.CharField(label="Address line 2", max_length=255, required=False, widget=forms.TextInput(attrs=input_attrs))
        address_city = forms.CharField(label="City", max_length=100, required=False, widget=forms.TextInput(attrs=input_attrs))
        address_state = forms.CharField(label="State", max_length=100, required=False, widget=forms.TextInput(attrs=input_attrs))
        address_postal_code = forms.CharField(label="Postal code", max_length=20, required=False, widget=forms.TextInput(attrs=input_attrs))
        address_country = forms.CharField(label="Country", max_length=100, required=False, widget=forms.TextInput(attrs=input_attrs))

        class Meta:
            model = m.InfluencerProfile
            exclude = ("user", "address")

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.fields["profile_image"].label = "Photo"
            self.fields["password"].help_text = password_validators_help_text_html()
            self.fields["confirm_password"].help_text = "Enter the same password again."
            address = self.instance.address if self.instance and isinstance(self.instance.address, dict) else {}
            for form_name, json_name in self.address_fields.items():
                self.fields[form_name].initial = address.get(json_name, "")
            if self.instance and self.instance.pk:
                self.fields["password"].help_text = format_html(
                    "{}<p>{}</p>", password_validators_help_text_html(),
                    "Leave blank to keep the current password.",
                )
                self.fields["confirm_password"].help_text = "Enter the same password again. Leave blank if you are not changing it."
                self.fields["username"].initial = self.instance.user.username
                self.fields["first_name"].initial = self.instance.user.first_name
                self.fields["last_name"].initial = self.instance.user.last_name
                self.fields["email"].initial = self.instance.user.email

        address_fields = {
            "address_line_1": "line1",
            "address_line_2": "line2",
            "address_city": "city",
            "address_state": "state",
            "address_postal_code": "postal_code",
            "address_country": "country",
        }

        def clean_username(self):
            username = self.cleaned_data["username"].strip()
            users = get_user_model().objects.filter(username__iexact=username)
            if self.instance and self.instance.pk:
                users = users.exclude(pk=self.instance.user_id)
            if users.exists():
                raise forms.ValidationError("This username is already in use.")
            return username

        def clean_email(self):
            email = self.cleaned_data["email"].strip().lower()
            current_user_id = self.instance.user_id if self.instance and self.instance.pk else None

            # On edit, allow the influencer to keep the email already attached
            # to this account. This also avoids false validation errors when
            # old/legacy user rows contain the same email address.
            if current_user_id:
                current_email = (self.instance.user.email or "").strip().lower()
                if email == current_email:
                    return email

            return normalize_unique_user_email(email, current_user_id)

        def clean(self):
            cleaned = super().clean()
            password = cleaned.get("password")
            if not self.instance.pk and not password:
                self.add_error("password", "A password is required for a new influencer.")
            if password:
                user = self.instance.user if self.instance and self.instance.pk else get_user_model()(
                    username=cleaned.get("username", ""),
                    email=cleaned.get("email", ""),
                    first_name=cleaned.get("first_name", ""),
                    last_name=cleaned.get("last_name", ""),
                )
                try:
                    validate_password(password, user=user)
                except ValidationError as error:
                    self.add_error("password", error)
            if password != cleaned.get("confirm_password"):
                self.add_error("confirm_password", "Passwords do not match.")
            return cleaned

        @transaction.atomic
        def save(self, commit=True):
            profile = super().save(commit=False)
            if profile.pk:
                user = profile.user
                user.username = self.cleaned_data["username"]
            else:
                user = get_user_model()(username=self.cleaned_data["username"])
            user.first_name = self.cleaned_data["first_name"].strip()
            user.last_name = self.cleaned_data["last_name"].strip()
            user.email = self.cleaned_data["email"]
            if self.cleaned_data.get("password"):
                user.set_password(self.cleaned_data["password"])
            user.save()
            profile.user = user
            address = dict(profile.address) if isinstance(profile.address, dict) else {}
            for form_name, json_name in self.address_fields.items():
                value = self.cleaned_data.get(form_name, "").strip()
                if value:
                    address[json_name] = value
                else:
                    address.pop(json_name, None)
            profile.address = address
            if commit:
                profile.save()
            return profile

    form = InfluencerForm
    list_display = ("affiliate_id", "user", "is_active")
    list_filter = ("is_active", "created_at")
    search_fields = ("affiliate_id", "user__username", "user__first_name", "user__last_name", "user__email")
    readonly_fields = ("affiliate_id_display", "created_at", "updated_at")
    fieldsets = (
        ("Account credentials", {"fields": ("username", "first_name", "last_name", "email", "password", "confirm_password")}),
        ("Influencer profile", {"fields": ("affiliate_id_display", "phone", "profile_image", "is_active")}),
        ("Address", {"fields": ("address_line_1", "address_line_2", "address_city", "address_state", "address_postal_code", "address_country")}),
        ("Audit", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )

    @admin.display(description="Affiliate id")
    def affiliate_id_display(self, obj):
        if obj and getattr(obj, "affiliate_id", None):
            return obj.affiliate_id
        return "The Influencer ID will be visible once the account has been successfully created."

    @admin.display(description="Orders", ordering="_sort_referred_orders")
    def referred_orders(self, obj): return obj.orders.count()

    def message_user(self, request, message, level=messages.INFO, extra_tags="", fail_silently=False):
        """Replace Django's default influencer-created message with the invitation email message."""
        message_text = str(message)
        if "was added successfully" in message_text:
            email = (request.POST.get("email") or "").strip()
            if email:
                message = f"Invitation email sent successfully to {email}."
            else:
                message = "Invitation email sent successfully."

        return super().message_user(
            request,
            message,
            level=level,
            extra_tags=extra_tags,
            fail_silently=fail_silently,
        )

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if change:
            return

        recipient = obj.user.email
        username = obj.user.username
        display_name = obj.user.get_full_name() or username
        initial_password = form.cleaned_data["password"]
        affiliate_id = obj.affiliate_id
        login_url = settings.INFLUENCER_LOGIN_URL
        logo_url = request.build_absolute_uri(static("branding/fabriqx-logo.jpeg"))

        def send_onboarding_email():
            context = {
                "display_name": display_name,
                "logo_url": logo_url,
                "login_url": login_url,
                "username": username,
                "temporary_password": initial_password,
                "affiliate_id": affiliate_id,
            }
            send_mail(
                "Your FABRIQX influencer account",
                (
                    f"Hello {display_name},\n\n"
                    "Your FABRIQX influencer account has been created.\n\n"
                    f"Login URL: {login_url}\n"
                    f"Username: {username}\n"
                    f"Temporary password: {initial_password}\n"
                    f"Affiliate ID: {affiliate_id}\n\n"
                    "Please sign in and change your password after your first login.\n\n"
                    "FABRIQX Team"
                ),
                settings.DEFAULT_FROM_EMAIL,
                [recipient],
                html_message=render_to_string("fabriqx/emails/influencer_onboarding.html", context),
            )

        transaction.on_commit(send_onboarding_email)

@admin.register(m.InfluencerCommission)
class CommissionAdmin(BaseAdmin):
    hide_from_index = True
    list_display = ("influencer", "order", "eligible_amount", "rate", "commission_amount", "status", "paid_at")
    list_filter = ("status", "created_at", "paid_at")
    search_fields = ("influencer__affiliate_id", "order__number", "payment_reference")
    autocomplete_fields = ("influencer", "order")
    actions = ExportMixin.actions + ("approve", "mark_paid", "reverse")

    def _update(self, request, queryset, status):
        values = {"status": status}
        if status == m.InfluencerCommission.Status.PAID:
            values["paid_at"] = timezone.now()
        self.message_user(request, f"Updated {queryset.update(**values)} commission(s).", messages.SUCCESS)

    @admin.action(description="Approve selected commissions")
    def approve(self, request, queryset): self._update(request, queryset, m.InfluencerCommission.Status.APPROVED)
    @admin.action(description="Mark selected commissions paid")
    def mark_paid(self, request, queryset): self._update(request, queryset, m.InfluencerCommission.Status.PAID)
    @admin.action(description="Reverse selected commissions")
    def reverse(self, request, queryset): self._update(request, queryset, m.InfluencerCommission.Status.REVERSED)


class ReviewAttachmentInline(TabularInline):
    model = m.ReviewAttachment
    extra = 1
    fields = ("image", "preview")
    readonly_fields = ("preview",)

    @admin.display(description="Preview")
    def preview(self, obj):
        return image_list_preview(obj.image, "Review attachment") if obj and obj.image else "—"


@admin.register(m.Review)
class ReviewAdmin(BaseAdmin):
    list_display = ("product", "customer", "rating", "attachment_preview", "is_verified_purchase", "status", "created_at")
    list_filter = ("status", "rating", "is_verified_purchase", "created_at")
    search_fields = ("product__name", "customer__user__username", "title", "body")
    autocomplete_fields = ("product", "customer", "order_item")
    inlines = (ReviewAttachmentInline,)
    actions = ExportMixin.actions + ("approve", "reject")

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("attachments")

    @admin.display(description="Attachments")
    def attachment_preview(self, obj):
        previews = []
        for attachment in obj.attachments.all()[:3]:
            try:
                previews.append(attachment.image.url)
            except ValueError:
                continue
        if not previews:
            return "—"
        return format_html_join(
            "", '<img src="{}" alt="Review attachment" loading="lazy" '
            'style="width:36px;height:36px;object-fit:cover;border-radius:4px;margin-right:4px;">',
            ((url,) for url in previews),
        )

    @admin.action(description="Approve selected reviews")
    def approve(self, request, queryset): queryset.update(status=m.Review.Status.APPROVED)
    @admin.action(description="Reject selected reviews")
    def reject(self, request, queryset): queryset.update(status=m.Review.Status.REJECTED)


@admin.register(m.ProductFAQ)
class ProductFAQAdmin(BaseAdmin):
    list_display = ("product", "question_preview", "is_active", "display_order", "created_at")
    list_filter = ("is_active", "created_at")
    search_fields = ("product__name", "question", "answer")
    autocomplete_fields = ("product",)
    readonly_fields = ("created_at", "updated_at")
    actions = ExportMixin.actions + ("activate", "deactivate")

    @admin.display(description="Question")
    def question_preview(self, obj):
        return obj.question[:80] + ("…" if len(obj.question) > 80 else "")

    @admin.action(description="Activate selected FAQs")
    def activate(self, request, queryset):
        queryset.update(is_active=True)

    @admin.action(description="Deactivate selected FAQs")
    def deactivate(self, request, queryset):
        queryset.update(is_active=False)


class SimpleAdmin(BaseAdmin):
    list_display = ("__str__",)
    search_fields = ("id",)

    def get_sortable_by(self, request):
        # Display callables are created per request; use their ordering metadata.
        return None

    def get_list_display(self, request):
        ordering = {
            "productimage": F("product__name"),
            "address": Concat("full_name", Value(", "), "city"),
            "wishlistitem": Concat("customer__user__username", Value(" — "), "product__name"),
            "orderitem": Concat("order__number", Value(" — "), "product_name"),
            "orderstatushistory": Concat("order__number", Value(": "), "from_status", Value(" → "), "to_status"),
        }.get(self.model._meta.model_name)
        columns = super().get_list_display(request)
        if ordering is None:
            return columns

        @admin.display(description=self.model._meta.verbose_name, ordering=ordering)
        def record_label(obj):
            return str(obj)

        return tuple(record_label if column == "__str__" else column for column in columns)


@admin.register(m.Payment)
class PaymentAdmin(BaseAdmin):
    list_display = ("order", "provider", "transaction_id", "amount", "status", "paid_at")
    list_filter = ("provider", "status", "paid_at")
    search_fields = ("order__number", "transaction_id")
    autocomplete_fields = ("order",)
    readonly_fields = ("gateway_response", "created_at", "updated_at")


@admin.register(m.Refund)
class RefundAdmin(BaseAdmin):
    list_display = ("payment", "amount", "status", "provider_reference", "processed_at")
    list_filter = ("status", "processed_at")
    search_fields = ("payment__order__number", "provider_reference", "reason")
    autocomplete_fields = ("payment",)


@admin.register(m.Invoice)
class InvoiceAdmin(BaseAdmin):
    list_display = ("number", "order", "issued_at", "tax_identifier", "print_link")
    search_fields = ("number", "order__number", "tax_identifier")
    autocomplete_fields = ("order",)
    readonly_fields = ("number", "created_at", "updated_at")

    @admin.display(description="Invoice")
    def print_link(self, obj):
        url = reverse("fabriqx:invoice_print", args=(obj.pk,))
        return format_html('<a href="{}" target="_blank">Print / Save PDF</a>', url)


class BannerAdminForm(forms.ModelForm):
    class Meta:
        model = m.Banner
        fields = "__all__"
        help_texts = {
            "image": m.HERO_BANNER_IMAGE_HELP_TEXT,
        }
        labels = {
            "image": "Change Image",
            "link": "Shop Now Button URL",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if "image" in self.fields:
            self.fields["image"].widget.attrs.update({
                "accept": ".jpg,.jpeg,.png,.gif,.webp",
            })

    def clean_image(self):
        image = self.cleaned_data.get("image")

        if image and hasattr(image, "size"):
            if image.size > 5 * 1024 * 1024:
                raise forms.ValidationError(
                    "Image file size must not exceed 5 MB."
                )

        return image


@admin.register(m.Banner)
class BannerAdmin(BaseAdmin):
    form = BannerAdminForm

    list_display = (
        "image_thumbnail",
        "title",
        "is_active",
    )
    search_fields = (
        "title",
        "subtitle",
    )

    @admin.display(description="Image")
    def image_thumbnail(self, obj):
        return image_list_preview(obj.image, obj.title)

    readonly_fields = (
        "image_preview",
    )

    # Banner add/edit page: requested fields only.
    fields = (
        "image",
        "title",
        "subtitle",
        "link",
    )

    @admin.display(description="Image Preview")
    def image_preview(self, obj):
        image_url = ""

        if obj and obj.pk and obj.image:
            try:
                image_url = obj.image.url
            except Exception:
                image_url = ""

        return format_html(
            """
            <div
                id="banner-preview-wrapper"
                class="banner-preview-wrapper"
                style="display:{};"
            >
                <img
                    id="banner-image-preview"
                    class="banner-image-preview"
                    src="{}"
                    alt="Banner preview"
                    style="
                        display:block;
                        max-width:260px;
                        max-height:130px;
                        width:auto;
                        height:auto;
                        object-fit:contain;
                        border:1px solid #e5e7eb;
                        border-radius:8px;
                        padding:6px;
                        background:#fff;
                    "
                />
            </div>
            <div
                id="banner-preview-empty"
                style="display:{}; color:#6b7280;"
            >
                Select an image to preview it here.
            </div>
            """,
            "block" if image_url else "none",
            image_url,
            "none" if image_url else "block",
        )


@admin.register(m.HomepageSection)
class SectionAdmin(BaseAdmin):
    list_display = ("title", "section_type", "display_order", "is_active", "updated_at")
    list_filter = ("section_type", "is_active")
    fieldsets = (
        (None, {"fields": ("title", "section_type", "content", "editor_content", "display_order", "is_active")}),
    )


class GiftSectionFeatureInline(TabularInline):
    model = m.GiftSectionFeature
    classes = ("gift-section-features-inline",)
    extra = 5
    min_num = 5
    max_num = 5
    fields = ("icon", "text", "display_order", "is_active")

    def get_formset(self, request, obj=None, **kwargs):
        kwargs["validate_min"] = True
        kwargs["validate_max"] = True
        return super().get_formset(request, obj, **kwargs)

    def has_add_permission(self, request, obj=None):
        return request.user.has_perm("content_management.change_giftsection") or request.user.has_perm("fabriqx.change_giftsection")

    def has_change_permission(self, request, obj=None):
        return self.has_add_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return self.has_add_permission(request, obj)


class GiftSectionStatisticInline(TabularInline):
    class GiftSectionStatisticForm(forms.ModelForm):
        class Meta:
            model = m.GiftSectionStatistic
            fields = "__all__"
            labels = {"eyebrow": "Small text above value"}

    model = m.GiftSectionStatistic
    classes = ("gift-section-statistics-inline",)
    form = GiftSectionStatisticForm
    extra = 3
    min_num = 3
    max_num = 3
    fields = ("icon", "eyebrow", "value", "label", "display_order", "is_active")

    def get_formset(self, request, obj=None, **kwargs):
        kwargs["validate_min"] = True
        kwargs["validate_max"] = True
        return super().get_formset(request, obj, **kwargs)

    def has_add_permission(self, request, obj=None):
        return request.user.has_perm("content_management.change_giftsection") or request.user.has_perm("fabriqx.change_giftsection")

    def has_change_permission(self, request, obj=None):
        return self.has_add_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return self.has_add_permission(request, obj)


@admin.register(m.GiftSection)
class GiftSectionAdmin(BaseAdmin):
    class GiftSectionAdminForm(forms.ModelForm):
        class Meta:
            model = m.GiftSection
            fields = "__all__"
            labels = {
                "badge_icon": "Top-left free-gift badge image",
                "gift_image": "Bottom-right gift-box image",
            }
            help_texts = {
                "badge_icon": "Displayed over the top-left of the main campaign image.",
                "gift_image": "Displayed beside the thank-you message at the bottom-right.",
            }

    form = GiftSectionAdminForm
    list_display = (
        "image_thumbnail",
        "name",
        "heading",
        "is_active",
    )
    search_fields = (
        "internal_name",
        "heading",
        "description",
    )

    @admin.display(description="Image")
    def image_thumbnail(self, obj):
        return image_list_preview(obj.main_image, obj.heading)

    @admin.display(description="Name", ordering="internal_name")
    def name(self, obj):
        return obj.internal_name

    # Features and statistics stay on the same Gift Section edit page.
    inlines = (
        GiftSectionFeatureInline,
        GiftSectionStatisticInline,
    )

    fieldsets = (
        (
            "Campaign",
            {
                "fields": (
                    "internal_name",
                    "display_order",
                    "starts_at",
                    "ends_at",
                    "is_active",
                )
            },
        ),
        (
            "Offer badge",
            {
                "fields": (
                    "badge_eyebrow",
                    "badge_title",
                    "badge_icon",
                )
            },
        ),
        (
            "Main content",
            {
                "fields": (
                    "logo",
                    "accent_heading",
                    "heading",
                    "description",
                    "main_image",
                    "gift_image",
                    "background_image",
                )
            },
        ),
        (
            "Thank-you message",
            {
                "fields": (
                    "thank_you_title",
                    "thank_you_text",
                )
            },
        ),
        (
            "Call to action",
            {
                "fields": (
                    "cta_label",
                    "cta_url",
                )
            },
        ),
    )

    def has_add_permission(self, request):
        return (
            super().has_add_permission(request)
            and not m.GiftSection.objects.exists()
        )

    def render_change_form(
        self,
        request,
        context,
        add=False,
        change=False,
        form_url="",
        obj=None,
    ):
        context["show_save_and_add_another"] = False
        return super().render_change_form(
            request,
            context,
            add,
            change,
            form_url,
            obj,
        )


class BrandLogoFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        additions = [form for form in self.extra_forms if form.has_changed() and not form.cleaned_data.get("DELETE")]
        if len(additions) > 1:
            raise forms.ValidationError("Only one brand logo can be added at a time.")


class BrandLogoAdminForm(forms.ModelForm):
    class Meta:
        model = m.BrandLogo
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["logo"].widget.attrs["accept"] = ".jpg,.jpeg,.png,.gif"
        self.fields["logo"].label = "Update the Logo"
        self.fields["brand_name"].label = "Logo Name"
        self.fields["brand_name"].help_text = "This name will be visible on hover in web view."
        self.fields["brand_name"].widget.attrs["placeholder"] = "This name will be visible on hover in web view."


class BrandLogoAdmin(BaseAdmin):
    form = BrandLogoAdminForm
    list_display = ("image_thumbnail", "logo_name", "logo_status", "created_at", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("brand_name",)
    readonly_fields = ("logo_preview",)
    fieldsets = (
        ("Logo details", {"fields": ("logo",)}),
        ("Brand", {"fields": ("brand_name",)}),
        ("Display", {"fields": ("is_active", "display_order")}),
    )

    @admin.display(description="Logo Name", ordering="brand_name")
    def logo_name(self, obj):
        return obj.brand_name

    @admin.display(description="Logo")
    def image_thumbnail(self, obj):
        return image_list_preview(obj.logo, obj.brand_name)

    @admin.display(description="Status", boolean=True, ordering="is_active")
    def logo_status(self, obj):
        return obj.is_active

    @admin.display(description="Logo preview")
    def logo_preview(self, obj):
        return format_html('<div class="admin-image-preview"><img data-image-preview="logo" src="{}" alt="{}" {}><p data-image-empty="logo" {}>Choose a logo to preview it.</p></div>',
                           obj.logo.url if obj and obj.logo else "", obj.brand_name if obj else "Brand logo",
                           "" if obj and obj.logo else "hidden", "hidden" if obj and obj.logo else "")

    def get_fieldsets(self, request, obj=None):
        # Updated At is a list column, never an editable audit field.
        return super().get_fieldsets(request, obj)

    def save_model(self, request, obj, form, change):
        if not obj.section_id:
            obj.section = m.BrandLogoSection.objects.order_by("pk").first() or m.BrandLogoSection.objects.create()
        obj.alt_text = obj.brand_name
        super().save_model(request, obj, form, change)

    def has_view_permission(self, request, obj=None):
        return super().has_view_permission(request, obj) or request.user.has_perm("content_management.view_brandlogosection")

    def has_add_permission(self, request):
        return super().has_add_permission(request) or request.user.has_perm("content_management.add_brandlogosection")

    def has_change_permission(self, request, obj=None):
        return super().has_change_permission(request, obj) or request.user.has_perm("content_management.change_brandlogosection")

    def has_delete_permission(self, request, obj=None):
        return super().has_delete_permission(request, obj) or request.user.has_perm("content_management.delete_brandlogosection")


class BrandLogoInline(TabularInline):
    form = BrandLogoAdminForm
    formset = BrandLogoFormSet
    model = m.BrandLogo
    extra = 0
    min_num = 1
    fields = ("logo", "brand_name", "display_order", "is_active")

    def get_extra(self, request, obj=None, **kwargs):
        return 1 if obj and obj.logos.exists() and request.GET.get("add_logo") else 0

    def get_max_num(self, request, obj=None, **kwargs):
        return (obj.logos.count() if obj else 0) + 1

    def get_formset(self, request, obj=None, **kwargs):
        kwargs["validate_min"] = True
        kwargs["validate_max"] = True
        return super().get_formset(request, obj, **kwargs)

    def has_add_permission(self, request, obj=None):
        return request.user.has_perm("content_management.change_brandlogosection") or request.user.has_perm("fabriqx.change_brandlogosection")

    def has_change_permission(self, request, obj=None):
        return self.has_add_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return self.has_add_permission(request, obj)


@admin.register(m.BrandLogoSection)
class BrandLogoSectionAdmin(BaseAdmin):
    def get_model_perms(self, request):
        return {}

    hide_default_add_button = True
    actions_list = ("add_brand_logo",)

    def changelist_view(self, request, extra_context=None):
        if not self.has_view_or_change_permission(request):
            raise PermissionDenied
        return redirect("admin:content_management_brandlogo_changelist")

    @action(description="Add brand logo", permissions=("change",), icon="add", url_path="add-brand-logo")
    def add_brand_logo(self, request):
        section = self.get_queryset(request).first()
        opts = self.model._meta
        if section is None:
            if not self.has_add_permission(request):
                raise PermissionDenied
            return redirect(reverse(f"admin:{opts.app_label}_{opts.model_name}_add"))
        if not self.has_change_permission(request, section):
            raise PermissionDenied
        url = reverse(f"admin:{opts.app_label}_{opts.model_name}_change", args=(section.pk,))
        return redirect(f"{url}?add_logo=1#logos-group")

    list_display = ("internal_name", "is_active", "created_at", "updated_at")
    inlines = (BrandLogoInline,)
    exclude = ("internal_name", "heading", "background_color", "is_active")

    def has_add_permission(self, request):
        return super().has_add_permission(request) and not m.BrandLogoSection.objects.exists()

    def render_change_form(self, request, context, add=False, change=False, form_url="", obj=None):
        context["show_save_and_add_another"] = False
        return super().render_change_form(request, context, add, change, form_url, obj)


@admin.register(m.OfferBanner)
class OfferBannerAdmin(BaseAdmin):
    list_display = ("image_thumbnail", "internal_name", "is_active")
    list_filter = ("is_active",)
    search_fields = ("internal_name", "alt_text", "shop_now_url")

    @admin.display(description="Image")
    def image_thumbnail(self, obj):
        return image_list_preview(obj.desktop_image, obj.alt_text or obj.internal_name)
    fieldsets = (
        ("Banner", {"fields": ("internal_name", "desktop_image", "mobile_image", "alt_text")}),
        ("Shop now link", {"fields": ("shop_now_url", "open_in_new_tab")}),
        ("Display", {"fields": ("is_active",)}),
    )


class OfferGridItemAdminForm(forms.ModelForm):
    class Meta:
        model = m.OfferGridItem
        fields = (
            "internal_name",
            "desktop_image",
            "shop_now_url",
            "is_active",
        )
        labels = {
            "internal_name": "Offer name",
            "desktop_image": "Change Image",
            "shop_now_url": "Shop Now Button URL",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if "desktop_image" in self.fields:
            self.fields["desktop_image"].widget.attrs.update({
                "accept": ".jpg,.jpeg,.png,.gif",
                "class": "offer-grid-image-input",
            })

    def save(self, commit=True):
        instance = super().save(commit=False)

        # internal_name is required by the database model but does not need
        # to be exposed in the simplified Offer Grid admin UI.
        if not instance.internal_name:
            instance.internal_name = "Offer grid item " + uuid4().hex[:8]

        if commit:
            instance.save()
            self.save_m2m()

        return instance


class OfferGridItemInline(TabularInline):
    model = m.OfferGridItem
    form = OfferGridItemAdminForm

    extra = 1
    min_num = 1
    max_num = 3

    readonly_fields = (
        "image_preview",
    )

    # Requested Offer Grid fields only.
    fields = (
        "desktop_image",
        "shop_now_url",
    )

    @admin.display(description="Image Preview")
    def image_preview(self, obj):
        image_url = ""

        if obj and obj.pk and obj.desktop_image:
            try:
                image_url = obj.desktop_image.url
            except Exception:
                image_url = ""

        return format_html(
            """
            <div class="offer-grid-preview-wrapper">
                <img
                    class="offer-grid-image-preview"
                    src="{}"
                    alt="Offer grid preview"
                    style="
                        display:{};
                        max-width:180px;
                        max-height:110px;
                        width:auto;
                        height:auto;
                        object-fit:contain;
                        border:1px solid #e5e7eb;
                        border-radius:6px;
                        padding:4px;
                        background:#fff;
                    "
                />
                <span
                    class="offer-grid-preview-empty"
                    style="display:{};color:#6b7280;"
                >
                    Select an image
                </span>
            </div>
            """,
            image_url,
            "block" if image_url else "none",
            "none" if image_url else "inline",
        )

    def has_add_permission(self, request, obj=None):
        return (
            request.user.has_perm(
                "content_management.change_offergridsection"
            )
            or request.user.has_perm(
                "fabriqx.change_offergridsection"
            )
        )

    def has_change_permission(self, request, obj=None):
        return self.has_add_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return self.has_add_permission(request, obj)


@admin.register(m.OfferGridSection)
class OfferGridSectionAdmin(BaseAdmin):
    list_display = (
        "internal_name",
        "is_active",
    )
    inlines = (
        OfferGridItemInline,
    )
    fields = (
        "internal_name",
        "is_active",
    )

    def has_add_permission(self, request):
        return (
            super().has_add_permission(request)
            and not m.OfferGridSection.objects.exists()
        )

    def render_change_form(
        self,
        request,
        context,
        add=False,
        change=False,
        form_url="",
        obj=None,
    ):
        context["show_save_and_add_another"] = False
        return super().render_change_form(
            request,
            context,
            add,
            change,
            form_url,
            obj,
        )


class OfferGridItemListAdmin(BaseAdmin):
    """Manage each homepage offer tile independently, without the section wrapper."""

    form = OfferGridItemAdminForm
    list_display = ("image_thumbnail", "internal_name", "shop_now_url", "is_active")
    list_filter = ("is_active",)
    search_fields = ("internal_name", "alt_text", "shop_now_url")
    fields = ("internal_name", "desktop_image", "shop_now_url", "is_active")

    @admin.display(description="Image")
    def image_thumbnail(self, obj):
        return image_list_preview(obj.desktop_image, obj.alt_text or obj.internal_name)

    def save_model(self, request, obj, form, change):
        if not obj.section_id:
            obj.section = m.OfferGridSection.objects.order_by("pk").first()
            if obj.section is None:
                obj.section = m.OfferGridSection.objects.create(internal_name="Homepage offer grid")
        super().save_model(request, obj, form, change)


class FooterSocialLinkInline(TabularInline):
    class FooterSocialLinkForm(forms.ModelForm):
        class Meta:
            model = m.FooterSocialLink
            fields = "__all__"
            # Explicitly use a normal URL input. This prevents any legacy
            # clearable-file widget markup ("Currently: …") from appearing.
            widgets = {"url": forms.URLInput}

    model = m.FooterSocialLink
    form = FooterSocialLinkForm
    extra = 4
    classes = ("footer-social-links-inline",)
    min_num = 4
    max_num = 4
    fields = ("platform_name", "url", "display_order", "is_active")

    def get_formset(self, request, obj=None, **kwargs):
        kwargs["validate_min"] = True
        kwargs["validate_max"] = True
        return super().get_formset(request, obj, **kwargs)

    def has_add_permission(self, request, obj=None):
        return request.user.has_perm("content_management.change_footersocialsection") or request.user.has_perm("fabriqx.change_footersocialsection")

    def has_change_permission(self, request, obj=None):
        return self.has_add_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return self.has_add_permission(request, obj)


@admin.register(m.FooterSocialSection)
class FooterSocialSectionAdmin(BaseAdmin):
    list_display = ("internal_name", "heading", "is_active", "updated_at")
    inlines = (FooterSocialLinkInline,)
    exclude = ("internal_name", "heading", "is_active")

    def has_add_permission(self, request):
        return super().has_add_permission(request) and not m.FooterSocialSection.objects.exists()

    def render_change_form(self, request, context, add=False, change=False, form_url="", obj=None):
        context["show_save_and_add_another"] = False
        return super().render_change_form(request, context, add, change, form_url, obj)


class TestimonialAdminForm(forms.ModelForm):
    class Meta:
        model = m.Testimonial
        fields = "__all__"
        labels = {
            "customer_name": "Customer Name",
            "image": "Customer Image",
            "sub_text": "Sub Text",
            "content": "Content",
            "rating": "Rating",
            "is_active": "Active",
        }
        help_texts = {
            "image": "Optional. Allowed formats: JPG, JPEG, PNG, GIF. Maximum file size: 5 MB.",
            "rating": "Enter a rating between 1 and 5.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if "image" in self.fields:
            self.fields["image"].required = False
            self.fields["image"].widget.attrs.update({
                "accept": ".jpg,.jpeg,.png,.gif",
            })


@admin.register(m.Testimonial)
class TestimonialAdmin(BaseAdmin):
    form = TestimonialAdminForm

    list_display = (
        "image_thumbnail",
        "customer_name",
        "sub_text",
        "rating",
        "is_active",
    )
    list_filter = (
        "rating",
        "is_active",
    )
    search_fields = (
        "customer_name",
        "sub_text",
        "content",
    )

    @admin.display(description="Image")
    def image_thumbnail(self, obj):
        return image_list_preview(obj.image, obj.customer_name)
    fields = (
        "customer_name",
        "image",
        "sub_text",
        "content",
        "rating",
        "is_active",
    )


@admin.register(m.NewsletterSubscription)
class NewsletterAdmin(BaseAdmin):
    list_display = ("email", "is_active")
    list_filter = ("is_active",)
    search_fields = ("email",)

    def get_search_results(self, request, queryset, search_term):
        # Treat an email query literally, including spaces and punctuation,
        # instead of Django's quoted-word search syntax.
        term = search_term.strip()
        return (queryset.filter(email__icontains=term) if term else queryset), False

    def has_add_permission(self, request):
        return False


class NewsletterSettingsAdminForm(forms.ModelForm):
    class Meta:
        model = m.NewsletterSettings
        fields = (
            "title",
            "description",
        )
        labels = {
            "title": "Title",
            "description": "Content",
        }
        widgets = {
            "description": WysiwygWidget,
        }


@admin.register(m.NewsletterSettings)
class NewsletterSettingsAdmin(BaseAdmin):
    form = NewsletterSettingsAdminForm

    list_display = (
        "title",
    )

    fields = (
        "title",
        "description",
    )

    def has_add_permission(self, request):
        return (
            super().has_add_permission(request)
            and not m.NewsletterSettings.objects.exists()
        )

    def render_change_form(
        self,
        request,
        context,
        add=False,
        change=False,
        form_url="",
        obj=None,
    ):
        context["show_save_and_add_another"] = False
        return super().render_change_form(
            request,
            context,
            add,
            change,
            form_url,
            obj,
        )


@admin.register(m.SiteSettings)
class SiteSettingsAdmin(BaseAdmin):
    class SiteSettingsForm(forms.ModelForm):
        class Meta:
            model = m.SiteSettings
            fields = "__all__"

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.fields["commission_rate"].required = False
            self.fields["commission_fixed_amount"].required = False

    form = SiteSettingsForm

    fields = (
        ("commission_type", "commission_rate"),
        "commission_fixed_amount",
    )

    conditional_fields = {
        "commission_rate": "commission_type == 'percentage'",
        "commission_fixed_amount": "commission_type == 'fixed'",
    }

    def has_add_permission(self, request):
        return (
            super().has_add_permission(request)
            and not m.SiteSettings.objects.exists()
        )

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        settings_object = m.SiteSettings.load()

        return redirect(
            "admin:fabriqx_sitesettings_change",
            settings_object.pk,
        )

    def response_change(self, request, obj):
        return redirect(
            "admin:fabriqx_sitesettings_change",
            obj.pk,
        )

    def render_change_form(
        self,
        request,
        context,
        add=False,
        change=False,
        form_url="",
        obj=None,
    ):
        # Hide unwanted buttons on Site Settings
        context["show_history"] = False
        context["show_save_and_continue"] = False
        context["show_save_and_add_another"] = False
        context["show_delete"] = False

        return super().render_change_form(
            request,
            context,
            add=add,
            change=change,
            form_url=form_url,
            obj=obj,
        )


class PageAdminForm(forms.ModelForm):
    class Meta:
        model = m.Page
        fields = (
            "title",
            "slug",
            "content",
            "is_active",
        )
        widgets = {
            "content": FabriqxWysiwygWidget,
        }
        labels = {
            "title": "Page Title",
            "slug": "Page URL",
            "content": "Content",
            "is_active": "Active",
        }


@admin.register(m.Page)
class PageAdmin(BaseAdmin):
    form = PageAdminForm

    list_display = (
        "title",
        "slug",
        "is_active",
    )
    search_fields = (
        "title",
        "slug",
        "content",
    )
    prepopulated_fields = {
        "slug": ("title",),
    }
    fields = (
        "title",
        "slug",
        "content",
        "is_active",
    )


@admin.register(m.ContactSubmission)
class ContactSubmissionAdmin(BaseAdmin):
    list_display = ("name", "email", "phone", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("name", "email", "phone", "message")
    row_actions = ("view", "delete")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return super().has_delete_permission(request, obj)


@admin.register(m.Shipment)
class ShipmentAdmin(BaseAdmin):
    list_display = ("order", "provider", "courier", "tracking_number", "status", "dispatched_at", "delivered_at")
    list_filter = ("provider", "status", "dispatched_at", "delivered_at")
    search_fields = ("order__number", "shipment_id", "tracking_number", "courier")
    autocomplete_fields = ("order",)
    readonly_fields = ("provider_response", "created_at", "updated_at")


@admin.register(m.ServiceablePincode)
class PincodeAdmin(BaseAdmin):
    hide_from_index = True
    list_display = ("pincode", "city", "state", "cash_on_delivery", "estimated_days", "is_active")
    list_filter = ("state", "cash_on_delivery", "is_active")
    search_fields = ("pincode", "city", "state")
    list_editable = ("cash_on_delivery", "estimated_days", "is_active")


@admin.register(m.IntegrationEvent)
class IntegrationAdmin(BaseAdmin):
    hide_from_index = True
    list_display = ("provider", "event_type", "direction", "external_id", "succeeded", "status_code", "created_at")
    list_filter = ("provider", "direction", "succeeded", "created_at")
    search_fields = ("provider", "event_type", "external_id", "error_message")
    readonly_fields = tuple(field.name for field in m.IntegrationEvent._meta.fields)
    def has_add_permission(self, request): return False
    def has_delete_permission(self, request, obj=None): return request.user.is_superuser


@admin.register(m.CouponUsage)
class CouponUsageAdmin(BaseAdmin):
    list_display = ("coupon", "customer", "order", "discount_amount", "used_at")
    list_filter = ("coupon", "used_at")
    search_fields = ("coupon__code", "order__number", "customer__user__username")
    readonly_fields = tuple(field.name for field in m.CouponUsage._meta.fields)
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False


@admin.register(m.AuditLog)
class AuditAdmin(BaseAdmin):
    hide_from_index = True
    list_display = ("created_at", "actor", "action", "model_name", "object_repr")
    list_filter = ("action", "model_name", "created_at")
    search_fields = ("actor__username", "object_repr", "object_id")
    readonly_fields = tuple(field.name for field in m.AuditLog._meta.fields)
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return request.user.is_superuser


for model in (m.ProductImage, m.Address, m.WishlistItem, m.OrderItem, m.OrderStatusHistory):
    admin.site.register(model, SimpleAdmin)


class ReadOnlyReportAdmin(BaseAdmin):
    date_hierarchy = None
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False


class StaticReportAdmin(ReadOnlyReportAdmin):
    """Base view for report pages whose contents respect the selected period."""

    report_columns = ()
    report_rows = ()
    report_cards = ()
    report_chart_labels = ()
    report_chart_values = ()
    show_report_chart = True
    report_heading = "Overview"

    @staticmethod
    def format_currency(value):
        return f"₹{float(value or 0):,.0f}"

    def get_report_rows(self, start_date, end_date):
        return self.report_rows

    def get_report_cards(self, start_date, end_date):
        return self.report_cards

    def get_report_chart(self, start_date, end_date):
        return {
            "labels": list(self.report_chart_labels),
            "datasets": [{
                "label": self.model._meta.verbose_name,
                "data": list(self.report_chart_values),
                "backgroundColor": "rgba(207, 126, 39, 0.72)",
                "borderColor": "rgb(170, 93, 29)",
                "borderWidth": 1,
            }],
        }

    def changelist_view(self, request, extra_context=None):
        if not self.has_view_permission(request):
            raise PermissionDenied
        valid_ranges = dict(DATE_RANGES)
        date_range = request.GET.get("date_range", DEFAULT_RANGE)
        date_range = date_range if date_range in valid_ranges else DEFAULT_RANGE
        start_date, end_date = _period(date_range)
        chart = self.get_report_chart(start_date, end_date)
        context = {
            **self.admin_site.each_context(request),
            "title": self.model._meta.verbose_name_plural.title(),
            "opts": self.model._meta,
            "report_columns": self.report_columns,
            "report_rows": self.get_report_rows(start_date, end_date),
            "report_cards": self.get_report_cards(start_date, end_date),
            "report_heading": self.report_heading,
            "report_chart": json.dumps(chart),
            "date_ranges": DATE_RANGES,
            "date_range": date_range,
            "date_range_label": valid_ranges[date_range],
            "chart_options": json.dumps({
                "responsive": True,
                "maintainAspectRatio": False,
                "plugins": {"legend": {"display": False}},
                "scales": {"y": {"beginAtZero": True}},
            }),
            **(extra_context or {}),
        }
        return render(request, "fabriqx/static_report.html", context)


@admin.register(m.SalesReport)
class SalesReportAdmin(StaticReportAdmin):
    report_columns = ("Order", "Date", "Customer", "Status", "Items", "Total")
    report_rows = (
        ("ORD-2026-1048", "08 Sep 2026", "Aarav Sharma", "Delivered", "3", "₹4,899"),
        ("ORD-2026-1047", "08 Sep 2026", "Meera Patel", "Shipped", "1", "₹2,499"),
        ("ORD-2026-1046", "07 Sep 2026", "Ananya Rao", "Processing", "4", "₹7,250"),
        ("ORD-2026-1045", "07 Sep 2026", "Kabir Singh", "Confirmed", "2", "₹3,799"),
    )
    report_cards = (("Total sales", "₹8,42,650"), ("Orders", "178"), ("Average order", "₹4,734"))
    report_chart_labels = ("02 Sep", "03 Sep", "04 Sep", "05 Sep", "06 Sep", "07 Sep", "08 Sep")
    report_chart_values = (42500, 51800, 47600, 69200, 73500, 88100, 64200)

    def get_orders(self, start_date, end_date):
        return m.Order.objects.filter(placed_at__date__range=(start_date, end_date))

    def get_report_rows(self, start_date, end_date):
        rows = [
            (
                order.number,
                timezone.localtime(order.placed_at).strftime("%d %b %Y"),
                order.customer or order.email,
                order.get_status_display(),
                str(order.items.count()),
                self.format_currency(order.grand_total),
            )
            for order in self.get_orders(start_date, end_date).select_related("customer__user").prefetch_related("items")[:100]
        ]
        return rows or self.report_rows

    def get_report_cards(self, start_date, end_date):
        orders = self.get_orders(start_date, end_date)
        totals = orders.aggregate(total=Sum("grand_total"), count=Count("id"))
        count = totals["count"]
        total = totals["total"] or 0
        if not count:
            return self.report_cards
        return (
            ("Total sales", self.format_currency(total)),
            ("Orders", f"{count:,}"),
            ("Average order", self.format_currency(total / count if count else 0)),
        )

    def get_report_chart(self, start_date, end_date):
        totals = self.get_orders(start_date, end_date).annotate(
            day=TruncDay("placed_at")
        ).values("day").annotate(total=Sum("grand_total")).order_by("day")
        if not totals:
            return super().get_report_chart(start_date, end_date)
        return {
            "labels": [timezone.localtime(row["day"]).strftime("%d %b") for row in totals],
            "datasets": [{
                "label": "Sales",
                "data": [float(row["total"] or 0) for row in totals],
                "backgroundColor": "rgba(207, 126, 39, 0.72)",
                "borderColor": "rgb(170, 93, 29)",
                "borderWidth": 1,
            }],
        }


@admin.register(m.InventoryReport)
class InventoryReportAdmin(StaticReportAdmin):
    report_columns = ("SKU", "Product", "Category", "Variant", "Stock", "Status")
    report_rows = (
        ("RYE-SAREE-01", "Royal Teal Embroidered Saree", "Apparel", "Teal", "42", "In stock"),
        ("GOLD-JUTTI-02", "Golden Embroidered Jutti", "Footwear", "EU 38", "8", "Low stock"),
        ("IVORY-KURTA-M", "Ivory & Black Kurta Set", "Apparel", "M", "27", "In stock"),
        ("MIDNIGHT-SUIT-L", "Midnight Blue Suit Set", "Apparel", "L", "0", "Out of stock"),
    )
    report_cards = (("Total units", "1,864"), ("Low stock", "14"), ("Out of stock", "6"))
    report_chart_labels = ("Apparel", "Footwear", "Accessories", "Jewellery")
    report_chart_values = (100, 150, 50, 25)

    def get_movements(self, start_date, end_date):
        return m.InventoryMovement.objects.filter(created_at__date__range=(start_date, end_date))

    def get_report_rows(self, start_date, end_date):
        rows = [
            (
                movement.variant.sku,
                movement.variant.product.name,
                movement.variant.product.category.name,
                ", ".join(filter(None, (movement.variant.size, movement.variant.color))) or "—",
                f"{movement.quantity:+d}",
                movement.get_movement_type_display(),
            )
            for movement in self.get_movements(start_date, end_date).select_related("variant__product__category")[:100]
        ]
        return rows or self.report_rows

    def get_report_cards(self, start_date, end_date):
        movements = self.get_movements(start_date, end_date)
        if not movements.exists():
            return self.report_cards
        added = movements.filter(quantity__gt=0).aggregate(total=Sum("quantity"))["total"] or 0
        removed = movements.filter(quantity__lt=0).aggregate(total=Sum("quantity"))["total"] or 0
        return (
            ("Stock movements", f"{movements.count():,}"),
            ("Units added", f"{added:,}"),
            ("Units removed", f"{abs(removed):,}"),
        )

    def get_report_chart(self, start_date, end_date):
        totals = {
            row["movement_type"]: row["total"]
            for row in m.InventoryMovement.objects.filter(
                created_at__date__range=(start_date, end_date)
            ).values("movement_type").annotate(total=Count("id"))
        }
        if not totals:
            return super().get_report_chart(start_date, end_date)
        types = tuple(m.InventoryMovement.MovementType)
        return {
            "labels": [movement.label for movement in types],
            "datasets": [{
                "label": "Inventory movements",
                "data": [totals.get(movement, 0) for movement in types],
                "backgroundColor": "rgba(207, 126, 39, 0.72)",
                "borderColor": "rgb(170, 93, 29)",
                "borderWidth": 1,
            }],
        }


@admin.register(m.InfluencerReport)
class InfluencerReportAdmin(StaticReportAdmin):
    show_report_chart = True
    report_heading = "Influencer performance summary"
    report_columns = ("Affiliate ID", "Influencer", "Referred orders", "Referral sales", "Paid", "Outstanding", "Status")
    report_rows = (
        ("INF-A7K-2Q91", "Ananya Krishnan", "38", "₹1,84,500", "₹14,200", "₹4,250", "Active"),
        ("INF-M9P-4R82", "Meera Agarwal", "31", "₹1,42,800", "₹11,500", "₹2,780", "Active"),
        ("INF-P3S-8T64", "Priya Sharma", "26", "₹1,16,250", "₹9,800", "₹1,825", "Active"),
        ("INF-N6V-1B53", "Neha Verma", "19", "₹82,900", "₹6,100", "₹2,190", "Inactive"),
    )
    report_cards = (
        ("Active influencers", "32"),
        ("Referred orders", "114"),
        ("Referral sales", "₹8,76,450"),
    )
    report_chart_labels = ("Ananya", "Meera", "Priya", "Neha")
    report_chart_values = (184500, 142800, 116250, 82900)

    def get_orders(self, start_date, end_date):
        return m.Order.objects.filter(
            placed_at__date__range=(start_date, end_date), influencer__isnull=False,
        )

    def get_report_rows(self, start_date, end_date):
        rows = self.get_orders(start_date, end_date).values(
            "influencer__affiliate_id", "influencer__user__first_name", "influencer__user__last_name", "influencer__user__username", "influencer__is_active"
        ).annotate(
            orders=Count("id"), sales=Sum("grand_total"), paid=Sum("commission__commission_amount", filter=Q(commission__status=m.InfluencerCommission.Status.PAID)),
            outstanding=Sum("commission__commission_amount", filter=~Q(commission__status=m.InfluencerCommission.Status.PAID)),
        ).order_by("-sales")
        report_rows = [
            (
                row["influencer__affiliate_id"],
                " ".join(filter(None, (row["influencer__user__first_name"], row["influencer__user__last_name"]))) or row["influencer__user__username"],
                str(row["orders"]), self.format_currency(row["sales"]), self.format_currency(row["paid"]), self.format_currency(row["outstanding"]),
                "Active" if row["influencer__is_active"] else "Inactive",
            ) for row in rows
        ]
        return report_rows or self.report_rows

    def get_report_cards(self, start_date, end_date):
        orders = self.get_orders(start_date, end_date)
        totals = orders.aggregate(orders=Count("id"), sales=Sum("grand_total"))
        if not totals["orders"]:
            return self.report_cards
        return (
            ("Active influencers", f"{orders.filter(influencer__is_active=True).values('influencer_id').distinct().count():,}"),
            ("Referred orders", f"{totals['orders']:,}"),
            ("Referral sales", self.format_currency(totals["sales"] or 0)),
        )

    def get_report_chart(self, start_date, end_date):
        rows = m.Order.objects.filter(
            placed_at__date__range=(start_date, end_date),
            influencer__isnull=False,
        ).values("influencer__affiliate_id").annotate(total=Sum("grand_total")).order_by("-total")[:8]
        if not rows:
            return super().get_report_chart(start_date, end_date)
        return {
            "labels": [row["influencer__affiliate_id"] for row in rows],
            "datasets": [{
                "label": "Referral sales",
                "data": [float(row["total"] or 0) for row in rows],
                "backgroundColor": "rgba(207, 126, 39, 0.72)",
                "borderColor": "rgb(170, 93, 29)",
                "borderWidth": 1,
            }],
        }


admin.site.index_template = "fabriqx/admin_dashboard.html"
UnfoldAdminSite.index_template = "fabriqx/admin_dashboard.html"

if admin.site.is_registered(Group):
    admin.site.unregister(Group)
