// Transactions list "Edit mode": every row's cells double as editable fields, and each field
// autosaves itself on change via the transaction_quick_edit endpoint, which reuses
// TransactionForm server-side.
//
// The page arrives with view cells only. Each row carries its raw values as data- attributes,
// and the first switch to Edit mode builds every row's controls from those values and the one
// shared #quick-edit-controls template. After that the button just shows and hides them.
(function () {
    var table = document.getElementById('transactions-table');
    if (!table) return;
    var toggleBtn = document.getElementById('edit-mode-toggle');
    var csrfInput = document.querySelector('#transactions-csrf input[name=csrfmiddlewaretoken]');
    var quickEditUrlBase = table.dataset.quickEditUrlBase;
    var controlsTemplate = document.getElementById('quick-edit-controls');
    var controlsBuilt = false;
    var payeeNamesList = document.getElementById('quick-edit-payee-names');
    var payeeNamesLoaded = false;

    function fillControl(control, values) {
        var amount = parseFloat(values.amount);
        switch (control.dataset.cell) {
            case 'date': control.value = values.date; break;
            case 'transaction_number': control.value = values.number; break;
            case 'account_name': control.value = values.account; break;
            case 'payee': control.value = values.payee; break;
            case 'category_name':
                // Transfer categories aren't in the shared list (they aren't picked by hand),
                // but a transaction already filed under one still shows it, selected.
                if (values.categoryLabel) control.add(new Option(values.categoryLabel, values.category));
                control.value = values.category;
                break;
            case 'memo': control.value = values.memo; break;
            case 'payment': control.value = amount < 0 ? values.amount.replace('-', '') : ''; break;
            case 'deposit': control.value = amount > 0 ? values.amount : ''; break;
            case 'reconciled': control.checked = values.reconciled === '1'; break;
        }
    }

    function buildControls() {
        if (controlsBuilt) return;
        controlsBuilt = true;
        table.querySelectorAll('tbody tr[data-pk]').forEach(function (row) {
            var controls = controlsTemplate.content.cloneNode(true);
            controls.querySelectorAll('[data-cell]').forEach(function (control) {
                fillControl(control, row.dataset);
                row.querySelector('[data-view="' + control.dataset.cell + '"]').after(control);
                if (control.dataset.cell === 'date') window.stashyDatepicker(control);
            });
        });
    }

    function loadPayeeNames() {
        if (payeeNamesLoaded || !payeeNamesList || !table.dataset.payeeListUrl) return;
        payeeNamesLoaded = true;
        fetch(table.dataset.payeeListUrl)
            .then(function (response) { return response.json(); })
            .then(function (data) {
                (data.payees || []).forEach(function (name) {
                    var option = document.createElement('option');
                    option.value = name;
                    payeeNamesList.appendChild(option);
                });
            });
    }

    function setEditing(on) {
        table.classList.toggle('editing', on);
        table.querySelectorAll('.quick-edit-view').forEach(function (el) { el.classList.toggle('d-none', on); });
        table.querySelectorAll('.quick-edit-cell').forEach(function (el) { el.classList.toggle('d-none', !on); });
        toggleBtn.innerHTML = on
            ? '<i class="bi bi-check-lg me-1"></i>Done editing'
            : '<i class="bi bi-pencil-square me-1"></i>Edit mode';
        toggleBtn.classList.toggle('btn-primary', on);
        toggleBtn.classList.toggle('btn-outline-secondary', !on);
    }

    toggleBtn.addEventListener('click', function () {
        loadPayeeNames();
        buildControls();
        setEditing(!table.classList.contains('editing'));
    });

    function clearRowErrors(row) {
        row.querySelectorAll('.is-invalid').forEach(function (el) {
            el.classList.remove('is-invalid');
            el.removeAttribute('title');
        });
        row.removeAttribute('title');
    }

    function showRowErrors(row, errors) {
        Object.keys(errors).forEach(function (field) {
            var message = errors[field].join(' ');
            var input = row.querySelector('[data-field="' + field + '"]');
            if (input) {
                input.classList.add('is-invalid');
                input.title = message;
            } else {
                row.title = message;
            }
        });
    }

    function flashSaved(row) {
        row.classList.add('quick-edit-saved');
        window.setTimeout(function () { row.classList.remove('quick-edit-saved'); }, 600);
    }

    function applyRow(row, data) {
        row.querySelector('[data-view="date"]').textContent = data.date;
        row.querySelector('[data-view="transaction_number"]').textContent = data.transaction_number;
        row.querySelector('[data-view="account_name"]').textContent = data.account_name;
        row.querySelector('[data-view="payee"]').textContent = data.payee;
        row.querySelector('[data-view="category_name"]').textContent = data.category_name;
        row.querySelector('[data-view="memo"]').textContent = data.memo;
        row.querySelector('[data-view="payment"]').textContent = data.payment;
        row.querySelector('[data-view="deposit"]').textContent = data.deposit;
        row.querySelector('[data-view="reconciled"]').innerHTML = data.reconciled ? '<i class="bi bi-check-lg"></i>' : '—';
        row.classList.toggle('row-reconciled', data.reconciled);
    }

    function saveRow(row) {
        var pk = row.dataset.pk;
        var payment = row.querySelector('.quick-edit-payment');
        var deposit = row.querySelector('.quick-edit-deposit');
        var amount = '0';
        if (payment && payment.value) amount = '-' + payment.value;
        else if (deposit && deposit.value) amount = deposit.value;

        var body = new URLSearchParams();
        body.set('csrfmiddlewaretoken', csrfInput.value);
        body.set('amount', amount);
        row.querySelectorAll('[data-field]').forEach(function (field) {
            var name = field.dataset.field;
            if (field.type === 'checkbox') {
                if (field.checked) body.set(name, 'on');
            } else {
                body.set(name, field.value);
            }
        });

        fetch(quickEditUrlBase.replace('/0/', '/' + pk + '/'), {
            method: 'POST', body: body, headers: { 'X-Requested-With': 'XMLHttpRequest' },
        })
            .then(function (response) { return response.json(); })
            .then(function (data) {
                if (data.ok) {
                    clearRowErrors(row);
                    applyRow(row, data.row);
                    flashSaved(row);
                } else {
                    showRowErrors(row, data.errors || {});
                }
            })
            .catch(function () { row.title = 'Could not save - check your connection.'; });
    }

    // Typing in Payment clears Deposit and vice versa - only one side of a transfer at a time.
    table.addEventListener('input', function (event) {
        var target = event.target;
        var row = target.closest('tr');
        if (!row) return;
        if (target.classList.contains('quick-edit-payment') && target.value) {
            var deposit = row.querySelector('.quick-edit-deposit');
            if (deposit) deposit.value = '';
        } else if (target.classList.contains('quick-edit-deposit') && target.value) {
            var payment = row.querySelector('.quick-edit-payment');
            if (payment) payment.value = '';
        }
    });

    table.addEventListener('change', function (event) {
        var field = event.target.closest('[data-field]');
        if (!field) return;
        var row = field.closest('tr');
        if (row) saveRow(row);
    });
})();
