from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView

from . import api_views as views


router = DefaultRouter()
router.register("products", views.ProductViewSet, basename="product")
router.register("account/addresses", views.AddressViewSet, basename="address")
router.register("account/wishlist", views.WishlistViewSet, basename="wishlist")
router.register("account/orders", views.OrderViewSet, basename="order")

urlpatterns = [
    path("", include(router.urls)),
    path("auth/register/", views.RegisterView.as_view(), name="api-register"),
    path("auth/login/", views.LoginView.as_view(), name="api-login"),
    path("auth/logout/", views.LogoutView.as_view(), name="api-logout"),
    path("auth/token/refresh/", TokenRefreshView.as_view(), name="api-token-refresh"),
    path("auth/forgot-password/", views.ForgotPasswordView.as_view(), name="api-forgot-password"),
    path("auth/reset-password/", views.ResetPasswordView.as_view(), name="api-reset-password"),
    path("homepage/", views.HomepageView.as_view(), name="api-homepage"),
    path("navigation/", views.NavigationView.as_view(), name="api-navigation"),
    path("newsletter/", views.NewsletterView.as_view(), name="api-newsletter"),
    path("pages/<slug:slug>/", views.PageDetailView.as_view(), name="api-page-detail"),
    path("account/profile/", views.ProfileView.as_view(), name="api-profile"),
    path("cart/", views.CartView.as_view(), name="api-cart"),
    path("cart/items/<int:pk>/", views.CartItemView.as_view(), name="api-cart-item"),
    path("cart/coupon/", views.CartCouponView.as_view(), name="api-cart-coupon"),
    path("checkout/affiliate/validate/", views.AffiliateValidationView.as_view(), name="api-affiliate-validate"),
    path("checkout/", views.CheckoutView.as_view(), name="api-checkout"),
    path("payments/marketplace/webhook/", views.MarketplaceWebhookView.as_view(), name="api-marketplace-webhook"),
    path("reviews/", views.ReviewCreateView.as_view(), name="api-review-create"),
    path("influencer/profile/", views.InfluencerProfileView.as_view(), name="api-influencer-profile"),
    path("influencer/dashboard/", views.InfluencerDashboardView.as_view(), name="api-influencer-dashboard"),
    path("influencer/orders/", views.InfluencerOrdersView.as_view(), name="api-influencer-orders"),
    path("influencer/orders/<int:pk>/", views.InfluencerOrderDetailView.as_view(), name="api-influencer-order-detail"),
    path("influencer/sales/", views.InfluencerSalesView.as_view(), name="api-influencer-sales"),
    path("influencer/commissions/", views.InfluencerCommissionListView.as_view(), name="api-influencer-commissions"),
    path("admin/reports/sales/", views.SalesAggregationView.as_view(), name="api-admin-sales-report"),
]
