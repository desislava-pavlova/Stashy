// Shared Chart.js setup: Stashy's number style, and colors that follow the light/dark theme.

function stashyCssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

// The viewer's number format (D10), set on <body> by base.html and read at each call.
function stashyNumber(value, decimals) {
    const thousands = (document.body.dataset.thousandsSeparator || ' ').replace(/ /g, ' ');
    const decimalMark = document.body.dataset.decimalSeparator || '.';
    const formatted = new Intl.NumberFormat('en-US', { minimumFractionDigits: decimals, maximumFractionDigits: decimals })
        .format(value);
    const [whole, fraction] = formatted.split('.');
    const grouped = whole.replace(/,/g, thousands);
    return fraction ? grouped + decimalMark + fraction : grouped;
}

(function () {
    if (!window.Chart) return;

    // Every chart on this site sits in an explicitly-sized wrapper div (e.g. .report-chart-wrap,
    // .donut-canvas) so it can be resized from JS (each report card sizes its own wrapper) or
    // CSS breakpoints - Chart.js only fills that wrapper's height when told not to preserve its own
    // aspect ratio instead.
    Chart.defaults.maintainAspectRatio = false;
    // Reports charts size their own wrapper from the data specifically so every category/period
    // label stays readable - never thin them out automatically.
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
