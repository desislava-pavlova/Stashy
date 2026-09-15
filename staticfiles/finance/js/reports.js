// Reports page: builds the 6 report charts from the data embedded via json_script, and wires
// up "Expand" (show just this one report + its table, hide the rest) / "Back to all reports".
(function () {
    function readJson(id) {
        var el = document.getElementById(id);
        return el ? JSON.parse(el.textContent) : [];
    }

    var spendingByCategory = readJson('spending-chart-data');
    var spendingVsAverage = readJson('spending-vs-average-chart-data');
    var incomeExpenseByCategory = readJson('income-expense-chart-data');
    var incomeExpenseVsAverage = readJson('income-expense-vs-average-chart-data');
    var netWorthSeries = readJson('net-worth-chart-data');
    var billsData = readJson('bills-chart-data');

    var charts = [];

    function colors() {
        return {
            positive: stashyCssVar('--stashy-positive'),
            negative: stashyCssVar('--stashy-negative'),
            muted: stashyCssVar('--stashy-muted'),
        };
    }

    function signColors(values, c) {
        return values.map(function (v) { return v >= 0 ? c.positive : c.negative; });
    }

    function makeChart(id, build) {
        var canvas = document.getElementById(id);
        if (!canvas) return null;
        var chart = build(canvas);
        charts.push(chart);
        return chart;
    }

    function buildCharts() {
        var c = colors();

        var spendingChart = makeChart('spendingByCategoryChart', function (canvas) {
            return new Chart(canvas, {
                type: 'bar',
                data: {
                    labels: spendingByCategory.map(function (r) { return r.name; }),
                    datasets: [{ label: 'Spending', data: spendingByCategory.map(function (r) { return r.total; }), backgroundColor: c.negative, borderRadius: 6 }],
                },
                options: { indexAxis: 'y', plugins: { legend: { display: false } } },
            });
        });

        var spendingVsAverageChart = makeChart('spendingVsAverageChart', function (canvas) {
            return new Chart(canvas, {
                type: 'bar',
                data: {
                    labels: spendingVsAverage.map(function (r) { return r.name; }),
                    datasets: [
                        { label: 'Current', data: spendingVsAverage.map(function (r) { return r.current; }), backgroundColor: c.negative, borderRadius: 6 },
                        { label: 'Average', data: spendingVsAverage.map(function (r) { return r.average; }), backgroundColor: c.muted, borderRadius: 6 },
                    ],
                },
                options: { indexAxis: 'y', plugins: { legend: { position: 'bottom' } } },
            });
        });

        // Columns, not bars (Phase 11.1) - no indexAxis override, so these read top-to-bottom
        // as amount instead of left-to-right like the rest of the report charts.
        var incomeExpenseChart = makeChart('incomeExpenseByCategoryChart', function (canvas) {
            var data = incomeExpenseByCategory.map(function (r) { return r.total; });
            return new Chart(canvas, {
                type: 'bar',
                data: {
                    labels: incomeExpenseByCategory.map(function (r) { return r.name; }),
                    datasets: [{ label: 'Amount', data: data, backgroundColor: signColors(data, c), borderRadius: 6 }],
                },
                options: { plugins: { legend: { display: false } } },
            });
        });

        var incomeExpenseVsAverageChart = makeChart('incomeExpenseVsAverageChart', function (canvas) {
            var current = incomeExpenseVsAverage.map(function (r) { return r.current; });
            return new Chart(canvas, {
                type: 'bar',
                data: {
                    labels: incomeExpenseVsAverage.map(function (r) { return r.name; }),
                    datasets: [
                        { label: 'Current', data: current, backgroundColor: signColors(current, c), borderRadius: 6 },
                        { label: 'Average', data: incomeExpenseVsAverage.map(function (r) { return r.average; }), backgroundColor: c.muted, borderRadius: 6 },
                    ],
                },
                options: { plugins: { legend: { position: 'bottom' } } },
            });
        });

        var netWorthChart = makeChart('netWorthChart', function (canvas) {
            return new Chart(canvas, {
                type: 'line',
                data: {
                    labels: netWorthSeries.map(function (r) { return stashyDate(r.period); }),
                    datasets: [{
                        label: 'Net worth', data: netWorthSeries.map(function (r) { return r.net_worth; }),
                        fill: true, tension: .25, borderColor: c.positive, backgroundColor: c.positive + '22',
                    }],
                },
                options: { plugins: { legend: { display: false } } },
            });
        });

        // A monthly spending trend, like Net worth's own line, rather than a per-category
        // comparison (Phase 11.1: "should be lines like net worth").
        var billsChart = makeChart('billsChart', function (canvas) {
            return new Chart(canvas, {
                type: 'line',
                data: {
                    labels: billsData.map(function (r) { return stashyDate(r.period); }),
                    datasets: [{
                        label: 'Bills & Utilities', data: billsData.map(function (r) { return r.total; }),
                        fill: true, tension: .25, borderColor: c.negative, backgroundColor: c.negative + '22',
                    }],
                },
                options: { plugins: { legend: { display: false } } },
            });
        });

        return [spendingChart, spendingVsAverageChart, incomeExpenseChart, incomeExpenseVsAverageChart, netWorthChart, billsChart];
    }

    function colorReportCharts() {
        var c = colors();
        charts.forEach(function (chart) {
            if (!chart) return;
            chart.data.datasets.forEach(function (dataset) {
                if (Array.isArray(dataset.backgroundColor)) {
                    dataset.backgroundColor = dataset.backgroundColor.map(function (_, i) {
                        return dataset.data[i] >= 0 ? c.positive : c.negative;
                    });
                } else if (dataset.label === 'Average') {
                    dataset.backgroundColor = c.muted;
                } else if (dataset.label === 'Net worth') {
                    dataset.borderColor = c.positive;
                    dataset.backgroundColor = c.positive + '22';
                } else if (dataset.label === 'Bills & Utilities') {
                    dataset.borderColor = c.negative;
                    dataset.backgroundColor = c.negative + '22';
                } else if (dataset.label === 'Spending' || dataset.label === 'Current') {
                    dataset.backgroundColor = c.negative;
                }
            });
            chart.update();
        });
    }

    // A chart built while its own canvas was hidden (mid-expand of another report) can end
    // up stuck at zero size - nudging every chart after a layout change fixes that.
    function resizeCharts() {
        charts.forEach(function (chart) { if (chart) chart.resize(); });
    }

    buildCharts();
    document.addEventListener('stashy:themechange', colorReportCharts);

    // Only one report can be expanded at a time - the button that opened it is the one that
    // closes it again (relabeled "Collapse"), rather than a separate "Back" button elsewhere.
    var expandedButton = null;

    function setButtonState(button, expanded) {
        button.querySelector('[data-expand-text]').textContent = expanded ? 'Collapse' : 'Expand';
        button.querySelector('[data-expand-icon]').className = expanded
            ? 'bi bi-fullscreen-exit me-1' : 'bi bi-arrows-fullscreen me-1';
    }

    function expand(reportCol, button) {
        document.querySelectorAll('.report-row').forEach(function (row) {
            row.hidden = !row.contains(reportCol);
        });
        document.querySelectorAll('.report-col').forEach(function (col) {
            col.hidden = col !== reportCol;
        });
        reportCol.classList.remove('col-md-6');
        reportCol.classList.add('col-12');
        var table = reportCol.querySelector('.report-table');
        if (table) table.hidden = false;
        // Export only makes sense once you're actually looking at the one report it exports -
        // offering it on every collapsed card just invites exporting the wrong one by mistake.
        var exportEl = reportCol.querySelector('.report-export');
        if (exportEl) exportEl.hidden = false;
        setButtonState(button, true);
        expandedButton = button;
        window.scrollTo({ top: 0, behavior: 'smooth' });
        requestAnimationFrame(resizeCharts);
    }

    function collapse() {
        document.querySelectorAll('.report-row').forEach(function (row) { row.hidden = false; });
        document.querySelectorAll('.report-col').forEach(function (col) {
            col.hidden = false;
            col.classList.remove('col-12');
            col.classList.add('col-md-6');
            var table = col.querySelector('.report-table');
            if (table) table.hidden = true;
        });
        document.querySelectorAll('.report-export').forEach(function (el) { el.hidden = true; });
        if (expandedButton) setButtonState(expandedButton, false);
        expandedButton = null;
        requestAnimationFrame(resizeCharts);
    }

    document.querySelectorAll('.report-expand-btn').forEach(function (button) {
        button.addEventListener('click', function () {
            if (expandedButton === button) {
                collapse();
            } else {
                expand(button.closest('.report-col'), button);
            }
        });
    });

    // Show/hide a parent row's own subcategories (or, on Net worth, a group's own accounts) -
    // shared across every report table via the same data-cat-id/data-cat-parent convention,
    // each prefixed per-table so two tables' matching category ids never collide.
    document.querySelectorAll('.report-toggle-icon').forEach(function (icon) {
        icon.addEventListener('click', function () {
            var target = icon.getAttribute('data-toggle-target');
            var collapsed = icon.classList.toggle('is-collapsed');
            document.querySelectorAll('tr[data-cat-parent="' + target + '"]').forEach(function (row) {
                row.hidden = collapsed;
            });
        });
    });
})();
