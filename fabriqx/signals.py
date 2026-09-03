from django.core.exceptions import ValidationError
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from .middleware import current_actor
from .models import AuditLog, InfluencerCommission, InfluencerProfile, Order, UserRole


@receiver(pre_save, sender="auth.User")
def enforce_single_super_admin(sender, instance, raw=False, **kwargs):
    if raw:
        return

    other_super_admins = sender._default_manager.filter(is_superuser=True).exclude(pk=instance.pk)
    if instance.is_superuser and other_super_admins.exists():
        raise ValidationError("FABRIQX supports only one Super Admin account.")

    if instance.pk:
        was_super_admin = sender._default_manager.filter(pk=instance.pk, is_superuser=True).exists()
        if was_super_admin and not instance.is_superuser:
            raise ValidationError("The protected Super Admin status cannot be removed.")


def should_audit(instance):
    return instance._meta.app_label in {"fabriqx", "products", "influencers", "content_management", "customers"} and not isinstance(instance, AuditLog)


@receiver(post_save)
def audit_save(sender, instance, created, raw=False, **kwargs):
    if raw or not should_audit(instance):
        return
    if isinstance(instance, InfluencerProfile) and not instance.user.is_superuser:
        role, _ = UserRole.objects.get_or_create(user=instance.user)
        if role.role != UserRole.Role.INFLUENCER:
            role.role = UserRole.Role.INFLUENCER
            role.save()
    AuditLog.objects.create(
        actor=current_actor.get(),
        action="created" if created else "updated",
        model_name=instance._meta.label,
        object_id=str(instance.pk),
        object_repr=str(instance)[:250],
    )


@receiver(post_delete)
def audit_delete(sender, instance, **kwargs):
    if not should_audit(instance):
        return
    AuditLog.objects.create(
        actor=current_actor.get(),
        action="deleted",
        model_name=instance._meta.label,
        object_id=str(instance.pk),
        object_repr=str(instance)[:250],
    )


@receiver(post_save, sender="auth.User")
def ensure_user_role(sender, instance, created, raw=False, **kwargs):
    if raw:
        return
    default_role = UserRole.Role.ADMIN if instance.is_superuser else UserRole.Role.CUSTOMER
    role, was_created = UserRole.objects.get_or_create(user=instance, defaults={"role": default_role})
    if instance.is_superuser and role.role != UserRole.Role.ADMIN:
        role.role = UserRole.Role.ADMIN
        role.save()


@receiver(post_save, sender=InfluencerProfile)
def assign_influencer_role(sender, instance, raw=False, **kwargs):
    if raw or instance.user.is_superuser:
        return
    role, _ = UserRole.objects.get_or_create(user=instance.user)
    if role.role != UserRole.Role.INFLUENCER:
        role.role = UserRole.Role.INFLUENCER
        role.save()


@receiver(post_save, sender=Order)
def reverse_invalidated_order_commission(sender, instance, raw=False, **kwargs):
    if raw or instance.status not in (Order.Status.CANCELLED, Order.Status.RETURNED, Order.Status.REFUNDED):
        return
    InfluencerCommission.objects.filter(order=instance).exclude(
        status=InfluencerCommission.Status.REVERSED
    ).update(status=InfluencerCommission.Status.REVERSED, note="Automatically reversed because the order was invalidated.")
