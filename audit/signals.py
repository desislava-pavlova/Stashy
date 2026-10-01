"""Model signal handlers that write the audit log.

Django sends signals around every save() and delete(), including ones made by
automatic code, cascaded deletes and the admin. Queryset .update() and
bulk_create() skip signals, so code that must be audited avoids them.

Storage matches Phase 14 / A3: raw, database-style values (stored choice keys,
ISO dates, exact decimals), one row per changed column on UPDATE, and one row
naming the whole record on INSERT/DELETE (with its full values kept, A4).
"""
from dataclasses import replace
from functools import partial

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, router, transaction
from django.utils import timezone

from .context import SYSTEM, current_context, new_operation_id
from .models import AuditLogEntry

Action = AuditLogEntry.Action

# A6: secrets never appear in the log, not even their hash.
HIDDEN_FIELDS = {("finance.profile", "recovery_code_hash")}


def is_tracked(model):
    return model._meta.app_label in settings.AUDIT_TRACKED_APPS and not model._meta.auto_created


def describe(obj):
    try:
        return str(obj)[:255]
    except Exception:  # a broken __str__ must never stop real data from being saved
        return f"{obj._meta.verbose_name} #{obj.pk}"


def _raw(sender, field, raw, instance=None):
    """Turn a stored value into the raw text kept in the log - the stored value itself,
    not a formatted-for-a-viewer version of it."""
    if (sender._meta.label_lower, field.name) in HIDDEN_FIELDS:
        return None if raw in (None, "") else "(hidden)"
    if raw is None or raw == "":
        return None
    if field.is_relation:
        related = field.get_cached_value(instance, None) if instance is not None else None
        if related is None or related.pk != field.to_python(raw):
            related = field.related_model._base_manager.filter(pk=raw).first()
        name = describe(related) if related is not None else "no longer exists"
        return f"{raw} ({name})"
    if isinstance(field, models.DecimalField):
        return str(field.to_python(raw))
    if isinstance(raw, bool):
        return "true" if raw else "false"
    if hasattr(raw, "isoformat"):
        return raw.isoformat()
    return str(raw)


def _snapshot(sender, instance):
    values = {}
    for field in sender._meta.concrete_fields:
        if field.primary_key:
            continue
        shown = _raw(sender, field, field.value_from_object(instance), instance)
        if shown is not None:
            values[field.attname] = shown
    return values


def _same(field, before, after):
    try:
        return field.to_python(before) == field.to_python(after)
    except ValidationError:
        return before == after


def _resolved_context():
    """An operation id, generated once and reused, so every row from the same save()/m2m
    change/cascade shares it - even outside a request, where current_context() otherwise
    starts with an empty one and write_row would mint a fresh id for every single row."""
    context = current_context()
    return context if context.operation else replace(context, operation=new_operation_id())


def write_row(action, *, table, row_id, row_label, column="", old_value=None, new_value=None,
               values=None, timestamp=None, context=None):
    context = context or current_context()
    request = context.request
    user = getattr(request, "user", None)
    AuditLogEntry.objects.create(
        timestamp=timestamp or timezone.now(),
        operation=context.operation or new_operation_id(),
        action=action,
        source=context.source,
        table=table,
        row_id=row_id,
        row_label=row_label[:255],
        column=column,
        old_value=old_value,
        new_value=new_value,
        values=values or {},
        reason=context.reason[:255],
        request=f"{request.method} {request.get_full_path()}"[:255] if request is not None else "",
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
    table = sender._meta.db_table
    row_id = str(instance.pk)
    row_label = describe(instance)
    if created:
        write_row(Action.INSERT, table=table, row_id=row_id, row_label=row_label, values=_snapshot(sender, instance))
        return
    old = instance.__dict__.pop("_audit_old", None)
    if old is None:
        return
    now = timezone.now()
    context = _resolved_context()
    for field in sender._meta.concrete_fields:
        if field.primary_key:
            continue
        if update_fields is not None and field.name not in update_fields and field.attname not in update_fields:
            continue
        before, after = old[field.attname], field.value_from_object(instance)
        if _same(field, before, after):
            continue
        write_row(
            Action.UPDATE, table=table, row_id=row_id, row_label=row_label, column=field.attname,
            old_value=_raw(sender, field, before), new_value=_raw(sender, field, after, instance),
            timestamp=now, context=context,
        )


# --- deletes ---------------------------------------------------------------

def remember_deleted(sender, instance, **kwargs):
    if not is_tracked(sender):
        return
    instance._audit_deleted = (describe(instance), _snapshot(sender, instance))
    _log_cleared_references_after_commit(instance)


def log_delete(sender, instance, **kwargs):
    if not is_tracked(sender) or "_audit_deleted" not in instance.__dict__:
        return
    row_label, snapshot = instance.__dict__.pop("_audit_deleted")
    write_row(Action.DELETE, table=sender._meta.db_table, row_id=str(instance.pk), row_label=row_label, values=snapshot)


def _log_cleared_references_after_commit(instance):
    """on_delete=SET_NULL blanks other rows with a bulk UPDATE that sends no signals, so log those here.

    Logging waits until the delete has committed: by then, rows that were
    themselves deleted in the same action are gone and don't get a misleading
    "changed" entry.
    """
    context = _resolved_context()
    # on_commit() waits for the commit of one database only - the stash this delete writes to,
    # not `default` (the picker's empty placeholder), which is what it uses without `using=`.
    using = router.db_for_write(type(instance), instance=instance)
    old_value = f"{instance.pk} ({describe(instance)})"
    reason = f"Cleared because {instance._meta.verbose_name} '{describe(instance)}' (#{instance.pk}) was deleted"
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
                partial(_log_cleared_references, relation.related_model, field, pks, old_value, reason, context),
                using=using,
            )


def _log_cleared_references(model, field, pks, old_value, reason, context):
    automatic = replace(context, source=SYSTEM, reason=reason)
    table = model._meta.db_table
    for obj in model._base_manager.filter(pk__in=pks, **{f"{field.attname}__isnull": True}):
        write_row(
            Action.UPDATE, table=table, row_id=str(obj.pk), row_label=describe(obj), column=field.attname,
            old_value=old_value, new_value=None, context=automatic,
        )


# --- many-to-many (e.g. Watched expenses' categories) -----------------------

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
    table = type(instance)._meta.db_table
    row_id = str(instance.pk)
    row_label = describe(instance)
    now = timezone.now()
    context = _resolved_context()
    adding = action == "post_add"
    for obj in model._base_manager.filter(pk__in=pk_set).order_by("pk"):
        item = f"{obj.pk} ({describe(obj)})"
        write_row(
            Action.UPDATE, table=table, row_id=row_id, row_label=row_label, column=name,
            old_value=None if adding else item, new_value=item if adding else None,
            timestamp=now, context=context,
        )
