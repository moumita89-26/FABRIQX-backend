from contextvars import ContextVar

from django.conf import settings
from django.http import HttpResponse
from django.utils.cache import patch_vary_headers


current_actor = ContextVar("fabriqx_current_actor", default=None)


class CorsMiddleware:
    """Add CORS headers only for explicitly configured storefront origins."""

    allowed_methods = "DELETE, GET, OPTIONS, PATCH, POST, PUT"
    allowed_headers = "Accept, Authorization, Content-Type, Origin, X-CSRFToken"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        origin = request.headers.get("Origin", "").rstrip("/")
        is_allowed = origin in settings.CORS_ALLOWED_ORIGINS
        is_preflight = request.method == "OPTIONS" and request.headers.get("Access-Control-Request-Method")
        response = HttpResponse(status=204) if is_allowed and is_preflight else self.get_response(request)
        if is_allowed:
            response["Access-Control-Allow-Origin"] = origin
            response["Access-Control-Allow-Methods"] = self.allowed_methods
            response["Access-Control-Allow-Headers"] = self.allowed_headers
            response["Access-Control-Max-Age"] = "86400"
            patch_vary_headers(response, ("Origin",))
        return response


class AuditActorMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        actor = request.user if getattr(request, "user", None) and request.user.is_authenticated else None
        token = current_actor.set(actor)
        try:
            return self.get_response(request)
        finally:
            current_actor.reset(token)
