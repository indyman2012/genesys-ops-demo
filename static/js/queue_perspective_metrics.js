(function () {
    const baselinePlugin = {
        id: "baselinePlugin",
        afterDraw(chart, args, options) {
            if (!options || options.value === null || options.value === undefined) return;

            const yScale = chart.scales.y;
            if (!yScale) return;

            const y = yScale.getPixelForValue(Number(options.value));
            const area = chart.chartArea;
            const ctx = chart.ctx;

            if (!area || !Number.isFinite(y)) return;

            ctx.save();
            ctx.beginPath();
            ctx.setLineDash([6, 6]);
            ctx.lineWidth = 2;
            ctx.strokeStyle = "rgba(90, 70, 50, 0.75)";
            ctx.moveTo(area.left, y);
            ctx.lineTo(area.right, y);
            ctx.stroke();

            const label = options.label || "Baseline";
            ctx.setLineDash([]);
            ctx.font = "12px Arial";
            const pad = 6;
            const width = ctx.measureText(label).width + pad * 2;
            const height = 20;
            const x = area.right - width - 6;
            const boxY = Math.max(area.top + 2, y - height - 4);

            ctx.fillStyle = "rgba(247,243,237,0.94)";
            ctx.strokeStyle = "rgba(90,70,50,0.75)";
            ctx.fillRect(x, boxY, width, height);
            ctx.strokeRect(x, boxY, width, height);
            ctx.fillStyle = "rgba(60,45,30,0.95)";
            ctx.fillText(label, x + pad, boxY + 14);
            ctx.restore();
        }
    };

    if (window.Chart && !Chart.registry.plugins.get("baselinePlugin")) {
        Chart.register(baselinePlugin);
    }

    window.makeLineChart = function(canvasId, labels, datasets, baselineValue, baselineLabel) {
        const el = document.getElementById(canvasId);
        if (!el) return null;

        return new Chart(el, {
            type: "line",
            data: { labels, datasets },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    baselinePlugin: {
                        value: baselineValue,
                        label: baselineLabel
                    }
                }
            }
        });
    };
})();
