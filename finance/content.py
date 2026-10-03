"""The "Stashy tips" shown in the help card beside a form (finance/includes/help_card.html).

Plain text kept out of the views: it's wording, not logic, so it lives in one place where it
is easy to find and change. Each list is passed to a template as `tips`.
"""

ACCOUNT_TIPS = [
    "Opening balance is what the account held on the day you start tracking it in Stashy"
    " — Stashy turns it into a real transaction.",
    "Institution is optional — fill it in only when a bank is behind the account.",
    "Stopped using an account? Untick Is active instead of deleting it, so its history stays.",
]
CATEGORY_TIPS = [
    "Give a category a parent to make it a subcategory, like Food → Groceries.",
    "Income and expense categories are reported separately — the Parent list narrows to match once you pick a type.",
    "Keeping it simple is fine too: top-level categories can be used on their own.",
]
PAYEE_TIPS = [
    "You rarely need to add payees by hand — Stashy memorizes them as you enter transactions.",
    "Category, memo and amount fill in automatically from what you used last time, per category — "
    "so a payee you use for more than one kind of purchase offers each one to pick from.",
]
REMINDER_TIPS = [
    "Reminders never create transactions on their own — press Enter when a bill is actually paid.",
    "Pick the account the money leaves from, or arrives in for income.",
    "Anything due within the next 7 days shows up on your dashboard.",
]
TRANSACTION_TIPS = [
    "Type a payee you've used before and Stashy fills in the category, memo and amount.",
    "Use a minus sign for money going out, like -12.50.",
    "Moving money between your own accounts? Use the Transfer button on the account page instead.",
]
IMPORT_TIPS = [
    "CSV or XLSX columns recognized: Date, Num, Account, Payee, Category, Memo, Amount, Reconciled - "
    "only Date needs a value; the rest can be left blank.",
    'A category that doesn\'t exist yet is created for you automatically - marked "auto" in '
    'the category list - and understands "Parent:Child" or "Parent > Child".',
    "An Account column lets one file cover several accounts at once; a row naming an account "
    "that doesn't exist yet is skipped rather than guessed at.",
    'Leave Date format on "Detect automatically" unless Stashy gets it wrong for this '
    "particular file - it reads a sample of the dates first and only asks you to pick when it can't tell.",
]
