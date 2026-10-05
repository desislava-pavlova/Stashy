// Stashy page behavior: light/dark theme toggle and date pickers.
(function () {
    const STORAGE_KEY = 'stashy-theme';
    const root = document.documentElement;
    const systemDark = window.matchMedia('(prefers-color-scheme: dark)');

    function savedTheme() {
        try {
            const theme = localStorage.getItem(STORAGE_KEY);
            return theme === 'light' || theme === 'dark' ? theme : null;
        } catch (e) {
            return null;
        }
    }

    function syncToggles(theme) {
        const next = theme === 'dark' ? 'light' : 'dark';
        document.querySelectorAll('[data-theme-toggle]').forEach(function (button) {
            button.setAttribute('aria-label', 'Switch to ' + next + ' mode');
            const icon = button.querySelector('i');
            if (icon) icon.className = theme === 'dark' ? 'bi bi-sun' : 'bi bi-moon-stars';
            const label = button.querySelector('[data-theme-label]');
            if (label) label.textContent = next === 'dark' ? 'Dark mode' : 'Light mode';
        });
    }

    function applyTheme(theme) {
        root.setAttribute('data-bs-theme', theme);
        syncToggles(theme);
        document.dispatchEvent(new CustomEvent('stashy:themechange', { detail: { theme: theme } }));
    }

    document.addEventListener('click', function (event) {
        if (!event.target.closest('[data-theme-toggle]')) return;
        const next = root.getAttribute('data-bs-theme') === 'dark' ? 'light' : 'dark';
        try {
            localStorage.setItem(STORAGE_KEY, next);
        } catch (e) {
            // Private windows can block storage; the theme still switches for this page.
        }
        applyTheme(next);
    });

    // Until someone picks a theme explicitly, follow the operating system's setting live.
    systemDark.addEventListener('change', function (event) {
        if (!savedTheme()) applyTheme(event.matches ? 'dark' : 'light');
    });

    syncToggles(root.getAttribute('data-bs-theme'));

    var dateFormat = document.body.dataset.dateFormat || 'd/m/Y';
    var firstDayOfWeek = parseInt(document.body.dataset.firstDay, 10);
    // Global so a date box added after the page loaded (the Transactions page's Edit mode)
    // gets the same picker.
    window.stashyDatepicker = function (input) {
        return flatpickr(input, {
            dateFormat: dateFormat, allowInput: true,
            locale: { firstDayOfWeek: isNaN(firstDayOfWeek) ? 1 : firstDayOfWeek },
        });
    };
    document.querySelectorAll('input[data-datepicker]').forEach(function (input) {
        window.stashyDatepicker(input);
    });

    // Typing (or picking) a Start/End date means a range of your own, so the form's Range
    // dropdown switches to "Custom range" straight away. Setting it from here fires no change
    // event, so nothing reloads: the page updates when you click Filter or Apply.
    document.querySelectorAll('input[data-range-date]').forEach(function (input) {
        var range = input.form && input.form.elements.namedItem('range');
        if (!range) return;
        ['input', 'change'].forEach(function (type) {
            input.addEventListener(type, function () {
                if (range.value !== 'custom') range.value = 'custom';
            });
        });
    });
})();
