from django.conf import settings
from django.db import models
from django.utils import timezone

from .context import SYSTEM, USER


class AuditLogEntry(models.Model):
    class Action(models.TextChoices):
        CREATE = "create", "Created"
        UPDATE = "update", "Changed"
        DELETE = "delete", "Deleted"

    class Source(models.TextChoices):
        USER_ACTION = USER, "You"
        AUTOMATIC = SYSTEM, "Automatic"

    timestamp = models.DateTimeField(default=timezone.now, db_index=True)
    action = models.CharField(max_length=10, choices=Action.choices)
    source = models.CharField(max_length=10, choices=Source.choices)
    model = models.CharField(max_length=100, db_index=True, help_text="app_label.modelname")
    model_name = models.CharField(max_length=100)
    object_id = models.CharField(max_length=64, db_index=True)
    object_repr = models.CharField("object", max_length=255)
    changes = models.JSONField(default=dict, blank=True)
    reason = models.CharField(max_length=255, blank=True)
    request = models.CharField(max_length=255, blank=True)
    operation = models.CharField(
        max_length=32, db_index=True,
        help_text="Entries sharing this value were caused by the same click or automatic action.",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ["-timestamp", "-id"]
        verbose_name = "audit log entry"
        verbose_name_plural = "audit log"

    def __str__(self):
        return f"{self.get_action_display()} {self.model_name} '{self.object_repr}'"
