// Reconcile checklist: keeps a running total as boxes are checked, and only reveals the
// "accept the difference" option once there actually is one.
(function () {
    const summary = document.getElementById('reconcile-summary');
    const form = document.getElementById('reconcile-form');
    if (!summary || !form) return;

    const openingBalance = parseFloat(summary.dataset.openingBalance) || 0;
    const statementBalance = parseFloat(summary.dataset.statementBalance) || 0;
    const symbol = summary.dataset.currencySymbol || '';
    const suffix = summary.dataset.currencySuffix === '1';
    const checkedTotalEl = summary.querySelector('[data-checked-total]');
    const newBalanceEl = summary.querySelector('[data-new-balance]');
    const differenceEl = summary.querySelector('[data-difference]');
    const acceptRow = summary.querySelector('[data-accept-row]');
    const acceptBox = document.getElementById('accept-difference');
    const checkboxes = Array.from(form.querySelectorAll('input[type="checkbox"][name="transaction"]'));
    const markAllButton = document.getElementById('mark-all-btn');

    function formatAmount(value) {
        const number = stashyNumber(Math.abs(value), 2);
        const signed = (value < 0 ? '-' : '') + number;
        if (!symbol) return signed;
        return suffix ? signed + ' ' + symbol : (value < 0 ? '-' + symbol + number : symbol + signed);
    }

    function recalc() {
        const checkedTotal = checkboxes
            .filter(function (box) { return box.checked; })
            .reduce(function (sum, box) { return sum + parseFloat(box.dataset.amount || '0'); }, 0);
        const newBalance = openingBalance + checkedTotal;
        const difference = Math.round((statementBalance - newBalance) * 100) / 100;

        checkedTotalEl.textContent = formatAmount(checkedTotal);
        newBalanceEl.textContent = formatAmount(newBalance);

        if (Math.abs(difference) < 0.005) {
            differenceEl.textContent = formatAmount(0);
            differenceEl.className = 'amount-positive';
            acceptRow.hidden = true;
            if (acceptBox) acceptBox.checked = false;
        } else {
            differenceEl.textContent = formatAmount(difference);
            differenceEl.className = 'amount-negative';
            acceptRow.hidden = false;
        }

        if (markAllButton && checkboxes.length) {
            const allChecked = checkboxes.every(function (box) { return box.checked; });
            markAllButton.textContent = allChecked ? 'Unmark all' : 'Mark all';
        }
    }

    if (markAllButton) {
        markAllButton.addEventListener('click', function () {
            const allChecked = checkboxes.every(function (box) { return box.checked; });
            checkboxes.forEach(function (box) { box.checked = !allChecked; });
            recalc();
        });
    }

    checkboxes.forEach(function (box) { box.addEventListener('change', recalc); });
    recalc();
})();
