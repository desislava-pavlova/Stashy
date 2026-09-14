from .context import request_context


class AuditContextMiddleware:
    """Mark every change made while handling a request as done by the logged-in user."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        with request_context(request):
            return self.get_response(request)
