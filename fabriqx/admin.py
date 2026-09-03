import csv
import json
from io import BytesIO
from urllib.parse import urlencode

from django.contrib import admin, messages
from django import forms
from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.contrib.auth.models import Group, Permission
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.db import transaction
from django.db import models as django_models
from django.db.models import Q
from django.core.mail import send_mail
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from openpyxl import Workbook
from unfold.admin import ModelAdmin, StackedInline, TabularInline
from unfold.contrib.forms.widgets import WysiwygWidget
from unfold.decorators import action
from unfold.sites import UnfoldAdminSite
from unfold.widgets import INPUT_CLASSES

from . import models as m
from .product_import import SAMPLE_COLUMNS, SAMPLE_ROW, import_products as run_product_import


# Only models represented by a visible, delegable admin-sidebar item belong in
# the staff access matrix. Admin-account management stays superuser-only.
STAFF_MENU_MODELS = {
    "content_management": {"banner", "brandlogosection", "footersocialsection", "giftsection", "newslettersettings", "newslettersubscription", "offerbanner", "offergridsection", "page", "testimonial"},
    "customers": {"customerprofile", "wishlistitem"},
    "influencers": {"influencerprofile", "influencerreport"},
    "products": {"category", "coupon", "inventorymovement", "inventoryreport", "productimage", "productvariant", "product"},
}
STAFF_MENU_LABELS = {
    "content_management": "Content Management System",
    "customers": "Customers",
    "influencers": "Influencers & Affiliates",
    "products": "Products & Inventory",
}
STAFF_MENU_ORDER = {app_label: position for position, app_label in enumerate(STAFF_MENU_MODELS)}


def staff_permission_queryset():
    allowed = Q()
    for app_label, model_names in STAFF_MENU_MODELS.items():
        allowed |= Q(content_type__app_label=app_label, content_type__model__in=model_names)
    return Permission.objects.filter(allowed).exclude(
        content_type__app_label="content_management",
        content_type__model="newslettersubscription",
        codename="add_newslettersubscription",
    )


