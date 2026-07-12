(function () {
  const REFRESH_MS = 30000;
  const rowsEl = document.getElementById("agent-rows");
  const countEl = document.getElementById("agent-count");
  const lastUpdatedEl = document.getElementById("last-updated");
  const nextRefreshEl = document.getElementById("next-refresh");
  const queryMsEl = document.getElementById("query-ms");
  const errorBanner = document.getElementById("error-banner");
  const refreshBtn = document.getElementById("btn-refresh");

  let countdown = REFRESH_MS / 1000;
  let countdownTimer = null;
  let refreshTimer = null;
  let loading = false;

  function formatLocalTime(iso) {
    if (!iso) return "—";
    const dt = new Date(iso);
    if (Number.isNaN(dt.getTime())) return iso;
    return dt.toLocaleString();
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

  function renderAgents(agents) {
    if (!agents.length) {
      rowsEl.innerHTML =
        '<tr class="all-clear"><td colspan="5">No agents in Not Responding for over 5 minutes</td></tr>';
      return;
    }

    rowsEl.innerHTML = agents
      .map(function (agent) {
        return (
          "<tr>" +
          "<td>" + escapeHtml(agent.name || agent.user_id) + "</td>" +
          "<td>" + escapeHtml(agent.email || "—") + "</td>" +
          "<td>" + escapeHtml(formatLocalTime(agent.start_time)) + "</td>" +
          '<td class="duration">' + escapeHtml(agent.duration_display || "") + "</td>" +
          '<td><code>' + escapeHtml(agent.user_id) + "</code></td>" +
          "</tr>"
        );
      })
      .join("");
  }

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function resetCountdown() {
    countdown = REFRESH_MS / 1000;
    nextRefreshEl.textContent = countdown + "s";
    if (countdownTimer) clearInterval(countdownTimer);
    countdownTimer = setInterval(function () {
      countdown -= 1;
      if (countdown <= 0) countdown = REFRESH_MS / 1000;
      nextRefreshEl.textContent = countdown + "s";
    }, 1000);
  }

  async function loadData() {
    if (loading) return;
    loading = true;
    refreshBtn.disabled = true;
    try {
      const response = await fetch("/api/not-responding");
      const payload = await response.json();
      if (!response.ok || payload.error) {
        throw new Error(payload.error || "Request failed");
      }
      setError("");
      countEl.textContent = String(payload.count || 0);
      countEl.classList.toggle("alert", (payload.count || 0) > 0);
      lastUpdatedEl.textContent = formatLocalTime(payload.generated_at);
      queryMsEl.textContent = (payload.query_ms || 0) + " ms";
      renderAgents(payload.agents || []);
      resetCountdown();
    } catch (err) {
      setError("Failed to refresh: " + err.message);
      rowsEl.innerHTML =
        '<tr class="empty-row"><td colspan="5">Unable to load data</td></tr>';
    } finally {
      loading = false;
      refreshBtn.disabled = false;
    }
  }

  refreshBtn.addEventListener("click", function () {
    loadData();
  });

  loadData();
  refreshTimer = setInterval(loadData, REFRESH_MS);
})();
