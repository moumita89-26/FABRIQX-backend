from http import HTTPStatus

from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler


def _first_error(value):
    """Return a useful, human-readable message from any DRF error shape."""
    if isinstance(value, dict):
        for item in value.values():
            message = _first_error(item)
            if message:
                return message
    elif isinstance(value, (list, tuple)):
        for item in value:
            message = _first_error(item)
            if message:
                return message
    elif value not in (None, ""):
        return str(value)
    return None


def api_exception_handler(exc, context):
    """Keep every API exception inside the public error response contract."""
    response = drf_exception_handler(exc, context)
    if response is not None:
        return response

    # Do not expose exception details to clients.
    return Response(
        {"detail": "An unexpected error occurred."},
        status=500,
    )


class UniformJSONRenderer(JSONRenderer):
    """Render v1 API responses using one stable success/error envelope."""

    def render(self, data, accepted_media_type=None, renderer_context=None):
        context = renderer_context or {}
        request = context.get("request")
        response = context.get("response")

        # Schema and other DRF-powered non-API views must retain their formats.
        if response is None or request is None or not request.path.startswith("/api/v1/"):
            return super().render(data, accepted_media_type, renderer_context)

        status_code = response.status_code
        if isinstance(data, dict) and {"success", "message", "status_code"}.issubset(data):
            envelope = data
        elif status_code >= 400:
            envelope = {
                "success": False,
                "message": _first_error(data) or HTTPStatus(status_code).phrase,
                "status_code": status_code,
            }
        else:
            message = None
            payload = data
            if isinstance(data, dict) and "detail" in data:
                message = str(data["detail"])
                payload = {key: value for key, value in data.items() if key != "detail"} or None

            envelope = {
                "success": True,
                "message": message or self._success_message(status_code),
                "data": payload,
                "status_code": status_code,
            }

        return super().render(envelope, accepted_media_type, renderer_context)

    @staticmethod
    def _success_message(status_code):
        if status_code == 201:
            return "Created successfully."
        return "Request successful."
