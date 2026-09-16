// Reports page: builds the 8 report charts from the data embedded via json_script, and wires
// up Expand/Collapse, Show all/Hide all and the filter popovers.
//
// Colors: income is green, expense red, net blue. Categories stacked inside one column share
// their side's color and are told apart by clear dividing lines, not by different colors.
// Watched expenses lines are separate series, so each keeps its own color.
(function () {
    function readJson(id) {
        var el = document.getElementById(id);
        return el ? JSON.parse(el.textContent) : null;
    }

    var spending = readJson('spending-chart-data') || [];
    var spendingStacked = readJson('spending-stacked-chart-data') || [];
    var income = readJson('income-chart-data') || [];
    var incomeStacked = readJson('income-stacked-chart-data') || [];
    var spendingVsAverage = readJson('spending-vs-average-chart-data') || [];
    var incomeVsAverage = readJson('income-vs-average-chart-data') || [];
    var incomeExpense = readJson('income-expense-chart-data') || { periods: [], income: [], expense: [] };
    var incomeExpenseVsAverage = readJson('income-expense-vs-average-chart-data');
    var netWorthSeries = readJson('net-worth-chart-data') || [];
    var watchedSeries = readJson('watched-chart-data') || [];

    var charts = [];

    function colors() {
        return {
            income: stashyCssVar('--stashy-positive'),
            expense: stashyCssVar('--stashy-negative'),
            net: stashyCssVar('--stashy-net'),
            line: stashyCssVar('--stashy-chart-line'),
            surface: stashyCssVar('--stashy-surface'),
            muted: stashyCssVar('--stashy-muted'),
            palette: [1, 2, 3, 4, 5, 6, 7, 8].map(function (n) { return stashyCssVar('--stashy-chart-' + n); }),
        };
    }

    // One category's piece of a stacked column, outlined in the chart line color (dark brown in
    // light mode) so the line between two pieces of the same color is easy to see.
    function stackDataset(label, stack, data, fill, c) {
        return { label: label, stack: stack, data: data, backgroundColor: fill, borderColor: c.line, borderWidth: 1.5 };
    }

    function valueIn(period, name) {
        var match = period.categories.filter(function (row) { return row.name === name; })[0];
        return match ? match.value : 0;
    }

    // An x-y dot for the average. clip: false so a dot at 0 (right on the chart's edge) is
    // drawn whole instead of cut off.
    function averageMarkers(data, c) {
        return {
            type: 'line', label: 'Average', data: data, showLine: false, clip: false,
            pointStyle: 'circle', pointRadius: 6, pointHoverRadius: 8,
            pointBackgroundColor: c.line, pointBorderColor: c.surface, pointBorderWidth: 2,
        };
    }

    // A single data point can't draw a line across the chart on its own - repeat it at both
    // edges so there's a visible dot and a flat line.
    function flatLineIfSingle(labels, values) {
        if (labels.length !== 1) return { labels: labels, values: values };
        return { labels: [labels[0], labels[0]], values: [values[0], values[0]] };
    }

    function makeChart(id, build) {
        var canvas = document.getElementById(id);
        if (!canvas) return null;
        var chart = build(canvas);
        if (chart) charts.push(chart);
        return chart;
    }

    var noLegend = { legend: { display: false } };
    var stackedScales = { x: { stacked: true }, y: { stacked: true } };

    // Spending / Income by category: one bar per main category, or - with a subtotal - one
    // column per period with the categories stacked inside it.
    function buildCategoryChart(canvas, flat, stacked, color) {
        var c = colors();
        if (!canvas.dataset.subtotal) {
            return new Chart(canvas, {
                type: 'bar',
                data: {
                    labels: flat.map(function (r) { return r.name; }),
                    datasets: [{ label: 'Amount', data: flat.map(function (r) { return r.total; }), backgroundColor: color, borderRadius: 4 }],
                },
                options: { indexAxis: 'y', plugins: noLegend },
            });
        }
        return new Chart(canvas, {
            type: 'bar',
            data: {
                labels: stacked.map(function (p) { return p.period; }),
                datasets: flat.map(function (r) {
                    return stackDataset(r.name, 'stack', stacked.map(function (p) { return valueIn(p, r.name); }), color, c);
                }),
            },
            options: { scales: stackedScales, plugins: noLegend },
        });
    }

    function buildVsAverageChart(canvas, rows, color) {
        var c = colors();
        return new Chart(canvas, {
            type: 'bar',
            data: {
                labels: rows.map(function (r) { return r.name; }),
                datasets: [
                    { type: 'bar', label: 'Current', data: rows.map(function (r) { return r.current; }), backgroundColor: color, borderRadius: 4 },
                    averageMarkers(rows.map(function (r) { return r.average; }), c),
                ],
            },
            options: { indexAxis: 'y', plugins: noLegend },
        });
    }

    function buildIncomeExpenseChart(canvas) {
        var c = colors();
        var datasets = income.map(function (r) {
            return stackDataset(r.name, 'income', incomeExpense.income.map(function (p) { return valueIn(p, r.name); }), c.income, c);
        }).concat(spending.map(function (r) {
            return stackDataset(r.name, 'expense', incomeExpense.expense.map(function (p) { return valueIn(p, r.name); }), c.expense, c);
        }));
        return new Chart(canvas, {
            type: 'bar',
            data: { labels: incomeExpense.periods, datasets: datasets },
            options: { scales: stackedScales, plugins: noLegend },
        });
    }

    function buildIncomeExpenseVsAverageChart(canvas) {
        if (!incomeExpenseVsAverage) return null;
        var c = colors();
        var d = incomeExpenseVsAverage;
        var labels = ['Income', 'Expense', 'Net'];
        var at = function (index, value) { return labels.map(function (_, i) { return i === index ? value : null; }); };
        var datasets = d.income.map(function (r) {
            return stackDataset(r.name, 'main', at(0, r.total), c.income, c);
        }).concat(d.expense.map(function (r) {
            return stackDataset(r.name, 'main', at(1, r.total), c.expense, c);
        })).concat([
            stackDataset('Net', 'main', at(2, d.current_net), c.net, c),
            averageMarkers([d.average_income, d.average_expense, d.average_net], c),
        ]);
        return new Chart(canvas, {
            type: 'bar',
            data: { labels: labels, datasets: datasets },
            options: { scales: stackedScales, plugins: noLegend },
        });
    }

    function buildNetWorthChart(canvas) {
        var c = colors();
        var flat = flatLineIfSingle(netWorthSeries.map(function (r) { return r.label; }), netWorthSeries.map(function (r) { return r.total; }));
        return new Chart(canvas, {
            type: 'line',
            data: {
                labels: flat.labels,
                datasets: [{ label: 'Net worth', data: flat.values, fill: true, tension: .25, borderColor: c.income, backgroundColor: c.income + '22', pointRadius: 4 }],
            },
            options: { plugins: noLegend },
        });
    }

    // Separate lines, not a stack - each chosen category keeps its own palette color, in order.
    function buildWatchedChart(canvas) {
        if (!watchedSeries.length) return null;
        var c = colors();
        var labels = watchedSeries[0].points.map(function (p) { return p.period; });
        return new Chart(canvas, {
            type: 'line',
            data: {
                labels: labels.length === 1 ? [labels[0], labels[0]] : labels,
                datasets: watchedSeries.map(function (series, i) {
                    var line = flatLineIfSingle(labels, series.points.map(function (p) { return p.total; }));
                    var color = i < c.palette.length ? c.palette[i] : c.muted;
                    return { label: series.name, data: line.values, fill: false, tension: .25, borderColor: color, backgroundColor: color, pointRadius: 4 };
                }),
            },
            options: { plugins: { legend: { display: watchedSeries.length > 1, position: 'bottom' } } },
        });
    }

    function buildCharts() {
        var c = colors();
        makeChart('spendingByCategoryChart', function (canvas) { return buildCategoryChart(canvas, spending, spendingStacked, c.expense); });
        makeChart('incomeByCategoryChart', function (canvas) { return buildCategoryChart(canvas, income, incomeStacked, c.income); });
        makeChart('spendingVsAverageChart', function (canvas) { return buildVsAverageChart(canvas, spendingVsAverage, c.expense); });
        makeChart('incomeVsAverageChart', function (canvas) { return buildVsAverageChart(canvas, incomeVsAverage, c.income); });
        makeChart('incomeExpenseChart', buildIncomeExpenseChart);
        makeChart('incomeExpenseVsAverageChart', buildIncomeExpenseVsAverageChart);
        makeChart('netWorthChart', buildNetWorthChart);
        makeChart('watchedExpensesChart', buildWatchedChart);
    }

    // Rebuild rather than patch colors in place after a light/dark switch - every color has to
    // be read from the new theme anyway.
    function rebuildCharts() {
        charts.forEach(function (chart) { chart.destroy(); });
        charts = [];
        buildCharts();
        if (expandedButton) sizeChartForExpand(expandedButton.closest('.report-col'));
    }

    // Help bubbles open on hover (and on focus, for keyboard and touch), one at a time, and
    // close again when the pointer leaves or anything else is clicked.
    function initPopovers() {
        var triggers = document.querySelectorAll('[data-bs-toggle="popover"]');
        triggers.forEach(function (el) { new bootstrap.Popover(el, { trigger: 'hover focus' }); });
        function hideAll(except) {
            triggers.forEach(function (el) {
                if (el === except) return;
                var popover = bootstrap.Popover.getInstance(el);
                if (popover) popover.hide();
            });
        }
        document.addEventListener('show.bs.popover', function (event) { hideAll(event.target); });
        document.addEventListener('click', function (event) {
            if (!event.target.closest('[data-bs-toggle="popover"]')) hideAll(null);
        });
    }

    function resizeCharts() {
        charts.forEach(function (chart) { chart.resize(); });
    }

    // Expanded: a horizontal-bar chart grows taller (a set height per bar); a column or line
    // chart gets a minimum width per column and scrolls sideways only when that doesn't fit.
    function sizeChartForExpand(reportCol) {
        var wrap = reportCol.querySelector('.report-chart-wrap');
        if (!wrap) return;
        var inner = wrap.querySelector('.report-chart-inner');
        var canvas = wrap.querySelector('canvas');
        var chart = charts.filter(function (ch) { return ch.canvas === canvas; })[0];
        if (!chart) return;
        var count = (chart.data.labels || []).length;
        if (chart.options.indexAxis === 'y') {
            wrap.style.height = Math.max(260, count * 34) + 'px';
            inner.style.minWidth = '';
        } else {
            wrap.style.height = '420px';
            inner.style.minWidth = Math.max(count * 60, 100) + 'px';
        }
    }

    function resetChartSize(reportCol) {
        var wrap = reportCol.querySelector('.report-chart-wrap');
        if (!wrap) return;
        wrap.style.height = '';
        wrap.querySelector('.report-chart-inner').style.minWidth = '';
    }

    function collapsibleRows(root) {
        return root.querySelectorAll('tr[data-cat-parent]');
    }

    function anyRowHidden(root) {
        return [].some.call(collapsibleRows(root), function (row) { return row.hidden; });
    }

    // The label follows the table's real state, so Net worth and Watched expenses - whose rows
    // start open - read "Hide all" rather than offering to show what's already shown.
    function refreshShowHide(root) {
        var button = root.querySelector('.report-showhide-btn');
        if (button) button.textContent = anyRowHidden(root) ? 'Show all' : 'Hide all';
    }

    // Only one report can be expanded at a time - the button that opened it closes it again.
    var expandedButton = null;

    function setButtonState(button, expanded) {
        button.querySelector('[data-expand-text]').textContent = expanded ? 'Collapse' : 'Expand';
        button.querySelector('[data-expand-icon]').className = expanded
            ? 'bi bi-fullscreen-exit me-1' : 'bi bi-arrows-fullscreen me-1';
    }

    function expand(reportCol, button) {
        document.querySelectorAll('.report-row').forEach(function (row) { row.hidden = !row.contains(reportCol); });
        document.querySelectorAll('.report-col').forEach(function (col) { col.hidden = col !== reportCol; });
        reportCol.classList.remove('col-md-6');
        reportCol.classList.add('col-12');
        var table = reportCol.querySelector('.report-table');
        if (table) table.hidden = false;
        var exportEl = reportCol.querySelector('.report-export');
        if (exportEl) exportEl.hidden = false;
        var showHide = reportCol.querySelector('.report-showhide');
        if (showHide) showHide.hidden = collapsibleRows(reportCol).length === 0;
        refreshShowHide(reportCol);
        setButtonState(button, true);
        expandedButton = button;
        sizeChartForExpand(reportCol);
        window.scrollTo({ top: 0, behavior: 'smooth' });
        requestAnimationFrame(resizeCharts);
    }

    function collapse() {
        document.querySelectorAll('.report-row').forEach(function (row) { row.hidden = false; });
        document.querySelectorAll('.report-col').forEach(function (col) {
            col.hidden = false;
            col.classList.remove('col-12');
            col.classList.add('col-md-6');
            resetChartSize(col);
        });
        document.querySelectorAll('.report-table').forEach(function (el) { el.hidden = true; });
        document.querySelectorAll('.report-export').forEach(function (el) { el.hidden = true; });
        document.querySelectorAll('.report-showhide').forEach(function (el) { el.hidden = true; });
        if (expandedButton) setButtonState(expandedButton, false);
        expandedButton = null;
        requestAnimationFrame(resizeCharts);
    }

    buildCharts();
    initPopovers();
    document.querySelectorAll('.report-card').forEach(refreshShowHide);
    document.addEventListener('stashy:themechange', rebuildCharts);

    document.querySelectorAll('.report-expand-btn').forEach(function (button) {
        button.addEventListener('click', function () {
            if (expandedButton === button) {
                collapse();
            } else {
                expand(button.closest('.report-col'), button);
            }
        });
    });

    // Opens or closes every subcategory row (on Net worth, every group's accounts) in this card.
    document.querySelectorAll('.report-showhide-btn').forEach(function (button) {
        button.addEventListener('click', function () {
            var card = button.closest('.report-card');
            var showAll = anyRowHidden(card);
            card.querySelectorAll('.report-toggle-icon').forEach(function (icon) {
                icon.classList.toggle('is-collapsed', !showAll);
                card.querySelectorAll('tr[data-cat-parent="' + icon.getAttribute('data-toggle-target') + '"]').forEach(function (row) {
                    row.hidden = !showAll;
                });
            });
            refreshShowHide(card);
        });
    });

    // One parent row's own subcategories (or one Net worth group's accounts).
    document.querySelectorAll('.report-toggle-icon').forEach(function (icon) {
        icon.addEventListener('click', function () {
            var collapsed = icon.classList.toggle('is-collapsed');
            document.querySelectorAll('tr[data-cat-parent="' + icon.getAttribute('data-toggle-target') + '"]').forEach(function (row) {
                row.hidden = collapsed;
            });
            refreshShowHide(icon.closest('.report-card'));
        });
    });
})();
