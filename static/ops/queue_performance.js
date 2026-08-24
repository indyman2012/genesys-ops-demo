(function () {
  const form = document.getElementById("filter-form");
  const rowsEl = document.getElementById("queue-rows");
  const agentRowsEl = document.getElementById("agent-rows");
  const errorBanner = document.getElementById("error-banner");
  const refreshBtn = document.getElementById("btn-refresh");
  const exportPdfBtn = document.getElementById("btn-export-pdf");
  const exportExcelBtn = document.getElementById("btn-export-excel");
  const reportPeriodEl = document.getElementById("report-period");
  const backBtn = document.getElementById("btn-back");
  const divisionSelect = document.getElementById("division-select");
  const daysSelect = document.getElementById("days-select");
  const startDateInput = document.getElementById("start-date");
  const endDateInput = document.getElementById("end-date");
  const overviewEl = document.getElementById("qp-overview");
  const detailEl = document.getElementById("qp-detail");

  let volumeChart = null;
  let perfChart = null;
  let detailVolumeChart = null;
  let detailPerfChart = null;
  let loading = false;
  let selectedQueueId = null;
  let selectedQueueName = "";

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
    return formatMmSs(value);
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

  function renderReportPeriod(filters) {
    if (!filters || !reportPeriodEl) return;
    const parts = [];
    if (filters.start_date && filters.end_date) {
      parts.push(filters.start_date + " through " + filters.end_date);
    }
    if (filters.queue_prefix) parts.push("Prefix: " + filters.queue_prefix);
    if (filters.division) parts.push("Division: " + filters.division);
    if (!parts.length) {
      reportPeriodEl.classList.add("hidden");
      reportPeriodEl.textContent = "";
      return;
    }
    reportPeriodEl.textContent = parts.join(" · ");
    reportPeriodEl.classList.remove("hidden");
  }

  function formatMmSs(value) {
    if (value == null || value === "") return "—";
    const total = Math.max(0, Math.round(Number(value)));
    if (!Number.isFinite(total)) return "—";
    const minutes = Math.floor(total / 60);
    const seconds = total % 60;
    return minutes + ":" + String(seconds).padStart(2, "0");
  }

  function chartTrendData(payload) {
    return payload.trend_90d || payload.trend || [];
  }

  function chartDateLabels(trend) {
    return (trend || []).map(function (row) {
      const date = row.date || "";
      return date.length >= 10 ? date.slice(5) : date;
    });
  }

  function exportUrl(path) {
    const params = new URLSearchParams(buildQuery());
    if (selectedQueueId) params.set("queue_id", selectedQueueId);
    return path + "?" + params.toString();
  }

  function exportPdfUrl() {
    return exportUrl("/api/queue-performance/export.pdf");
  }

  function exportExcelUrl() {
    return exportUrl("/api/queue-performance/export.xlsx");
  }

  function triggerDownload(button, idleLabel, url) {
    if (loading) return;
    setError("");
    button.disabled = true;
    button.textContent = "Exporting…";
    window.location.href = url;
    window.setTimeout(function () {
      button.disabled = false;
      button.textContent = idleLabel;
    }, 2500);
  }

  function exportPdf() {
    triggerDownload(exportPdfBtn, "Export PDF", exportPdfUrl());
  }

  function exportExcel() {
    triggerDownload(exportExcelBtn, "Export Excel", exportExcelUrl());
  }

  function renderSummary(summary, queueCount) {
    document.getElementById("queue-count").textContent = formatNumber(queueCount);
    document.getElementById("summary-offered").textContent = formatNumber(summary.offered);
    document.getElementById("summary-sl").textContent = formatPct(summary.service_level_pct);
    document.getElementById("summary-abandon").textContent = formatPct(summary.abandon_pct);
    document.getElementById("summary-asa").textContent = formatSec(summary.asa_sec);
  }

  function renderMetricRowCells(row, primaryKey, secondaryKey) {
    const statuses = row.statuses || {};
    const overall = row.overall_status || "green";
    let html =
      '<td><span class="pill ' + escapeHtml(overall) + '">' + escapeHtml(overall.toUpperCase()) + "</span></td>" +
      "<td>" + escapeHtml(row[primaryKey] || "") + "</td>";
    if (secondaryKey) {
      html += "<td>" + escapeHtml(row[secondaryKey] || "—") + "</td>";
    }
    html +=
      metricCell(row.offered, "metric-na", formatNumber) +
      metricCell(row.answered, "metric-na", formatNumber) +
      metricCell(row.abandoned, "metric-na", formatNumber) +
      metricCell(row.abandon_pct, statuses.abandon_pct, formatPct) +
      metricCell(row.service_level_pct, statuses.service_level_pct, formatPct) +
      metricCell(row.asa_sec, statuses.asa, formatSec) +
      metricCell(row.avg_talk_sec, statuses.aht, formatSec) +
      metricCell(row.avg_hold_sec, "metric-na", formatSec) +
      metricCell(row.avg_acw_sec, statuses.acw, formatSec) +
      metricCell(row.avg_handle_sec, "metric-na", formatSec);
    return html;
  }

  function renderQueues(queues) {
    if (!queues.length) {
      rowsEl.innerHTML =
        '<tr class="empty-row"><td colspan="13">No inbound voice traffic for the selected filters.</td></tr>';
      return;
    }

    rowsEl.innerHTML = queues
      .map(function (queue) {
        return (
          '<tr class="queue-row" tabindex="0" role="button" data-queue-id="' +
          escapeHtml(queue.queue_id || "") +
          '" data-queue-name="' +
          escapeHtml(queue.queue_name || "") +
          '" title="Click to view queue detail">' +
          renderMetricRowCells(queue, "queue_name", "division_name") +
          "</tr>"
        );
      })
      .join("");
  }

  function renderAgents(agents) {
    if (!agents.length) {
      agentRowsEl.innerHTML =
        '<tr class="empty-row"><td colspan="12">No agent activity on this queue for the selected window.</td></tr>';
      return;
    }

    agentRowsEl.innerHTML = agents
      .map(function (agent) {
        return "<tr>" + renderMetricRowCells(agent, "user_name", null) + "</tr>";
      })
      .join("");
  }

  function destroyChart(chart) {
    if (chart) chart.destroy();
    return null;
  }

  function renderCharts(trend, volumeCanvasId, perfCanvasId) {
    const volumeCanvas = document.getElementById(volumeCanvasId);
    const perfCanvas = document.getElementById(perfCanvasId);
    const labels = chartDateLabels(trend);
    const offered = (trend || []).map(function (row) { return row.offered || 0; });
    const answered = (trend || []).map(function (row) { return row.answered || 0; });
    const abandoned = (trend || []).map(function (row) { return row.abandoned || 0; });
    const serviceLevel = (trend || []).map(function (row) { return row.service_level_pct; });
    const abandonPct = (trend || []).map(function (row) { return row.abandon_pct; });
    const asa = (trend || []).map(function (row) { return row.asa_sec; });

    const nextVolume = new Chart(volumeCanvas, {
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

    const nextPerf = new Chart(perfCanvas, {
      type: "line",
      data: {
        labels: labels,
        datasets: [
          { label: "Service level %", data: serviceLevel, borderColor: "#1f6b3a", yAxisID: "y", tension: 0.25 },
          { label: "Abandon %", data: abandonPct, borderColor: "#9b1c1c", yAxisID: "y", tension: 0.25 },
          { label: "ASA", data: asa, borderColor: "#1f4b99", yAxisID: "y1", tension: 0.25 },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          tooltip: {
            callbacks: {
              label: function (context) {
                let label = context.dataset.label || "";
                if (label) label += ": ";
                if (context.dataset.yAxisID === "y1") {
                  label += formatMmSs(context.parsed.y);
                } else if (context.parsed.y != null) {
                  label += Number(context.parsed.y).toFixed(1) + "%";
                }
                return label;
              },
            },
          },
        },
        scales: {
          y: { type: "linear", position: "left", beginAtZero: true, title: { display: true, text: "%" } },
          y1: {
            type: "linear",
            position: "right",
            beginAtZero: true,
            grid: { drawOnChartArea: false },
            title: { display: true, text: "MM:SS" },
            ticks: {
              callback: function (value) {
                return formatMmSs(value);
              },
            },
          },
        },
      },
    });

    return { volume: nextVolume, perf: nextPerf };
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

  function showOverview() {
    selectedQueueId = null;
    selectedQueueName = "";
    overviewEl.classList.remove("hidden");
    detailEl.classList.add("hidden");
    document.getElementById("page-subtitle").textContent =
      "Inbound voice queue metrics with health color coding — click a queue to drill down by agent.";
    if (history.state && history.state.view === "detail") {
      history.pushState({ view: "overview" }, "", window.location.pathname + window.location.search);
    }
  }

  function showDetailView(queueId, queueName) {
    selectedQueueId = queueId;
    selectedQueueName = queueName || "Queue detail";
    overviewEl.classList.add("hidden");
    detailEl.classList.remove("hidden");
    document.getElementById("page-subtitle").textContent =
      "Queue drill-down — trends and per-agent metrics for the selected date range.";
    document.getElementById("detail-queue-name").textContent = selectedQueueName;
    history.pushState({ view: "detail", queueId: queueId }, "", window.location.pathname + window.location.search);
  }

  function renderDetailSummary(queue, agentCount) {
    document.getElementById("detail-agent-count").textContent = formatNumber(agentCount);
    document.getElementById("detail-offered").textContent = formatNumber(queue.offered);
    document.getElementById("detail-sl").textContent = formatPct(queue.service_level_pct);
    document.getElementById("detail-abandon").textContent = formatPct(queue.abandon_pct);
    document.getElementById("detail-asa").textContent = formatSec(queue.asa_sec);
    const overall = queue.overall_status || "green";
    document.getElementById("detail-health").innerHTML =
      '<span class="pill ' + escapeHtml(overall) + '">' + escapeHtml(overall.toUpperCase()) + "</span>";
    const division = queue.division_name ? " · " + queue.division_name : "";
    document.getElementById("detail-queue-meta").textContent =
      (queue.offered != null ? formatNumber(queue.offered) + " offered" : "") + division;
  }

  async function handleAuthFailure(response, payload) {
    if (response.status === 401 && payload && payload.login_url) {
      window.location.href = payload.login_url;
      return true;
    }
    if (response.status === 403) {
      throw new Error(
        (payload && payload.error) ||
          "Access denied. Queue Performance requires Supervisor, Admin, or Super Admin."
      );
    }
    return false;
  }

  async function loadQueueDetail(queueId, queueName) {
    if (loading) return;
    loading = true;
    setError("");
    showDetailView(queueId, queueName);
    agentRowsEl.innerHTML = '<tr class="empty-row"><td colspan="12">Loading agents…</td></tr>';

    try {
      const response = await fetch(
        "/api/queue-performance/queues/" + encodeURIComponent(queueId) + "?" + buildQuery(),
        { headers: { Accept: "application/json" } }
      );
      const payload = await response.json();
      if (await handleAuthFailure(response, payload)) return;
      if (!response.ok) {
        throw new Error(payload.error || "Failed to load queue detail.");
      }

      applyFiltersToForm(payload.filters);
      renderReportPeriod(payload.filters);
      const queue = payload.queue || {};
      document.getElementById("detail-queue-name").textContent = queue.queue_name || queueName;
      renderDetailSummary(queue, payload.agent_count || 0);
      renderAgents(payload.agents || []);
      detailVolumeChart = destroyChart(detailVolumeChart);
      detailPerfChart = destroyChart(detailPerfChart);
      const detailCharts = renderCharts(chartTrendData(payload), "detail-volume-chart", "detail-perf-chart");
      detailVolumeChart = detailCharts.volume;
      detailPerfChart = detailCharts.perf;
      document.getElementById("last-updated").textContent = new Date().toLocaleString();
    } catch (err) {
      setError(err.message || String(err));
      agentRowsEl.innerHTML = '<tr class="empty-row"><td colspan="12">Unable to load queue detail.</td></tr>';
    } finally {
      loading = false;
    }
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
      if (await handleAuthFailure(response, payload)) return;
      if (!response.ok) {
        throw new Error(payload.error || "Failed to load queue performance data.");
      }

      applyFiltersToForm(payload.filters);
      renderReportPeriod(payload.filters);
      populateDivisions(payload.divisions, payload.filters && payload.filters.division);
      document.getElementById("queue-count").textContent = formatNumber(payload.queue_count);
      renderSummary(payload.summary || {}, payload.queue_count);
      renderQueues(payload.queues || []);
      volumeChart = destroyChart(volumeChart);
      perfChart = destroyChart(perfChart);
      const charts = renderCharts(chartTrendData(payload), "volume-chart", "perf-chart");
      volumeChart = charts.volume;
      perfChart = charts.perf;
      document.getElementById("last-updated").textContent = new Date().toLocaleString();

      if (selectedQueueId) {
        const stillVisible = (payload.queues || []).some(function (q) {
          return q.queue_id === selectedQueueId;
        });
        if (stillVisible) {
          await loadQueueDetail(selectedQueueId, selectedQueueName);
        } else {
          showOverview();
        }
      }
    } catch (err) {
      setError(err.message || String(err));
      rowsEl.innerHTML = '<tr class="empty-row"><td colspan="13">Unable to load data.</td></tr>';
    } finally {
      loading = false;
    }
  }

  rowsEl.addEventListener("click", function (event) {
    const row = event.target.closest(".queue-row");
    if (!row) return;
    const queueId = row.getAttribute("data-queue-id");
    if (!queueId) return;
    loadQueueDetail(queueId, row.getAttribute("data-queue-name") || "");
  });

  rowsEl.addEventListener("keydown", function (event) {
    if (event.key !== "Enter" && event.key !== " ") return;
    const row = event.target.closest(".queue-row");
    if (!row) return;
    event.preventDefault();
    const queueId = row.getAttribute("data-queue-id");
    if (!queueId) return;
    loadQueueDetail(queueId, row.getAttribute("data-queue-name") || "");
  });

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    if (selectedQueueId) {
      loadQueueDetail(selectedQueueId, selectedQueueName);
    } else {
      loadData();
    }
  });

  daysSelect.addEventListener("change", function () {
    if (daysSelect.value) {
      startDateInput.value = "";
      endDateInput.value = "";
    }
  });

  refreshBtn.addEventListener("click", function () {
    if (selectedQueueId) {
      loadQueueDetail(selectedQueueId, selectedQueueName);
    } else {
      loadData();
    }
  });

  exportPdfBtn.addEventListener("click", exportPdf);
  exportExcelBtn.addEventListener("click", exportExcel);

  backBtn.addEventListener("click", showOverview);

  window.addEventListener("popstate", function () {
    if (history.state && history.state.view === "detail" && history.state.queueId) {
      loadQueueDetail(history.state.queueId, selectedQueueName);
    } else {
      showOverview();
    }
  });

  loadData();
})();
