from django.apps import AppConfig
from django.db.models import signals as model_signals


class AuditConfig(AppConfig):
    name = "audit"
    verbose_name = "Audit log"

    def ready(self):
        # Imported here: signals imports the models, which don't exist yet when Django
        # imports this module.
        from . import signals

        model_signals.pre_save.connect(signals.remember_old_values, dispatch_uid="audit.pre_save")
        model_signals.post_save.connect(signals.log_save, dispatch_uid="audit.post_save")
        model_signals.pre_delete.connect(signals.remember_deleted, dispatch_uid="audit.pre_delete")
        model_signals.post_delete.connect(signals.log_delete, dispatch_uid="audit.post_delete")
        model_signals.m2m_changed.connect(signals.log_m2m, dispatch_uid="audit.m2m_changed")
