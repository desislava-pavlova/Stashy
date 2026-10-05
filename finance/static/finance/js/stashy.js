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
        flatpickr(input, {
            dateFormat: dateFormat, allowInput: true,
            locale: { firstDayOfWeek: isNaN(firstDayOfWeek) ? 1 : firstDayOfWeek },
        });
    };
    document.querySelectorAll('input[data-datepicker]').forEach(function (input) {
        window.stashyDatepicker(input);
    });
})();
