"""Small JSON endpoints used by the transaction form's autofill JavaScript."""

from django.http import JsonResponse

from ..models import Payee


def api_payee_autofill(request):
    """Given ?name=..., return every category/memo/amount combo on record for that payee -
    one per category, most recently used first, so a repeat-visit payee with several kinds of
    purchase (e.g. a pharmacy: health one week, gifts another) offers each to pick from."""
    name = request.GET.get("name", "").strip()
    if not name:
        return JsonResponse({"combos": []})
    payee = Payee.objects.named(name).first()
    if not payee:
        return JsonResponse({"combos": []})
    combos = [
        {
            "category_id": txn.category_id,
            "category_name": txn.category.full_name if txn.category_id else "Uncategorized",
            "memo": txn.memo,
            "amount": str(txn.amount),
        }
        for txn in payee.category_history()
    ]
    return JsonResponse({"combos": combos})


def api_payee_list(request):
    # The system payees behind Opening Balance/Transfer transactions aren't something you'd
    # type by hand, so they're left out of the everyday autocomplete noise.
    names = list(
        Payee.objects.filter(is_active=True).exclude(name__in=Payee.SYSTEM_NAMES).values_list("name", flat=True)
    )
    return JsonResponse({"payees": names})