class ExportMixin:
    actions = ("export_csv", "export_excel")
    export_fields = None

    def get_export_fields(self):
        return self.export_fields or [field.name for field in self.model._meta.fields]

    @admin.action(description="Export selected rows as CSV")
    def export_csv(self, request, queryset):
        fields = self.get_export_fields()
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="{self.model._meta.model_name}.csv"'
        writer = csv.writer(response)
        writer.writerow(fields)
        for obj in queryset.iterator():
            writer.writerow([getattr(obj, field, "") for field in fields])
        return response

    @admin.action(description="Export selected rows as Excel")
    def export_excel(self, request, queryset):
        fields = self.get_export_fields()
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = self.model._meta.verbose_name_plural.title()[:31]
        sheet.append(fields)
        for obj in queryset.iterator():
            sheet.append([str(getattr(obj, field, "") or "") for field in fields])
        stream = BytesIO()
        workbook.save(stream)
        response = HttpResponse(stream.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        response["Content-Disposition"] = f'attachment; filename="{self.model._meta.model_name}.xlsx"'
        return response


class BaseAdmin(ExportMixin, ModelAdmin):
    list_per_page = 40
    save_on_top = True
    hide_from_index = False
    formfield_overrides = {
        django_models.TextField: {"widget": WysiwygWidget},
    }

    def get_model_perms(self, request):
        if self.hide_from_index:
            return {}
        return super().get_model_perms(request)

    def get_list_display(self, request):
        columns = list(super().get_list_display(request))
        if self.has_change_permission(request) and "edit_row_action" not in columns:
            columns.append("edit_row_action")
        if self.has_delete_permission(request) and "delete_row_action" not in columns:
            columns.append("delete_row_action")
        return columns

    @admin.display(description="Edit")
    def edit_row_action(self, obj):
        change_url = reverse(
            f"admin:{obj._meta.app_label}_{obj._meta.model_name}_change",
            args=(obj.pk,),
        )
        return format_html(
            '<a class="inline-flex items-center rounded-default bg-primary-600 px-3 py-1.5 font-medium text-white hover:bg-primary-700" href="{}">Edit</a>',
            change_url,
        )

    @admin.display(description="Delete")
    def delete_row_action(self, obj):
        delete_url = reverse(
            f"admin:{obj._meta.app_label}_{obj._meta.model_name}_delete",
            args=(obj.pk,),
        )
        return format_html(
            '<a class="inline-flex items-center rounded-default bg-red-600 px-3 py-1.5 font-medium text-white hover:bg-red-700" href="{}">Delete</a>',
            delete_url,
        )


class ProductImageInline(TabularInline):
    model = m.ProductImage
    extra = 1


class ProductVariantInline(TabularInline):
    model = m.ProductVariant
    extra = 1


@admin.register(m.Category)
class CategoryAdmin(BaseAdmin):
    list_display = ("drag_handle", "name", "parent", "audience", "display_order", "is_active", "updated_at")
    list_filter = ("audience", "is_active", "parent")
    search_fields = ("name", "slug", "description")
    prepopulated_fields = {"slug": ("name",)}
    list_editable = ("is_active",)
    readonly_fields = ("display_order",)

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

    def get_urls(self):
        custom_urls = [
            path("reorder/", self.admin_site.admin_view(self.reorder_view), name="products_category_reorder"),
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


@admin.register(m.Product)
class ProductAdmin(BaseAdmin):
    actions_list = ("import_products", "download_product_sample")
    list_display = ("name", "category", "regular_price", "sale_price", "stock", "status", "is_featured", "is_trending")
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
                    summary = f"Processed {counts['rows']} rows: {counts['products']} products, {counts['variants']} variants and {counts['images']} images created; {counts['failed']} rows failed."
                    self.message_user(request, summary, level)
                    for error in errors[:10]:
                        self.message_user(request, error, messages.ERROR)
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
        instructions.append(("One row per variant", "Repeat the product slug for additional size/color/SKU variants."))
        instructions.append(("image_url", "For third-party images: enter the complete public HTTP/HTTPS URL. It is downloaded, validated and saved to local media storage."))
        instructions.append(("image_path", "Leave blank when image_url is used. Only use this for a file that already exists under MEDIA_ROOT."))
        instructions.append(("status", "draft, active or archived"))
        instructions.append(("category_audience", "women, men, unisex or kids"))
        instructions.append(("boolean fields", "Use true/false, yes/no or 1/0."))
        stream = BytesIO()
        workbook.save(stream)
        response = HttpResponse(stream.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        response["Content-Disposition"] = 'attachment; filename="fabriqx-product-import-sample.xlsx"'
        return response

    @admin.display(description="Stock", ordering="variants__stock_quantity")
    def stock(self, obj):
        return obj.total_stock


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
    readonly_fields = ("stock_before", "stock_after", "created_by", "created_at", "updated_at")

    def has_change_permission(self, request, obj=None):
        return False if obj else super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return False

    @transaction.atomic
    def save_model(self, request, obj, form, change):
        variant = m.ProductVariant.objects.select_for_update().get(pk=obj.variant_id)
        after = variant.stock_quantity + obj.quantity
        if after < 0:
            raise ValueError("Stock adjustment would make inventory negative.")
        obj.stock_before, obj.stock_after, obj.created_by = variant.stock_quantity, after, request.user
        variant.stock_quantity = after
        variant.save(update_fields=("stock_quantity", "updated_at"))
        super().save_model(request, obj, form, change)


class AddressInline(TabularInline):
    model = m.Address
    extra = 0


class WishlistInline(TabularInline):
    model = m.WishlistItem
    extra = 0
    autocomplete_fields = ("product", "variant")


@admin.register(m.CustomerProfile)
class CustomerAdmin(BaseAdmin):
    list_display = ("user", "phone", "is_active", "marketing_consent", "order_count", "created_at")
    list_filter = ("is_active", "marketing_consent", "created_at")
    search_fields = ("user__username", "user__first_name", "user__last_name", "user__email", "phone")
    autocomplete_fields = ("user",)
    inlines = (AddressInline, WishlistInline)
    actions = ExportMixin.actions + ("send_password_reset",)

    def has_add_permission(self, request):
        return False

    @admin.action(description="Email secure password reset link", permissions=("change",))
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

    @admin.display(description="Orders")
    def order_count(self, obj):
        return obj.orders.count()


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

    @admin.display(boolean=True, description="Admin access")
    def staff_access(self, obj):
        return obj.role == m.UserRole.Role.ADMIN or obj.user.is_superuser


class UserRoleInline(StackedInline):
    model = m.UserRole
    can_delete = False
    extra = 0
    max_num = 1
    fields = ("role",)


class FabriqxUserAdmin(BaseUserAdmin, ModelAdmin):
    permission_queryset = staff_permission_queryset()

    class AdminAccountCreationForm(BaseUserAdmin.add_form):
        email = forms.EmailField(
            required=True,
            widget=forms.EmailInput(attrs={"class": " ".join(INPUT_CLASSES)}),
        )
        role = forms.ChoiceField(
            label="Account type",
            choices=((m.UserRole.Role.ADMIN, "Admin / Staff"),),
            initial=m.UserRole.Role.ADMIN,
            disabled=True,
            help_text="Customer and influencer accounts must be created from their dedicated flows.",
        )
        permissions = forms.ModelMultipleChoiceField(
            queryset=staff_permission_queryset(),
            required=False,
        )

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            password_input_classes = " ".join(INPUT_CLASSES)
            for field_name in ("password1", "password2"):
                self.fields[field_name].widget.attrs["class"] = password_input_classes
            self.fields["permissions"].widget = UserRoleAdmin.PermissionMatrixWidget(self.fields["permissions"].queryset)

    class UserWithRoleForm(BaseUserAdmin.form):
        role = forms.ChoiceField(choices=((m.UserRole.Role.ADMIN, "Admin / Staff"),), disabled=True)
        permissions = forms.ModelMultipleChoiceField(
            queryset=staff_permission_queryset(),
            required=False,
        )

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.fields["permissions"].widget = UserRoleAdmin.PermissionMatrixWidget(self.fields["permissions"].queryset)
            self.fields["is_superuser"].disabled = True
            self.fields["is_superuser"].help_text = "The site has one protected Super Admin account. This status cannot be changed here."
            try:
                user_role = self.instance.fabriqx_role
                role = user_role.role
            except m.UserRole.DoesNotExist:
                role = m.UserRole.Role.ADMIN
            self.initial["role"] = role
            self.initial["permissions"] = list(self.instance.user_permissions.values_list("pk", flat=True))

    form = UserWithRoleForm
    add_form = AdminAccountCreationForm
    list_display = (
        "username",
        "email",
        "first_name",
        "last_name",
        "role_display",
        "is_staff",
        "edit_account",
        "delete_account",
    )
    list_filter = BaseUserAdmin.list_filter + ("fabriqx_role__role",)
    inlines = ()
    fieldsets = (
        (None, {"fields": ("username", "password")}),
        ("Personal info", {"fields": ("first_name", "last_name", "email")}),
        ("Account status", {"fields": ("is_active", "is_staff", "is_superuser")}),
        ("Role and permissions", {"fields": ("role", "permissions")}),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (None, {"fields": ("username", "email", "password1", "password2")}),
        ("Role and access permissions", {"fields": ("role", "permissions")}),
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

    @admin.display(description="Edit")
    def edit_account(self, obj):
        change_url = reverse("admin:auth_user_change", args=(obj.pk,))
        return format_html(
            '<a class="inline-flex items-center rounded-default bg-primary-600 px-3 py-1.5 font-medium text-white hover:bg-primary-700" href="{}">Edit</a>',
            change_url,
        )

    @admin.display(description="Delete")
    def delete_account(self, obj):
        if obj.is_superuser:
            return format_html('<span class="text-base-400">{}</span>', "Protected")
        delete_url = reverse("admin:auth_user_delete", args=(obj.pk,))
        return format_html(
            '<a class="inline-flex items-center rounded-default bg-red-600 px-3 py-1.5 font-medium text-white hover:bg-red-700" href="{}">Delete</a>',
            delete_url,
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
        role_name = m.UserRole.Role.ADMIN if not change else form.cleaned_data.get("role")
        if role_name:
            user_role, _ = m.UserRole.objects.update_or_create(user=obj, defaults={"role": role_name})
            obj.user_permissions.set(form.cleaned_data.get("permissions", Permission.objects.none()))

        if not change and obj.email:
            recipient = obj.email
            display_name = obj.get_full_name() or obj.get_username()
            username = obj.get_username()
            login_url = request.build_absolute_uri(reverse("admin:login"))
            password_change_url = request.build_absolute_uri(reverse("admin:password_change"))

            def send_access_email():
                context = {
                    "display_name": display_name,
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

    @admin.display(description="Role", ordering="fabriqx_role__role")
    def role_display(self, obj):
        if obj.is_superuser:
            return "Super Admin"
        try:
            return obj.fabriqx_role.get_role_display()
        except m.UserRole.DoesNotExist:
            return "Admin" if obj.is_superuser else "Customer"


@admin.register(m.Coupon)
class CouponAdmin(BaseAdmin):
    list_display = ("code", "discount_type", "discount_value", "starts_at", "expires_at", "usage_count", "is_active")
    list_filter = ("discount_type", "is_active", "starts_at", "expires_at")
    search_fields = ("code", "description")
    filter_horizontal = ("categories", "products", "influencers")
    list_editable = ("is_active",)

    @admin.display(description="Uses")
    def usage_count(self, obj):
        return obj.usages.count()


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

    @admin.display(description="Payment")
    def payment_state(self, obj):
        payment = obj.payments.order_by("-created_at").first()
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
        password = forms.CharField(widget=forms.PasswordInput(attrs={**input_attrs, "autocomplete": "new-password"}), required=False, help_text="Required when creating an influencer. Leave blank when editing to keep the current password.")
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
            address = self.instance.address if self.instance and isinstance(self.instance.address, dict) else {}
            for form_name, json_name in self.address_fields.items():
                self.fields[form_name].initial = address.get(json_name, "")
            if self.instance and self.instance.pk:
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
            users = get_user_model().objects.filter(email__iexact=email)
            if self.instance and self.instance.pk:
                users = users.exclude(pk=self.instance.user_id)
            if users.exists():
                raise forms.ValidationError("An account with this email address already exists.")
            return email

        def clean(self):
            cleaned = super().clean()
            password = cleaned.get("password")
            if not self.instance.pk and not password:
                self.add_error("password", "A password is required for a new influencer.")
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
    list_display = ("affiliate_id", "user", "referred_orders", "is_active")
    list_filter = ("is_active", "created_at")
    search_fields = ("affiliate_id", "user__username", "user__first_name", "user__last_name", "user__email")
    readonly_fields = ("affiliate_id", "created_at", "updated_at")
    fieldsets = (
        ("Account credentials", {"fields": ("username", "first_name", "last_name", "email", "password", "confirm_password")}),
        ("Influencer profile", {"fields": ("affiliate_id", "phone", "profile_image", "is_active")}),
        ("Address", {"fields": ("address_line_1", "address_line_2", "address_city", "address_state", "address_postal_code", "address_country")}),
        ("Audit", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )

    @admin.display(description="Orders")
    def referred_orders(self, obj): return obj.orders.count()

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

        def send_onboarding_email():
            context = {
                "display_name": display_name,
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


@admin.register(m.Review)
class ReviewAdmin(BaseAdmin):
    list_display = ("product", "customer", "rating", "is_verified_purchase", "status", "created_at")
    list_filter = ("status", "rating", "is_verified_purchase", "created_at")
    search_fields = ("product__name", "customer__user__username", "title", "body")
    autocomplete_fields = ("product", "customer", "order_item")
    actions = ExportMixin.actions + ("approve", "reject")

    @admin.action(description="Approve selected reviews")
    def approve(self, request, queryset): queryset.update(status=m.Review.Status.APPROVED)
    @admin.action(description="Reject selected reviews")
    def reject(self, request, queryset): queryset.update(status=m.Review.Status.REJECTED)


@admin.register(m.ProductQuestion)
class ProductQuestionAdmin(BaseAdmin):
    hide_from_index = True
    list_display = ("product", "customer", "is_published", "answered_at", "created_at")
    list_filter = ("is_published", "answered_at", "created_at")
    search_fields = ("product__name", "customer__user__username", "question", "answer")
    autocomplete_fields = ("product", "customer")
    readonly_fields = ("created_at", "updated_at")


class SimpleAdmin(BaseAdmin):
    list_display = ("__str__",)
    search_fields = ("id",)


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


@admin.register(m.Banner)
class BannerAdmin(BaseAdmin):
    list_display = ("title", "display_order", "starts_at", "ends_at", "is_active")
    list_filter = ("is_active", "starts_at", "ends_at")
    search_fields = ("title", "subtitle")
    list_editable = ("display_order", "is_active")


@admin.register(m.HomepageSection)
class SectionAdmin(BaseAdmin):
    list_display = ("title", "section_type", "display_order", "is_active", "updated_at")
    list_filter = ("section_type", "is_active")
    list_editable = ("display_order", "is_active")
    fieldsets = (
        (None, {"fields": ("title", "section_type", "editor_content", "display_order", "is_active")}),
    )


class GiftSectionFeatureInline(TabularInline):
    model = m.GiftSectionFeature
    extra = 5
    max_num = 5
    fields = ("icon", "text", "display_order", "is_active")

    def get_formset(self, request, obj=None, **kwargs):
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
    form = GiftSectionStatisticForm
    extra = 3
    max_num = 3
    fields = ("icon", "eyebrow", "value", "label", "display_order", "is_active")

    def get_formset(self, request, obj=None, **kwargs):
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
    list_display = ("internal_name", "heading", "display_order", "starts_at", "ends_at", "is_active")
    list_filter = ("is_active", "starts_at", "ends_at")
    list_editable = ("display_order", "is_active")
    search_fields = ("internal_name", "heading", "description")
    inlines = (GiftSectionFeatureInline, GiftSectionStatisticInline)
    fieldsets = (
        ("Campaign", {"fields": ("internal_name", "display_order", "starts_at", "ends_at", "is_active")}),
        ("Offer badge", {"fields": ("badge_eyebrow", "badge_title", "badge_icon")}),
        ("Main content", {"fields": ("logo", "accent_heading", "heading", "description", "main_image", "gift_image", "background_image")}),
        ("Thank-you message", {"fields": ("thank_you_title", "thank_you_text")}),
        ("Call to action", {"fields": ("cta_label", "cta_url")}),
    )

    def has_add_permission(self, request):
        return super().has_add_permission(request) and not m.GiftSection.objects.exists()

    def render_change_form(self, request, context, add=False, change=False, form_url="", obj=None):
        context["show_save_and_add_another"] = False
        return super().render_change_form(request, context, add, change, form_url, obj)


class BrandLogoInline(TabularInline):
    model = m.BrandLogo
    extra = 6
    max_num = 6
    fields = ("logo", "brand_name", "alt_text", "url", "open_in_new_tab", "display_order", "is_active")

    def get_formset(self, request, obj=None, **kwargs):
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
    list_display = ("internal_name", "heading", "is_active", "updated_at")
    inlines = (BrandLogoInline,)
    fieldsets = (("Section settings", {"fields": ("internal_name", "heading", "background_color", "is_active")}),)

    def has_add_permission(self, request):
        return super().has_add_permission(request) and not m.BrandLogoSection.objects.exists()

    def render_change_form(self, request, context, add=False, change=False, form_url="", obj=None):
        context["show_save_and_add_another"] = False
        return super().render_change_form(request, context, add, change, form_url, obj)


@admin.register(m.OfferBanner)
class OfferBannerAdmin(BaseAdmin):
    list_display = ("internal_name", "display_order", "starts_at", "ends_at", "is_active")
    list_editable = ("display_order", "is_active")
    list_filter = ("is_active", "starts_at", "ends_at")
    search_fields = ("internal_name", "alt_text", "shop_now_url")
    fieldsets = (
        ("Banner", {"fields": ("internal_name", "desktop_image", "mobile_image", "alt_text")}),
        ("Shop now link", {"fields": ("shop_now_url", "open_in_new_tab")}),
        ("Display", {"fields": ("display_order", "starts_at", "ends_at", "is_active")}),
    )


class OfferGridItemInline(TabularInline):
    model = m.OfferGridItem
    extra = 3
    max_num = 3
    fields = ("desktop_image", "mobile_image", "internal_name", "alt_text", "shop_now_url", "open_in_new_tab", "display_order", "is_active")

    def get_formset(self, request, obj=None, **kwargs):
        kwargs["validate_max"] = True
        return super().get_formset(request, obj, **kwargs)

    def has_add_permission(self, request, obj=None):
        return request.user.has_perm("content_management.change_offergridsection") or request.user.has_perm("fabriqx.change_offergridsection")

    def has_change_permission(self, request, obj=None):
        return self.has_add_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return self.has_add_permission(request, obj)


@admin.register(m.OfferGridSection)
class OfferGridSectionAdmin(BaseAdmin):
    list_display = ("internal_name", "is_active", "updated_at")
    inlines = (OfferGridItemInline,)
    fields = ("internal_name", "is_active")

    def has_add_permission(self, request):
        return super().has_add_permission(request) and not m.OfferGridSection.objects.exists()

    def render_change_form(self, request, context, add=False, change=False, form_url="", obj=None):
        context["show_save_and_add_another"] = False
        return super().render_change_form(request, context, add, change, form_url, obj)


class FooterSocialLinkInline(TabularInline):
    model = m.FooterSocialLink
    extra = 4
    max_num = 4
    fields = ("icon", "platform_name", "url", "aria_label", "display_order", "is_active")

    def get_formset(self, request, obj=None, **kwargs):
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
    fields = ("internal_name", "heading", "is_active")

    def has_add_permission(self, request):
        return super().has_add_permission(request) and not m.FooterSocialSection.objects.exists()

    def render_change_form(self, request, context, add=False, change=False, form_url="", obj=None):
        context["show_save_and_add_another"] = False
        return super().render_change_form(request, context, add, change, form_url, obj)


@admin.register(m.Testimonial)
class TestimonialAdmin(BaseAdmin):
    list_display = ("customer_name", "rating", "is_active")
    list_filter = ("rating", "is_active")
    search_fields = ("customer_name", "quote")
    list_editable = ("is_active",)


@admin.register(m.NewsletterSubscription)
class NewsletterAdmin(BaseAdmin):
    list_display = ("email", "source", "is_active", "created_at")
    list_filter = ("is_active", "source", "created_at")
    search_fields = ("email",)

    def has_add_permission(self, request):
        return False


@admin.register(m.NewsletterSettings)
class NewsletterSettingsAdmin(BaseAdmin):
    list_display = ("notification_email", "notifications_enabled", "updated_at")
    fields = ("notification_email", "notifications_enabled")

    def has_add_permission(self, request):
        return super().has_add_permission(request) and not m.NewsletterSettings.objects.exists()

    def render_change_form(self, request, context, add=False, change=False, form_url="", obj=None):
        context["show_save_and_add_another"] = False
        return super().render_change_form(request, context, add, change, form_url, obj)


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
    fields = ("commission_type", "commission_rate", "commission_fixed_amount")
    conditional_fields = {
        "commission_rate": "commission_type == 'percentage'",
        "commission_fixed_amount": "commission_type == 'fixed'",
    }

    def has_add_permission(self, request):
        return super().has_add_permission(request) and not m.SiteSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        settings_object = m.SiteSettings.load()
        return redirect("admin:fabriqx_sitesettings_change", settings_object.pk)

    def response_change(self, request, obj):
        return redirect("admin:fabriqx_sitesettings_change", obj.pk)

    def render_change_form(self, request, context, add=False, change=False, form_url="", obj=None):
        context["show_save_and_add_another"] = False
        context["show_delete"] = False
        return super().render_change_form(request, context, add, change, form_url, obj)


@admin.register(m.Page)
class PageAdmin(BaseAdmin):
    list_display = ("title", "slug", "is_active", "updated_at")
    list_filter = ("is_active", "updated_at")
    search_fields = ("title", "slug", "content", "seo_title")
    prepopulated_fields = {"slug": ("title",)}
    list_editable = ("is_active",)
    fieldsets = (
        ("Page", {"fields": ("title", "slug", "content", "is_active")}),
        ("SEO", {"fields": ("seo_title", "seo_description")}),
        ("Audit", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )
    readonly_fields = ("created_at", "updated_at")


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


@admin.register(m.SalesReport)
class SalesReportAdmin(ReadOnlyReportAdmin):
    list_display = ("number", "placed_at", "status", "email", "subtotal", "discount_total", "tax_total", "grand_total", "influencer")
    list_filter = ("status", "placed_at", "influencer")
    search_fields = ("number", "email", "phone")
    date_hierarchy = "placed_at"
    export_fields = ("number", "placed_at", "status", "email", "subtotal", "discount_total", "shipping_total", "tax_total", "grand_total")


@admin.register(m.InventoryReport)
class InventoryReportAdmin(ReadOnlyReportAdmin):
    list_display = ("sku", "product", "size", "color", "stock_quantity", "low_stock_threshold", "stock_status", "is_active")
    list_filter = ("is_active", "product__category", "size", "color")
    search_fields = ("sku", "product__name")
    export_fields = ("sku", "product", "size", "color", "stock_quantity", "low_stock_threshold", "is_active")


@admin.register(m.InfluencerReport)
class InfluencerReportAdmin(ReadOnlyReportAdmin):
    list_display = ("influencer", "order", "eligible_amount", "rate", "commission_amount", "status", "paid_at")
    list_filter = ("status", "created_at", "paid_at", "influencer")
    search_fields = ("influencer__affiliate_id", "order__number", "payment_reference")
    export_fields = ("influencer", "order", "eligible_amount", "rate", "commission_amount", "status", "payment_reference", "paid_at")


admin.site.index_template = "fabriqx/admin_dashboard.html"
UnfoldAdminSite.index_template = "fabriqx/admin_dashboard.html"

if admin.site.is_registered(Group):
    admin.site.unregister(Group)
