"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.contrib.auth.models import Group
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from django.contrib.auth import get_user_model
from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path, reverse_lazy
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from types import MethodType

if admin.site.is_registered(Group):
    admin.site.unregister(Group)

for token_model in (BlacklistedToken, OutstandingToken):
    if admin.site.is_registered(token_model):
        admin.site.unregister(token_model)

from fabriqx.admin import FabriqxUserAdmin
from fabriqx.auth_forms import AdminPasswordResetForm

User = get_user_model()
# This User registration is exclusively the admin/staff account manager.
# The admin action label is derived from the model's singular verbose name.
User._meta.verbose_name = "Admin / Staff"
if admin.site.is_registered(User):
    admin.site.unregister(User)
admin.site.register(User, FabriqxUserAdmin)

_original_get_app_list = admin.site.get_app_list


def _group_roles_under_authentication(self, request, app_label=None):
    lookup_label = None if app_label in {"auth", "fabriqx"} else app_label
    app_list = _original_get_app_list(request, lookup_label)
    auth_app = next((app for app in app_list if app["app_label"] == "auth"), None)
    fabriqx_app = next((app for app in app_list if app["app_label"] == "fabriqx"), None)

    if auth_app and fabriqx_app:
        role_models = [model for model in fabriqx_app["models"] if model["object_name"] == "UserRole"]
        fabriqx_app["models"] = [model for model in fabriqx_app["models"] if model["object_name"] != "UserRole"]
        for model in role_models:
            model["name"] = "Roles"
        auth_app["models"].extend(role_models)
        auth_app["models"].sort(key=lambda model: model["name"])

    if auth_app:
        for model in auth_app["models"]:
            if model["object_name"] == "User":
                model["name"] = "Admin & Staff Accounts"

    # The core app contains several business domains. Present them as focused
    # navigation groups without changing their models, database tables or URLs.
    if fabriqx_app:
        # Temporarily keep these business areas out of the admin navigation.
        # Their models remain registered, so this can be reversed without any
        # database or admin-class changes.
        hidden_navigation_models = {
            "Order",
            "OrderItem",
            "OrderStatusHistory",
            "Shipment",
            "SalesReport",
            "Payment",
            "Refund",
            "Invoice",
            "Coupon",
            "CouponUsage",
            "Review",
        }
        fabriqx_app["models"] = [
            model
            for model in fabriqx_app["models"]
            if model["object_name"] not in hidden_navigation_models
        ]

        section_definitions = (
            ("operations", "Operations & Integrations", {"ServiceablePincode", "IntegrationEvent"}),
        )
        grouped_names = set()
        organized_apps = []
        for section_label, section_name, object_names in section_definitions:
            section_models = [model for model in fabriqx_app["models"] if model["object_name"] in object_names]
            if not section_models:
                continue
            grouped_names.update(model["object_name"] for model in section_models)
            section = fabriqx_app.copy()
            section["app_label"] = section_label
            section["name"] = section_name
            section["models"] = section_models
            section["app_url"] = section_models[0].get("admin_url", fabriqx_app["app_url"])
            organized_apps.append(section)

        remaining_models = [model for model in fabriqx_app["models"] if model["object_name"] not in grouped_names]
        app_list.remove(fabriqx_app)
        if remaining_models:
            fabriqx_app["name"] = "Platform Administration"
            fabriqx_app["models"] = remaining_models
            organized_apps.append(fabriqx_app)
        app_list.extend(organized_apps)

    app_order = {
        "auth": 0,
        "content_management": 1,
        "customers": 2,
        "influencers": 3,
        "products": 4,
        "orders": 5,
        "finance": 6,
        "marketing": 7,
        "operations": 8,
        "fabriqx": 9,
    }
    app_list.sort(key=lambda app: (app_order.get(app["app_label"], 99), app["name"].lower()))

    influencers_app = next((app for app in app_list if app["app_label"] == "influencers"), None)
    if influencers_app:
        influencer_order = {"InfluencerProfile": 0, "InfluencerReport": 1}
        influencers_app["models"].sort(key=lambda model: (influencer_order.get(model["object_name"], 99), model["name"]))

    customers_app = next((app for app in app_list if app["app_label"] == "customers"), None)
    if customers_app:
        customer_order = {"CustomerProfile": 0, "WishlistItem": 1}
        customers_app["models"].sort(key=lambda model: (customer_order.get(model["object_name"], 99), model["name"]))

    products_app = next((app for app in app_list if app["app_label"] == "products"), None)
    if products_app:
        product_order = {
            "Product": 0,
            "Category": 1,
            "ProductVariant": 2,
            "ProductImage": 3,
            "InventoryMovement": 4,
            "InventoryReport": 5,
            "Coupon": 6,
        }
        products_app["models"].sort(
            key=lambda model: (product_order.get(model["object_name"], 99), model["name"])
        )

    content_app = next((app for app in app_list if app["app_label"] == "content_management"), None)
    if content_app:
        content_app["models"] = [model for model in content_app["models"] if model["object_name"] != "HomepageSection"]
        content_order = {
            "Banner": 0,
            "BrandLogoSection": 1,
            "GiftSection": 2,
            "OfferBanner": 3,
            "OfferGridSection": 4,
            "Testimonial": 5,
            "FooterSocialSection": 6,
            "Page": 7,
            "NewsletterSettings": 8,
            "NewsletterSubscription": 9,
        }
        content_app["models"].sort(
            key=lambda model: (content_order.get(model["object_name"], 99), model["name"])
        )

    if app_label == "fabriqx":
        return [app for app in app_list if app["app_label"] in {"orders", "finance", "marketing", "operations", "fabriqx"}]
    if app_label:
        return [app for app in app_list if app["app_label"] == app_label]
    return app_list


admin.site.get_app_list = MethodType(_group_roles_under_authentication, admin.site)

urlpatterns = [
    path(
        'admin/password-reset/',
        auth_views.PasswordResetView.as_view(
            form_class=AdminPasswordResetForm,
            template_name='registration/password_reset_form.html',
            email_template_name='registration/password_reset_email.html',
            subject_template_name='registration/password_reset_subject.txt',
            success_url=reverse_lazy('password_reset_done'),
        ),
        name='admin_password_reset',
    ),
    path(
        'admin/password-reset/done/',
        auth_views.PasswordResetDoneView.as_view(template_name='registration/password_reset_done.html'),
        name='password_reset_done',
    ),
    path(
        'admin/reset/<uidb64>/<token>/',
        auth_views.PasswordResetConfirmView.as_view(
            template_name='registration/password_reset_confirm.html',
            success_url=reverse_lazy('password_reset_complete'),
        ),
        name='password_reset_confirm',
    ),
    path(
        'admin/reset/complete/',
        auth_views.PasswordResetCompleteView.as_view(template_name='registration/password_reset_complete.html'),
        name='password_reset_complete',
    ),
    path('admin/', admin.site.urls),
    path('api/schema/', SpectacularAPIView.as_view(), name='api-schema'),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='api-schema'), name='api-docs'),
    path('api/v1/', include('fabriqx.api_urls')),
    path('', include('fabriqx.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
