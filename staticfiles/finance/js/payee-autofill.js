// Wires a payee name input to Stashy's per-category memory (Phase 6) and to a styled
// typeahead of known payee names (Phase 6.1 - replaces the browser's native <datalist>
// popup, which can't be made to look like the rest of the app).
function stashyPayeeAutofill(config) {
    var payeeInput = config.payeeInput;
    if (!payeeInput) return;
    var categorySelect = config.categorySelect;
    var memoInput = config.memoInput;
    var amountInput = config.amountInput;
    var comboBox = config.suggestions; // category/memo/amount pick list
    var nameBox = config.nameSuggestions; // payee-name typeahead
    var knownNames = [];

    var touched = { category: false, memo: false, amount: false };
    if (categorySelect) categorySelect.addEventListener('change', function () { touched.category = true; });
    if (memoInput) memoInput.addEventListener('input', function () { touched.memo = true; });
    if (amountInput) amountInput.addEventListener('input', function () { touched.amount = true; });

    function hideCombo() {
        if (!comboBox) return;
        comboBox.hidden = true;
        comboBox.innerHTML = '';
    }

    function hideNames() {
        if (!nameBox) return;
        nameBox.hidden = true;
        nameBox.innerHTML = '';
    }

    function applyCombo(combo) {
        if (combo.category_id && categorySelect && !touched.category) categorySelect.value = combo.category_id;
        if (combo.memo && memoInput && !touched.memo && !memoInput.value) memoInput.value = combo.memo;
        if (combo.amount && amountInput && !touched.amount && !amountInput.value) amountInput.value = combo.amount;
        hideCombo();
    }

    function renderCombos(combos) {
        if (!comboBox) return;
        comboBox.innerHTML = '';
        combos.forEach(function (combo) {
            var button = document.createElement('button');
            button.type = 'button';
            button.className = 'payee-suggestion';
            var bits = [combo.category_name || 'Uncategorized'];
            if (combo.memo) bits.push(combo.memo);
            if (combo.amount) bits.push(combo.amount);
            button.textContent = bits.join(' · ');
            button.addEventListener('click', function () { applyCombo(combo); });
            comboBox.appendChild(button);
        });
        comboBox.hidden = combos.length === 0;
    }

    function lookupCombos(name) {
        fetch(config.autofillUrl + '?name=' + encodeURIComponent(name))
            .then(function (r) { return r.json(); })
            .then(function (data) {
                var combos = (data && data.combos) || [];
                if (combos.length === 1) {
                    applyCombo(combos[0]);
                } else if (combos.length > 1) {
                    renderCombos(combos);
                }
            });
    }

    function renderNames(matches) {
        if (!nameBox) return;
        nameBox.innerHTML = '';
        matches.forEach(function (name) {
            var button = document.createElement('button');
            button.type = 'button';
            button.className = 'payee-suggestion';
            button.textContent = name;
            button.addEventListener('mousedown', function (event) {
                // mousedown (not click) fires before the input's blur, so this wins the race.
                event.preventDefault();
                payeeInput.value = name;
                hideNames();
                lookupCombos(name);
            });
            nameBox.appendChild(button);
        });
        nameBox.hidden = matches.length === 0;
    }

    var debounceTimer;
    payeeInput.addEventListener('input', function () {
        clearTimeout(debounceTimer);
        hideCombo();
        var value = payeeInput.value.trim();
        if (!value) {
            hideNames();
            return;
        }
        var lower = value.toLowerCase();
        var exactMatch = knownNames.some(function (name) { return name.toLowerCase() === lower; });
        var matches = exactMatch ? [] : knownNames.filter(function (name) {
            return name.toLowerCase().indexOf(lower) !== -1;
        }).slice(0, 8);
        renderNames(matches);
        debounceTimer = setTimeout(function () { lookupCombos(value); }, 250);
    });
    payeeInput.addEventListener('blur', function () {
        setTimeout(hideNames, 100);
    });

    if (config.payeeListUrl) {
        fetch(config.payeeListUrl)
            .then(function (r) { return r.json(); })
            .then(function (data) {
                knownNames = (data && data.payees) || [];
            });
    }
}
