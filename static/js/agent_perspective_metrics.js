(function () {
    window.makeLineChart = function(canvasId, labels, datasets) {
        const el = document.getElementById(canvasId);
        if (!el) return null;

        return new Chart(el, {
            type: "line",
            data: { labels, datasets },
            options: {
                responsive: true,
                maintainAspectRatio: false
            }
        });
    };

    window.filterQueueOptions = function() {
        const search = document.getElementById("queueSearch");
        const select = document.getElementById("queueSelect");
        if (!search || !select) return;

        const term = search.value.toLowerCase();

        for (let i = 0; i < select.options.length; i++) {
            const option = select.options[i];
            const text = option.text.toLowerCase();
            option.hidden = !text.includes(term);
        }
    };
})();
