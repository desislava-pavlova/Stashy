// Transactions list category filter: checking a main category also checks all of its own
// subcategories (one-directional - unchecking a subcategory by hand doesn't touch its parent).
(function () {
    document.querySelectorAll('.category-filter-menu').forEach(function (menu) {
        menu.querySelectorAll('input[type="checkbox"][data-category-id]').forEach(function (box) {
            if (box.dataset.parentId) return; // only main-category boxes drive their subcategories
            box.addEventListener('change', function () {
                menu.querySelectorAll('input[data-parent-id="' + box.dataset.categoryId + '"]').forEach(function (sub) {
                    sub.checked = box.checked;
                });
            });
        });
    });
})();
