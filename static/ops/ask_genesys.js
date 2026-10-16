(function () {
  const form = document.getElementById("ask-form");
  const promptEl = document.getElementById("ask-prompt");
  const submitBtn = document.getElementById("ask-submit");
  const errorEl = document.getElementById("ask-error");
  const resultEl = document.getElementById("ask-result");
  const answerEl = document.getElementById("ask-answer");
  const helpList = document.getElementById("ask-examples-list");
  const tableWrap = document.getElementById("ask-table-wrap");
  const thead = document.getElementById("ask-thead");
  const tbody = document.getElementById("ask-tbody");
  const csvLink = document.getElementById("ask-csv");
  const jsonToggle = document.getElementById("ask-json-toggle");
  const jsonEl = document.getElementById("ask-json");

  // Columns shown per intent (full row data stays in CSV / raw JSON).
  const COLUMNS = {
    last_inbound_call: [
      ["conversationStartLocal", "Start (ET)"],
      ["direction", "Direction"],
      ["queueName", "Queue"],
      ["agentName", "Agent"],
      ["outcome", "Outcome"],
      ["talkSec", "Talk (s)"],
      ["conversationId", "Conversation ID"],
    ],
    user_queues_joined: [
      ["queueName", "Queue"],
      ["joined", "Joined"],
      ["queueId", "Queue ID"],
    ],
  };

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function setError(message) {
    errorEl.textContent = message || "";
    errorEl.classList.toggle("hidden", !message);
  }

  function renderTable(result) {
    const rows = result.rows || [];
    let cols = COLUMNS[result.intent];
    if (result.meta && result.meta.ambiguous) {
      cols = [["name", "Name"], ["email", "Email"], ["userId", "User ID"]];
    }
    if (!rows.length || !cols) {
      tableWrap.classList.add("hidden");
      return;
    }
    thead.innerHTML = "<tr>" + cols.map(function (c) { return "<th>" + escapeHtml(c[1]) + "</th>"; }).join("") + "</tr>";
    tbody.innerHTML = rows
      .map(function (row) {
        return "<tr>" + cols.map(function (c) { return "<td>" + escapeHtml(row[c[0]]) + "</td>"; }).join("") + "</tr>";
      })
      .join("");
    tableWrap.classList.remove("hidden");
  }

  function renderResult(result) {
    resultEl.classList.remove("hidden");
    answerEl.textContent = result.answer || "";
    const examples = result.examples || [];
    helpList.innerHTML = examples.map(function (e) { return "<li>" + escapeHtml(e) + "</li>"; }).join("");
    helpList.classList.toggle("hidden", !examples.length);
    renderTable(result);
    if (result.csv_url) {
      csvLink.href = result.csv_url;
      csvLink.classList.remove("hidden");
    } else {
      csvLink.classList.add("hidden");
    }
    jsonEl.textContent = JSON.stringify(result, null, 2);
    jsonEl.classList.add("hidden");
    jsonToggle.textContent = "View raw JSON";
  }

  async function ask(prompt) {
    setError("");
    submitBtn.disabled = true;
    submitBtn.textContent = "Asking…";
    try {
      const response = await fetch("/api/ask-genesys", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt: prompt }),
      });
      const payload = await response.json().catch(function () { return {}; });
      if (response.status === 401 && payload.login_url) {
        window.location.href = payload.login_url;
        return;
      }
      if (!response.ok || payload.error) {
        throw new Error(payload.error || "Request failed (HTTP " + response.status + ")");
      }
      renderResult(payload);
    } catch (err) {
      setError("Could not answer: " + err.message);
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = "Ask";
    }
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    const prompt = promptEl.value.trim();
    if (prompt) ask(prompt);
  });

  document.querySelectorAll(".ask-chip").forEach(function (chip) {
    chip.addEventListener("click", function () {
      promptEl.value = chip.getAttribute("data-example") || "";
      promptEl.focus();
    });
  });

  jsonToggle.addEventListener("click", function () {
    const hidden = jsonEl.classList.toggle("hidden");
    jsonToggle.textContent = hidden ? "View raw JSON" : "Hide raw JSON";
  });
})();
