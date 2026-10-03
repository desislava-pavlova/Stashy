from datetime import timedelta

from django.shortcuts import render
from django.utils import timezone

from ..currency import SUFFIX_CURRENCIES, RateTable, convert_balances
from ..models import Account, Profile, Reminder, grouped_accounts
from ..reporting import income_and_spending, savings_rate, spending_tree

# Kept as one constant on the model (Reminder.DUE_SOON_DAYS) so the dashboard's "due soon"
# window and each reminder's computed status agree on what "soon" means.
REMINDER_LOOKAHEAD_DAYS = Reminder.DUE_SOON_DAYS


def _greeting():
    hour = timezone.localtime().hour
    if hour < 12:
        return "Good morning"
    if hour < 18:
        return "Good afternoon"
    return "Good evening"


def dashboard(request):
    today = timezone.localdate()
    month_start = today.replace(day=1)
    last_month_end = month_start - timedelta(days=1)
    last_month_start = last_month_end.replace(day=1)

    main_currency = Profile.for_user(request.user).main_currency
    accounts = list(Account.objects.filter(is_active=True).with_balances())
    net_worth, missing_rates = convert_balances(accounts, main_currency)
    # Same grouping/order as the accounts page (Physical Liquid -> Digital Liquid -> Illiquid) -
    # inactive accounts never reach this panel, since `accounts` above already excludes them.
    account_groups = grouped_accounts(accounts)

    # Converted into the main currency exactly like Reports, so the tiles, the donut and the
    # reports never disagree when accounts use different currencies.
    rates = RateTable(main_currency)
    this_month = income_and_spending(month_start, today, rates=rates)
    last_month = income_and_spending(last_month_start, last_month_end, rates=rates)
    rate = savings_rate(last_month["income"], last_month["spending"])

    due_soon = list(
        Reminder.objects.filter(next_due_date__lte=today + timedelta(days=REMINDER_LOOKAHEAD_DAYS))
        .select_related("account")
        .order_by("next_due_date")
    )
    # Already paid or skipped doesn't belong on a "what needs attention" widget, even if its
    # date still falls in the lookahead window.
    due_soon = [r for r in due_soon if r.status(today) not in ("paid", "skipped")]
    for reminder in due_soon:
        reminder.days_until = (reminder.next_due_date - today).days

    context = {
        "greeting": _greeting(),
        "today": today,
        "accounts": accounts,
        "account_groups": account_groups,
        "main_currency": main_currency,
        "missing_rates": missing_rates,
        "net_worth": net_worth,
        "month_income": this_month["income"],
        "month_spending": this_month["spending"],
        "last_month_start": last_month_start,
        "savings_rate": rate,
        "savings_bar": max(0, min(100, int(rate))) if rate is not None else 0,
        "due_soon": due_soon,
        "reminder_lookahead_days": REMINDER_LOOKAHEAD_DAYS,
        "spending_tree": spending_tree(month_start, today, rates=rates),
        "spending_period": {"start": month_start.isoformat(), "end": today.isoformat()},
        # The donut chart (JS) needs to know whether its symbol goes before or after the
        # number, same convention |moneyc already applies server-side.
        "currency_is_suffix": main_currency in SUFFIX_CURRENCIES,
    }
    return render(request, "finance/dashboard.html", context)
