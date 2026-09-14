// Dashboard spending donut: click a category to see its subcategories, then click again for its transactions.
(function () {
    const panel = document.getElementById('spending-panel');
    const canvas = document.getElementById('spending-donut');
    if (!panel || !canvas) return;

    const tree = JSON.parse(document.getElementById('spending-tree').textContent);
    const period = JSON.parse(document.getElementById('spending-period').textContent);
    const transactionsUrl = panel.dataset.transactionsUrl;
    const mainCurrency = panel.dataset.mainCurrency || '';
    const currencySuffix = panel.dataset.currencySuffix === '1';
    const legend = panel.querySelector('[data-legend]');
    const backButton = panel.querySelector('[data-back]');
    const subtitle = panel.querySelector('[data-subtitle]');
    const centerValue = panel.querySelector('[data-center-value]');
    const centerLabel = panel.querySelector('[data-center-label]');

    let openGroup = null;

    // Same symbol-placement convention the |moneyc template filter already uses server-side
    // ("9.66 €" for a suffix currency, "$9.66" everywhere else).
    function formatAmount(value) {
        const number = stashyNumber(value, 2);
        if (!mainCurrency) return number;
        return currencySuffix ? number + ' ' + mainCurrency : mainCurrency + number;
    }

    function palette() {
        return Array.from({ length: 8 }, function (_, i) {
            return stashyCssVar('--stashy-chart-' + (i + 1));
        });
    }

    function currentItems() {
        return openGroup ? openGroup.children : tree;
    }

    function transactionsLink(item) {
        const params = new URLSearchParams({ start: period.start, end: period.end });
        if (item.id === null) {
            params.set('category', 'none');
        } else {
            params.set('category', item.id);
            if (openGroup === null || item.with_subcategories) params.set('with_subcategories', '1');
        }
        return transactionsUrl + '?' + params.toString();
    }

    function select(index) {
        const item = currentItems()[index];
        if (!item) return;
        if (openGroup === null && item.children && item.children.length > 1) {
            openGroup = item;
            render();
        } else {
            window.location.href = transactionsLink(item);
        }
    }

    const chart = new Chart(canvas, {
        type: 'doughnut',
        data: { labels: [], datasets: [{ data: [], backgroundColor: [], borderWidth: 3, hoverOffset: 6 }] },
        options: {
            maintainAspectRatio: false,
            cutout: '66%',
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        label: function (ctx) {
                            return ctx.label + ': ' + formatAmount(ctx.parsed);
                        },
                    },
                },
            },
        },
    });

    canvas.addEventListener('click', function (event) {
        const hits = chart.getElementsAtEventForMode(event, 'nearest', { intersect: true }, true);
        if (hits.length) select(hits[0].index);
    });

    function legendItem(item, index, color, total) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'legend-item';
        button.addEventListener('click', function () { select(index); });

        const dot = document.createElement('span');
        dot.className = 'legend-dot';
        dot.style.background = color;
        const name = document.createElement('span');
        name.className = 'legend-name';
        name.textContent = item.name;
        name.title = item.name;
        const amount = document.createElement('span');
        amount.className = 'legend-amount';
        amount.textContent = formatAmount(item.total);
        const share = document.createElement('span');
        share.className = 'legend-share';
        share.textContent = total ? Math.round((item.total / total) * 100) + '%' : '';

        button.append(dot, name, amount, share);
        return button;
    }

    function render() {
        const items = currentItems();
        const colors = palette();
        const total = items.reduce(function (sum, item) { return sum + item.total; }, 0);

        chart.data.labels = items.map(function (item) { return item.name; });
        chart.data.datasets[0].data = items.map(function (item) { return item.total; });
        chart.data.datasets[0].backgroundColor = items.map(function (_, i) { return colors[i % colors.length]; });
        chart.data.datasets[0].borderColor = stashyCssVar('--stashy-surface');
        chart.update();

        centerValue.innerHTML = stashyNumber(total, 2) +
            (mainCurrency ? ' <span class="donut-center-currency">' + mainCurrency + '</span>' : '');
        centerLabel.textContent = openGroup ? openGroup.name : 'spent so far';
        subtitle.textContent = openGroup
            ? 'Click a slice to see its transactions'
            : 'Click a category to dig deeper';
        backButton.hidden = openGroup === null;
        legend.replaceChildren.apply(legend, items.map(function (item, i) {
            return legendItem(item, i, colors[i % colors.length], total);
        }));
    }

    backButton.addEventListener('click', function () {
        openGroup = null;
        render();
    });
    document.addEventListener('stashy:themechange', render);
    render();
})();
