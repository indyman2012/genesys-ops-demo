(function () {
  const form = document.getElementById("filter-form");
  const rowsEl = document.getElementById("queue-rows");
  const errorBanner = document.getElementById("error-banner");
  const refreshBtn = document.getElementById("btn-refresh");
  const divisionSelect = document.getElementById("division-select");
  const daysSelect = document.getElementById("days-select");
  const startDateInput = document.getElementById("start-date");
  const endDateInput = document.getElementById("end-date");

  let volumeChart = null;
  let perfChart = null;
  let loading = false;

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function setError(message) {
    if (!message) {
      errorBanner.textContent = "";
      errorBanner.classList.add("hidden");
      return;
    }
    errorBanner.textContent = message;
    errorBanner.classList.remove("hidden");
  }

  function formatNumber(value) {
    if (value == null || value === "") return "—";
    return Number(value).toLocaleString();
  }

  function formatPct(value) {
    if (value == null || value === "") return "—";
    return Number(value).toFixed(1) + "%";
  }

  function formatSec(value) {
    if (value == null || value === "") return "—";
    return Number(value).toFixed(1) + "s";
  }

  function statusClass(status) {
    if (status === "metric-good") return "green";
    if (status === "metric-warning") return "yellow";
    if (status === "metric-bad") return "red";
    return "na";
  }

  function metricCell(value, status, formatter) {
    const display = formatter ? formatter(value) : formatNumber(value);
    return (
      '<td class="num ' + escapeHtml(status || "metric-na") + '">' +
      escapeHtml(display) +
      "</td>"
    );
  }

  function buildQuery() {
    const params = new URLSearchParams(new FormData(form));
    if (daysSelect.value) {
      params.set("days", daysSelect.value);
      params.delete("start_date");
      params.delete("end_date");
    }
    return params.toString();
  }

  function populateDivisions(divisions, selected) {
    const current = selected || divisionSelect.value || "";
    divisionSelect.innerHTML = '<option value="">All divisions</option>';
    (divisions || []).forEach(function (name) {
      const option = document.createElement("option");
      option.value = name;
      option.textContent = name;
      if (name === current) option.selected = true;
      divisionSelect.appendChild(option);
    });
  }

  function renderSummary(summary, queueCount) {
    document.getElementById("queue-count").textContent = formatNumber(queueCount);
    document.getElementById("summary-offered").textContent = formatNumber(summary.offered);
    document.getElementById("summary-sl").textContent = formatPct(summary.service_level_pct);
    document.getElementById("summary-abandon").textContent = formatPct(summary.abandon_pct);
    document.getElementById("summary-asa").textContent = formatSec(summary.asa_sec);
  }

  function renderQueues(queues) {
    if (!queues.length) {
      rowsEl.innerHTML =
        '<tr class="empty-row"><td colspan="13">No inbound voice traffic for the selected filters.</td></tr>';
      return;
    }

    rowsEl.innerHTML = queues
      .map(function (queue) {
        const statuses = queue.statuses || {};
        const overall = queue.overall_status || "green";
        return (
          "<tr>" +
          '<td><span class="pill ' + escapeHtml(overall) + '">' + escapeHtml(overall.toUpperCase()) + "</span></td>" +
          "<td>" + escapeHtml(queue.queue_name || "") + "</td>" +
          "<td>" + escapeHtml(queue.division_name || "—") + "</td>" +
          metricCell(queue.offered, "metric-na", formatNumber) +
          metricCell(queue.answered, "metric-na", formatNumber) +
          metricCell(queue.abandoned, "metric-na", formatNumber) +
          metricCell(queue.abandon_pct, statuses.abandon_pct, formatPct) +
          metricCell(queue.service_level_pct, statuses.service_level_pct, formatPct) +
          metricCell(queue.asa_sec, statuses.asa, formatSec) +
          metricCell(queue.avg_talk_sec, statuses.aht, formatSec) +
          metricCell(queue.avg_hold_sec, "metric-na", formatSec) +
          metricCell(queue.avg_acw_sec, statuses.acw, formatSec) +
          metricCell(queue.avg_handle_sec, "metric-na", formatSec) +
          "</tr>"
        );
      })
      .join("");
  }

  function destroyChart(chart) {
    if (chart) chart.destroy();
    return null;
  }

  function renderCharts(trend) {
    const labels = (trend || []).map(function (row) { return row.date; });
    const offered = trend.map(function (row) { return row.offered || 0; });
    const answered = trend.map(function (row) { return row.answered || 0; });
    const abandoned = trend.map(function (row) { return row.abandoned || 0; });
    const serviceLevel = trend.map(function (row) { return row.service_level_pct; });
    const abandonPct = trend.map(function (row) { return row.abandon_pct; });
    const asa = trend.map(function (row) { return row.asa_sec; });

    volumeChart = destroyChart(volumeChart);
    perfChart = destroyChart(perfChart);

    volumeChart = new Chart(document.getElementById("volume-chart"), {
      type: "line",
      data: {
        labels: labels,
        datasets: [
          { label: "Offered", data: offered, borderColor: "#1f4b99", backgroundColor: "rgba(31,75,153,0.08)", tension: 0.25 },
          { label: "Answered", data: answered, borderColor: "#1f6b3a", backgroundColor: "rgba(31,107,58,0.08)", tension: 0.25 },
          { label: "Abandoned", data: abandoned, borderColor: "#9b1c1c", backgroundColor: "rgba(155,28,28,0.08)", tension: 0.25 },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        scales: { y: { beginAtZero: true } },
      },
    });

    perfChart = new Chart(document.getElementById("perf-chart"), {
      type: "line",
      data: {
        labels: labels,
        datasets: [
          { label: "Service level %", data: serviceLevel, borderColor: "#1f6b3a", yAxisID: "y", tension: 0.25 },
          { label: "Abandon %", data: abandonPct, borderColor: "#9b1c1c", yAxisID: "y", tension: 0.25 },
          { label: "ASA (sec)", data: asa, borderColor: "#1f4b99", yAxisID: "y1", tension: 0.25 },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        scales: {
          y: { type: "linear", position: "left", beginAtZero: true, title: { display: true, text: "%" } },
          y1: { type: "linear", position: "right", beginAtZero: true, grid: { drawOnChartArea: false }, title: { display: true, text: "Seconds" } },
        },
      },
    });
  }

  function applyFiltersToForm(filters) {
    if (!filters) return;
    if (filters.start_date) startDateInput.value = filters.start_date;
    if (filters.end_date) endDateInput.value = filters.end_date;
    if (filters.queue_prefix != null) document.getElementById("queue-prefix").value = filters.queue_prefix;
    if (filters.target_service_level_pct != null) document.getElementById("target-sl").value = filters.target_service_level_pct;
    if (filters.target_asa != null) document.getElementById("target-asa").value = filters.target_asa;
    if (filters.target_aht != null) document.getElementById("target-aht").value = filters.target_aht;
    if (filters.target_acw != null) document.getElementById("target-acw").value = filters.target_acw;
    if (filters.target_abandon_pct != null) document.getElementById("target-abandon").value = filters.target_abandon_pct;
  }

  async function loadData() {
    if (loading) return;
    loading = true;
    setError("");
    rowsEl.innerHTML = '<tr class="empty-row"><td colspan="13">Loading…</td></tr>';

    try {
      const response = await fetch("/api/queue-performance?" + buildQuery(), {
        headers: { Accept: "application/json" },
      });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.error || "Failed to load queue performance data.");
      }

      applyFiltersToForm(payload.filters);
      populateDivisions(payload.divisions, payload.filters && payload.filters.division);
      document.getElementById("queue-count").textContent = formatNumber(payload.queue_count);
      renderSummary(payload.summary || {}, payload.queue_count);
      renderQueues(payload.queues || []);
      renderCharts(payload.trend || []);
      document.getElementById("last-updated").textContent = new Date().toLocaleString();
    } catch (err) {
      setError(err.message || String(err));
      rowsEl.innerHTML = '<tr class="empty-row"><td colspan="13">Unable to load data.</td></tr>';
    } finally {
      loading = false;
    }
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    loadData();
  });

  daysSelect.addEventListener("change", function () {
    if (daysSelect.value) {
      startDateInput.value = "";
      endDateInput.value = "";
    }
  });

  refreshBtn.addEventListener("click", loadData);

  loadData();
})();
