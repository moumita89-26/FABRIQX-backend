from dataclasses import dataclass

from django.conf import settings

from .models import IntegrationEvent


class IntegrationNotConfigured(RuntimeError):
    pass


@dataclass
class ProviderResult:
    external_id: str
    payload: dict


class ProviderService:
    provider = "provider"
    required_settings = ()

    def ensure_configured(self):
        missing = [name for name in self.required_settings if not getattr(settings, name, "")]
        if missing:
            raise IntegrationNotConfigured(f"Missing settings: {', '.join(missing)}")

    def record(self, event_type, direction, payload, response=None, succeeded=False, external_id="", error=""):
        return IntegrationEvent.objects.create(
            provider=self.provider,
            event_type=event_type,
            direction=direction,
            external_id=external_id,
            payload=payload,
            response=response or {},
            succeeded=succeeded,
            error_message=error,
        )


class RazorpayService(ProviderService):
    provider = "Razorpay"
    required_settings = ("RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET")


class ShiprocketService(ProviderService):
    provider = "Shiprocket"
    required_settings = ("SHIPROCKET_EMAIL", "SHIPROCKET_PASSWORD")
