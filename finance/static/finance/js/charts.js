// Shared Chart.js setup: Stashy's number style, and colors that follow the light/dark theme.

function stashyCssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

// Read once: the viewer's number/date preferences (D10), set on <body> by base.html.
function stashyNumber(value, decimals) {
    const thousands = (document.body.dataset.thousandsSeparator || ' ').replace(/ /g, ' ');
    const decimalMark = document.body.dataset.decimalSeparator || '.';
    const formatted = new Intl.NumberFormat('en-US', { minimumFractionDigits: decimals, maximumFractionDigits: decimals })
        .format(value);
    const [whole, fraction] = formatted.split('.');
    const grouped = whole.replace(/,/g, thousands);
    return fraction ? grouped + decimalMark + fraction : grouped;
}

function stashyDate(isoDate) {
    const [year, month, day] = isoDate.split('-');
    const order = (document.body.dataset.dateFormat || 'd/m/Y').toLowerCase();
    if (order.indexOf('y') === 0) return year + '-' + month + '-' + day;
    if (order.indexOf('m') === 0) return month + '/' + day + '/' + year;
    return day + '/' + month + '/' + year;
}

(function () {
    if (!window.Chart) return;

    // Every chart on this site sits in an explicitly-sized wrapper div (e.g. .report-chart-wrap,
    // .donut-canvas) so it can be resized from JS (Phase 13's per-report expand sizing) or CSS
    // breakpoints - Chart.js only fills that wrapper's height when told not to preserve its own
    // aspect ratio instead.
    Chart.defaults.maintainAspectRatio = false;
    // Reports charts size their own wrapper from the data specifically so every category/period
    // label stays readable (Phase 13) - never thin them out automatically.
    Chart.defaults.scales.category.ticks.autoSkip = false;

    Chart.defaults.scales.linear.ticks.callback = function (value) {
        return stashyNumber(value, Number.isInteger(value) ? 0 : 2);
    };
    Chart.defaults.plugins.tooltip.callbacks.label = function (ctx) {
        const horizontal = ctx.chart.options.indexAxis === 'y';
        const value = typeof ctx.parsed === 'object' ? (horizontal ? ctx.parsed.x : ctx.parsed.y) : ctx.parsed;
        const name = ctx.dataset.label || ctx.label || '';
        return (name ? name + ': ' : '') + stashyNumber(value, 2);
    };

    function applyTheme() {
        Chart.defaults.color = stashyCssVar('--stashy-muted');
        Chart.defaults.borderColor = stashyCssVar('--stashy-line');
        Chart.defaults.font.family = stashyCssVar('--stashy-font-body');
        Object.values(Chart.instances).forEach(function (chart) {
            chart.update();
        });
    }

    applyTheme();
    document.addEventListener('stashy:themechange', applyTheme);
    if (document.fonts) document.fonts.ready.then(applyTheme);
})();
