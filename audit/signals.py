"""Model signal handlers that write the audit log.

Django sends signals around every save() and delete(), including ones made by
automatic code, cascaded deletes and the admin. Queryset .update() and
bulk_create() skip signals, so code that must be audited avoids them.
"""
from dataclasses import replace
from datetime import datetime
from functools import partial

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from .context import SYSTEM, current_context, new_operation_id
from .models import AuditLogEntry

Action = AuditLogEntry.Action


def is_tracked(model):
    return model._meta.app_label in settings.AUDIT_TRACKED_APPS and not model._meta.auto_created


def describe(obj):
    try:
        return str(obj)[:255]
    except Exception:  # a broken __str__ must never stop real data from being saved
        return f"{obj._meta.verbose_name} #{obj.pk}"


def _display(field, raw, instance=None):
    """Turn a stored value into the human-readable text kept in the log."""
    if raw is None or raw == "":
        return None
    if field.is_relation:
        related = field.get_cached_value(instance, None) if instance is not None else None
        if related is None or related.pk != field.to_python(raw):
            related = field.related_model._base_manager.filter(pk=raw).first()
        return f"{describe(related)} (#{raw})" if related is not None else f"#{raw} (no longer exists)"
    if field.flatchoices:
        return str(dict(field.flatchoices).get(raw, raw))
    if isinstance(field, models.DecimalField):
        return f"{field.to_python(raw):.{field.decimal_places}f}"
    if isinstance(raw, datetime):
        return timezone.localtime(raw).strftime("%d/%m/%Y %H:%M:%S") if timezone.is_aware(raw) else raw.strftime("%d/%m/%Y %H:%M:%S")
    if hasattr(raw, "strftime"):
        return raw.strftime("%d/%m/%Y")
    if isinstance(raw, (bool, int, float, str)):
        return raw
    return str(raw)


def _snapshot(instance):
    values = {}
    for field in instance._meta.concrete_fields:
        if field.primary_key:
            continue
        shown = _display(field, field.value_from_object(instance), instance)
        if shown is not None:
            values[field.name] = shown
    return values


def _same(field, before, after):
    try:
        return field.to_python(before) == field.to_python(after)
    except ValidationError:
        return before == after


def write_entry(action, instance, changes, description=None, context=None):
    context = context or current_context()
    request = context.request
    user = getattr(request, "user", None)
    AuditLogEntry.objects.create(
        action=action,
        source=context.source,
        model=instance._meta.label_lower,
        model_name=instance._meta.verbose_name,
        object_id=str(instance.pk),
        object_repr=description or describe(instance),
        changes=changes,
        reason=context.reason[:255],
        request=f"{request.method} {request.get_full_path()}"[:255] if request is not None else "",
        operation=context.operation or new_operation_id(),
        user_id=user.pk if user is not None and user.is_authenticated else None,
    )


# --- saves -----------------------------------------------------------------

def remember_old_values(sender, instance, raw=False, **kwargs):
    if raw or not is_tracked(sender) or instance._state.adding:
        return
    attnames = [field.attname for field in sender._meta.concrete_fields]
    instance._audit_old = sender._base_manager.filter(pk=instance.pk).values(*attnames).first()


def log_save(sender, instance, created, raw=False, update_fields=None, **kwargs):
    if raw or not is_tracked(sender):
        return
    if created:
        write_entry(Action.CREATE, instance, _snapshot(instance))
        return
    old = instance.__dict__.pop("_audit_old", None)
    if old is None:
        return
    changes = {}
    for field in sender._meta.concrete_fields:
        if update_fields is not None and field.name not in update_fields and field.attname not in update_fields:
            continue
        before, after = old[field.attname], field.value_from_object(instance)
        if not _same(field, before, after):
            changes[field.name] = [_display(field, before), _display(field, after, instance)]
    if changes:
        write_entry(Action.UPDATE, instance, changes)


# --- deletes ---------------------------------------------------------------

def remember_deleted(sender, instance, **kwargs):
    if not is_tracked(sender):
        return
    instance._audit_deleted = (describe(instance), _snapshot(instance))
    _log_cleared_references_after_commit(instance)


def log_delete(sender, instance, **kwargs):
    if not is_tracked(sender) or "_audit_deleted" not in instance.__dict__:
        return
    description, snapshot = instance.__dict__.pop("_audit_deleted")
    write_entry(Action.DELETE, instance, snapshot, description=description)


def _log_cleared_references_after_commit(instance):
    """on_delete=SET_NULL blanks other rows with a bulk UPDATE that sends no signals, so log those here.

    Logging waits until the delete has committed: by then, rows that were
    themselves deleted in the same action are gone and don't get a misleading
    "changed" entry.
    """
    context = current_context()
    description = f"{describe(instance)} (#{instance.pk})"
    reason = f"Cleared because {instance._meta.verbose_name} '{describe(instance)}' was deleted"
    for relation in instance._meta.get_fields(include_hidden=True):
        if not (relation.auto_created and not relation.concrete and (relation.one_to_many or relation.one_to_one)):
            continue
        if relation.on_delete is not models.SET_NULL or not is_tracked(relation.related_model):
            continue
        field = relation.field
        pks = list(
            relation.related_model._base_manager.filter(**{field.name: instance}).values_list("pk", flat=True)
        )
        if pks:
            transaction.on_commit(
                partial(_log_cleared_references, relation.related_model, field, pks, description, reason, context)
            )


def _log_cleared_references(model, field, pks, description, reason, context):
    automatic = replace(context, source=SYSTEM, reason=reason)
    for obj in model._base_manager.filter(pk__in=pks, **{f"{field.attname}__isnull": True}):
        write_entry(Action.UPDATE, obj, {field.name: [description, None]}, context=automatic)


# --- many-to-many (e.g. transaction tags) ------------------------------------

def _m2m_field_name(through, instance):
    for field in instance._meta.get_fields(include_hidden=True):
        if not field.many_to_many:
            continue
        if isinstance(field, models.ManyToManyField):
            if field.remote_field.through is through:
                return field.name
        elif field.through is through:
            return field.get_accessor_name()
    return "related items"


def log_m2m(sender, instance, action, model, pk_set, **kwargs):
    if not is_tracked(type(instance)) or action not in ("post_add", "post_remove", "pre_clear"):
        return
    name = _m2m_field_name(sender, instance)
    if action == "pre_clear":
        pk_set = set(getattr(instance, name).values_list("pk", flat=True))
    if not pk_set:
        return
    items = sorted(describe(obj) for obj in model._base_manager.filter(pk__in=pk_set))
    write_entry(Action.UPDATE, instance, {name: {"added" if action == "post_add" else "removed": items}})
