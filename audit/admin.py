from urllib.parse import urlencode

from django.contrib import admin
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.html import format_html, format_html_join
from django.utils.text import Truncator

from finance import regional

from .models import AuditLogEntry


def _label(field_name):
    return field_name.replace("_", " ").capitalize()


def _text(value):
    if value is None:
        return "—"
    if value is True:
        return "Yes"
    if value is False:
        return "No"
    if isinstance(value, dict):
        return "; ".join(f"{kind.capitalize()}: {', '.join(items)}" for kind, items in value.items())
    return str(value)


def _before_after(value):
    if isinstance(value, list):
        return _text(value[0]), _text(value[1])
    return "—", _text(value)


@admin.register(AuditLogEntry)
class AuditLogEntryAdmin(admin.ModelAdmin):
    list_display = ("when", "action", "source", "model_name", "object_link", "summary", "why", "same_action")
    list_filter = ("action", "source", "model", "timestamp")
    search_fields = ("object_repr", "object_id", "reason", "request")
    date_hierarchy = "timestamp"
    list_per_page = 100
    list_select_related = ("user",)
    fields = (
        "when", "action", "source", "user", "model_name", "object_id", "object_repr",
        "reason", "request", "same_action", "details",
    )
    readonly_fields = fields

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="When", ordering="timestamp")
    def when(self, obj):
        # The timestamp is a raw DateTimeField, so this reads the viewer's own preferences (D10);
        # the changes JSON below is still formatted at write time — see the Phase 3 backlog note.
        return regional.format_datetime(obj.timestamp, seconds=True)

    @admin.display(description="Object", ordering="object_repr")
    def object_link(self, obj):
        query = urlencode({"model": obj.model, "object_id": obj.object_id})
        return format_html('<a href="?{}" title="Full history of this object">{}</a>', query, obj.object_repr)

    @admin.display(description="What changed")
    def summary(self, obj):
        if obj.action != AuditLogEntry.Action.UPDATE:
            return f"{len(obj.changes)} field{'s' if len(obj.changes) != 1 else ''}"
        parts = []
        for name, value in obj.changes.items():
            before, after = _before_after(value)
            parts.append(f"{_label(name)}: {before} → {after}")
        return Truncator("; ".join(parts)).chars(140)

    @admin.display(description="Why")
    def why(self, obj):
        return obj.reason or obj.request or "—"

    @admin.display(description="Same action")
    def same_action(self, obj):
        url = f"{reverse('admin:audit_auditlogentry_changelist')}?{urlencode({'operation': obj.operation})}"
        return format_html('<a href="{}">show all</a>', url)

    @admin.display(description="Details")
    def details(self, obj):
        if obj.action == AuditLogEntry.Action.UPDATE:
            header = format_html("<tr><th>{}</th><th>{}</th><th>{}</th></tr>", "Field", "Before", "After")
            rows = format_html_join(
                "", "<tr><td>{}</td><td>{}</td><td>{}</td></tr>",
                ((_label(name), *_before_after(value)) for name, value in obj.changes.items()),
            )
        else:
            header = format_html("<tr><th>{}</th><th>{}</th></tr>", "Field", "Value")
            rows = format_html_join(
                "", "<tr><td>{}</td><td>{}</td></tr>",
                ((_label(name), _text(value)) for name, value in obj.changes.items()),
            )
        return format_html("<table>{}{}</table>", header, rows)


class AuditHistoryAdminMixin:
    """Point the admin's "History" button at this object's audit trail."""

    def history_view(self, request, object_id, extra_context=None):
        query = urlencode({"model": self.model._meta.label_lower, "object_id": object_id})
        return redirect(f"{reverse('admin:audit_auditlogentry_changelist')}?{query}")
