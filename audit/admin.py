from datetime import timedelta
from urllib.parse import urlencode

from django.contrib import admin
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html, format_html_join

from .models import AuditLogEntry


def _text(value):
    return "—" if value in (None, "") else str(value)


def format_timestamp(value):
    """The audit log's own timestamp column: always ISO-style, with milliseconds and the
    offset, in the active time zone (the viewer's own) - unlike every other date/time in the
    app, it never follows a date-format preference, since it reads like a database log."""
    if value is None or value == "":
        return ""
    local = timezone.localtime(value) if timezone.is_aware(value) else value
    offset = local.utcoffset() if timezone.is_aware(local) else None
    minutes = int((offset or timedelta()).total_seconds() // 60)
    sign = "+" if minutes >= 0 else "-"
    hours, minutes = divmod(abs(minutes), 60)
    return f"{local:%Y-%m-%d %H:%M:%S}.{local.microsecond // 1000:03d} {sign}{hours:02d}:{minutes:02d}"


@admin.register(AuditLogEntry)
class AuditLogEntryAdmin(admin.ModelAdmin):
    list_display = ("id", "when", "op", "table", "row", "column_display", "old", "new", "source")
    list_display_links = ("id",)
    list_filter = ("action", "source", "table", "column", "timestamp")
    search_fields = ("row_id", "row_label", "old_value", "new_value", "reason", "request")
    date_hierarchy = "timestamp"
    list_per_page = 100
    list_select_related = ("user",)
    fields = (
        "when",
        "op",
        "source",
        "user",
        "table",
        "row",
        "column_display",
        "old",
        "new",
        "reason",
        "request",
        "same_operation",
        "values_table",
    )
    readonly_fields = fields

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="Time", ordering="timestamp")
    def when(self, obj):
        return format_timestamp(obj.timestamp)

    @admin.display(description="Op", ordering="action")
    def op(self, obj):
        return obj.get_action_display()

    @admin.display(description="Row", ordering="row_id")
    def row(self, obj):
        label = f"{obj.row_id} ({obj.row_label})" if obj.row_label else obj.row_id
        query = urlencode({"table": obj.table, "row_id": obj.row_id})
        return format_html('<a href="?{}" title="Full history of this row">{}</a>', query, label)

    @admin.display(description="Column", ordering="column")
    def column_display(self, obj):
        return _text(obj.column)

    @admin.display(description="Old")
    def old(self, obj):
        return _text(obj.old_value)

    @admin.display(description="New")
    def new(self, obj):
        return _text(obj.new_value)

    @admin.display(description="Same operation")
    def same_operation(self, obj):
        url = f"{reverse('admin:audit_auditlogentry_changelist')}?{urlencode({'operation': obj.operation})}"
        return format_html('<a href="{}">show all rows from this action</a>', url)

    @admin.display(description="Full values")
    def values_table(self, obj):
        if not obj.values:
            return "—"
        header = format_html("<tr><th>{}</th><th>{}</th></tr>", "Column", "Value")
        rows = format_html_join(
            "",
            "<tr><td>{}</td><td>{}</td></tr>",
            ((column, _text(value)) for column, value in obj.values.items()),
        )
        return format_html("<table>{}{}</table>", header, rows)


class AuditHistoryAdminMixin:
    """Point the admin's "History" button at this object's audit trail."""

    def history_view(self, request, object_id, extra_context=None):
        query = urlencode({"table": self.model._meta.db_table, "row_id": object_id})
        return redirect(f"{reverse('admin:audit_auditlogentry_changelist')}?{query}")
