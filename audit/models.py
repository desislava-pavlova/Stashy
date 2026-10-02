from django.conf import settings
from django.db import models
from django.utils import timezone

from .context import SYSTEM, USER


class AuditLogEntry(models.Model):
    """One row per database-level event (Phase 14 / A3): an INSERT or DELETE names the whole
    row, an UPDATE gets one row per changed column. Values are stored raw - stored keys,
    ISO dates, exact decimals - never formatted for a viewer, so the log reads like a real
    database audit log rather than a friendly summary."""

    class Action(models.TextChoices):
        INSERT = "insert", "INSERT"
        UPDATE = "update", "UPDATE"
        DELETE = "delete", "DELETE"

    class Source(models.TextChoices):
        USER_ACTION = USER, "You"
        AUTOMATIC = SYSTEM, "Automatic"

    timestamp = models.DateTimeField(default=timezone.now, db_index=True)
    operation = models.CharField(
        max_length=32,
        db_index=True,
        help_text="Entries sharing this value were caused by the same click or automatic action.",
    )
    action = models.CharField(max_length=10, choices=Action.choices)
    source = models.CharField(max_length=10, choices=Source.choices)
    table = models.CharField(max_length=100, db_index=True, help_text="Raw database table name.")
    row_id = models.CharField(max_length=64, db_index=True)
    row_label = models.CharField(max_length=255, blank=True, help_text="The row's own name at the time of the event.")
    column = models.CharField(
        max_length=100, blank=True, db_index=True, help_text="Raw column name; blank for INSERT/DELETE."
    )
    # NULL ("no value": the row didn't exist yet, or no longer does) is a different fact from "".
    old_value = models.TextField(null=True, blank=True)  # noqa: DJ001
    new_value = models.TextField(null=True, blank=True)  # noqa: DJ001
    values = models.JSONField(default=dict, blank=True, help_text="Full raw column values (INSERT/DELETE only, A4).")
    reason = models.CharField(max_length=255, blank=True)
    request = models.CharField(max_length=255, blank=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ["-timestamp", "-id"]
        verbose_name = "audit log entry"
        verbose_name_plural = "audit log"

    def __str__(self):
        row = f"{self.table} {self.row_id}"
        if self.action == self.Action.UPDATE:
            return f"UPDATE {row} {self.column}: {self.old_value} -> {self.new_value}"
        return f"{self.get_action_display()} {row}"
