from django.apps import AppConfig


class FinanceConfig(AppConfig):
    name = "finance"

    def ready(self):
        # Every stash that already exists on disk becomes a connection alias here, once, so
        # finance.db_router.StashRouter can route a session's queries to it.
        # StashSelectionMiddleware registers a stash created after this process started. Under
        # `manage.py test`, STASHES_DIR is the test settings' temporary folder, so the only stash
        # found is the test stash.
        #
        # Imported here, not at the top: the registry imports the models, which don't exist
        # yet when Django imports this module.
        from .stashes import registry

        for name in registry.list_stashes():
            registry.ensure_registered(name)
