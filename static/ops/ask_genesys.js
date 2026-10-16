(function () {
  const form = document.getElementById("ask-form");
  const promptEl = document.getElementById("ask-prompt");
  const submitBtn = document.getElementById("ask-submit");
  const errorEl = document.getElementById("ask-error");
  const resultEl = document.getElementById("ask-result");
  const answerEl = document.getElementById("ask-answer");
  const routeEl = document.getElementById("ask-route");
  const helpList = document.getElementById("ask-examples-list");
  const tableWrap = document.getElementById("ask-table-wrap");
  const thead = document.getElementById("ask-thead");
  const tbody = document.getElementById("ask-tbody");
  const csvLink = document.getElementById("ask-csv");
  const jsonToggle = document.getElementById("ask-json-toggle");
  const jsonEl = document.getElementById("ask-json");
  const advancedEl = document.getElementById("ask-advanced"); // null when the user lacks access
  const statusEl = document.getElementById("ask-status");
  const statusText = document.getElementById("ask-status-text");
  const advResult = document.getElementById("ask-adv-result");
  const advBadge = document.getElementById("ask-adv-badge");
  const scriptEl = document.getElementById("ask-script");
  const scriptFile = document.getElementById("ask-script-file");
  const execOut = document.getElementById("ask-exec-out");
  const execErr = document.getElementById("ask-exec-err");
  const execErrHeading = document.getElementById("ask-exec-err-heading");

  const STANDARD_MAX = 500;
  const ADVANCED_MAX = 1500;

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

  function isAdvanced() {
    return !!(advancedEl && advancedEl.checked);
  }

  function setBusy(busy, text) {
    submitBtn.disabled = busy;
    if (advancedEl) advancedEl.disabled = busy;
    submitBtn.textContent = busy ? "Working…" : "Ask";
    if (statusText && text) statusText.textContent = text;
    if (statusEl) statusEl.classList.toggle("hidden", !busy);
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

  function renderRoute(result) {
    const p = result.parsed_intent;
    if (!p || result.advanced) {
      routeEl.classList.add("hidden");
      return;
    }
    routeEl.textContent =
      p.source === "llm"
        ? "Understood by AI (" + (p.model || "OpenAI") + ") as: " + p.intent
        : "Understood by keyword matching as: " + p.intent;
    routeEl.classList.remove("hidden");
  }

  function renderAdvanced(result) {
    if (!result.advanced) {
      advResult.classList.add("hidden");
      return;
    }
    advResult.classList.remove("hidden");
    const safety = result.safety || {};
    const exec = result.execution;
    let badge;
    let cls;
    if (!safety.passed) {
      badge = "BLOCKED FOR SAFETY — script was not executed.";
      cls = "ask-adv-fail";
    } else if (exec && exec.timed_out) {
      badge = "Safety check passed · execution timed out.";
      cls = "ask-adv-fail";
    } else if (exec && exec.returncode === 0) {
      badge = "Safety check passed · executed successfully (" + exec.duration_sec + "s).";
      cls = "ask-adv-ok";
    } else {
      badge = "Safety check passed · script failed (exit " + (exec ? exec.returncode : "?") + ").";
      cls = "ask-adv-fail";
    }
    advBadge.textContent = badge;
    advBadge.className = "ask-adv-badge " + cls;

    scriptEl.textContent = result.script || "";
    scriptFile.textContent = result.script_file ? "· saved: " + result.script_file : "";
    const violations = safety.violations || [];
    let outText = "";
    if (!safety.passed) {
      outText = "Blocked for safety:\n - " + violations.join("\n - ");
    } else if (exec) {
      outText = exec.stdout || "(no output)";
    }
    execOut.textContent = outText;
    const stderr = exec && exec.stderr ? exec.stderr : "";
    execErr.textContent = stderr;
    execErr.classList.toggle("hidden", !stderr);
    execErrHeading.classList.toggle("hidden", !stderr);
    document.getElementById("ask-script-details").open = !safety.passed;
  }

  function renderResult(result) {
    resultEl.classList.remove("hidden");
    answerEl.textContent = result.answer || "";
    const examples = result.examples || [];
    helpList.innerHTML = examples.map(function (e) { return "<li>" + escapeHtml(e) + "</li>"; }).join("");
    helpList.classList.toggle("hidden", !examples.length);
    renderTable(result);
    renderRoute(result);
    renderAdvanced(result);
    if (result.csv_url) {
      csvLink.href = result.csv_url;
      csvLink.classList.remove("hidden");
    } else {
      csvLink.classList.add("hidden");
    }
    jsonEl.textContent = JSON.stringify(result, null, 2); // includes parsed_intent for transparency
    jsonEl.classList.add("hidden");
    jsonToggle.textContent = "View raw JSON";
  }

  async function ask(prompt) {
    setError("");
    const advanced = isAdvanced();
    setBusy(true, advanced ? "Thinking… (writing script)" : "Thinking…");
    // Advanced requests run a script server-side: flip the label once generation is likely done.
    const phase = advanced ? setTimeout(function () { statusText.textContent = "Running query…"; }, 6000) : null;
    try {
      const response = await fetch("/api/ask-genesys", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt: prompt, advanced: advanced }),
      });
      const payload = await response.json().catch(function () { return {}; });
      if (response.status === 401 && payload.login_url) {
        window.location.href = payload.login_url;
        return;
      }
      if (!response.ok || (payload.error && !payload.script)) {
        throw new Error(payload.error || "Request failed (HTTP " + response.status + ")");
      }
      renderResult(payload);
    } catch (err) {
      setError("Could not answer: " + err.message);
    } finally {
      if (phase) clearTimeout(phase);
      setBusy(false);
    }
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    const prompt = promptEl.value.trim();
    if (prompt) ask(prompt);
  });

  if (advancedEl) {
    advancedEl.addEventListener("change", function () {
      promptEl.maxLength = advancedEl.checked ? ADVANCED_MAX : STANDARD_MAX;
      form.classList.toggle("ask-form-advanced", advancedEl.checked);
      submitBtn.textContent = "Ask";
    });
  }

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
