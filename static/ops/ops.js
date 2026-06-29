const toolSelect = document.getElementById("tool-select");
const toolForm = document.getElementById("tool-form");
const fieldsContainer = document.getElementById("fields-container");
const toolDescription = document.getElementById("tool-description");
const toolHelp = document.getElementById("tool-help");
const toolWarning = document.getElementById("tool-warning");
const placeholder = document.getElementById("placeholder");
const executeSection = document.getElementById("execute-section");
const executeMode = document.getElementById("execute-mode");
const executeModeHidden = document.getElementById("execute-mode-hidden");
const executeWarning = document.getElementById("execute-warning");
const confirmField = document.getElementById("confirm-field");
const btnRunExecute = document.getElementById("btn-run-execute");
const logEl = document.getElementById("log");
const clearBeforeRun = document.getElementById("clear-before-run");

const DEMO_MODE = document.documentElement.dataset.demoMode === "1";

let currentTool = null;

function clearLog() {
  logEl.innerHTML = "";
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

function appendLog(text) {
  for (const line of text.split("\n")) {
    if (!line && text.endsWith("\n")) continue;
    const span = document.createElement("span");
    span.textContent = line + "\n";
    const lower = line.toLowerCase();
    if (line.includes("[ERROR]") || lower.includes("failed") || lower.includes("fatal")) {
      span.className = "line-error";
    } else if (line.includes("[WARN]")) {
      span.className = "line-warn";
    } else if (line.includes("[OK]") || line.includes("[CREATED]") || line.includes("[UPDATED]")) {
      span.className = "line-ok";
    } else if (line.includes("[DRY RUN]") || lower.includes("dry run")) {
      span.className = "line-dry";
    } else {
      span.className = "line-info";
    }
    logEl.appendChild(span);
  }
  logEl.scrollTop = logEl.scrollHeight;
}

function setExecuteUi(enabled) {
  if (!executeMode || !executeModeHidden) return;
  executeMode.checked = enabled;
  executeModeHidden.value = enabled ? "1" : "0";
  if (executeWarning) executeWarning.classList.toggle("hidden", !enabled);
  if (confirmField) confirmField.classList.toggle("hidden", !enabled);
  if (btnRunExecute) btnRunExecute.classList.toggle("hidden", !enabled);
  const confirmInput = document.getElementById("execute-confirm");
  if (!enabled && confirmInput) confirmInput.value = "";
}

function renderField(field) {
  const wrap = document.createElement("label");
  wrap.dataset.field = field.name;

  const label = document.createElement("span");
  label.textContent = field.label + (field.required ? " *" : "");
  wrap.appendChild(label);

  let input;
  if (field.type === "file") {
    input = document.createElement("input");
    input.type = "file";
    input.name = field.name;
    input.accept = ".csv,.txt,.json,.xlsx,.xls";
  } else if (field.type === "select") {
    input = document.createElement("select");
    input.name = field.name;
    for (const choice of field.choices || []) {
      const opt = document.createElement("option");
      opt.value = choice;
      opt.textContent = choice;
      if (choice === field.default) opt.selected = true;
      input.appendChild(opt);
    }
  } else if (field.type === "api_select") {
    input = document.createElement("select");
    input.name = field.name;
    input.dataset.api = field.api;
    const empty = document.createElement("option");
    empty.value = "";
    empty.textContent = "— Loading… —";
    input.appendChild(empty);
    loadApiOptions(input, field.api);
  } else if (field.type === "checkbox") {
    input = document.createElement("input");
    input.type = "checkbox";
    input.name = field.name;
    wrap.className = "execute-toggle";
    wrap.insertBefore(input, label);
    return wrap;
  } else if (field.type === "textarea") {
    input = document.createElement("textarea");
    input.name = field.name;
    input.rows = 3;
  } else {
    input = document.createElement("input");
    input.type = field.type === "email" ? "email" : field.type === "date" ? "date" : field.type === "number" ? "number" : "text";
    input.name = field.name;
    if (field.default) input.value = field.default;
  }

  if (field.required && field.type !== "checkbox") input.required = true;
  wrap.appendChild(input);

  if (field.help) {
    const help = document.createElement("div");
    help.className = "field-help";
    help.textContent = field.help;
    wrap.appendChild(help);
  }
  return wrap;
}

async function loadApiOptions(select, apiType) {
  try {
    const resp = await fetch(`/api/options/${apiType}`);
    const data = await resp.json();
    select.innerHTML = "";
    const empty = document.createElement("option");
    empty.value = "";
    empty.textContent = "— Select —";
    select.appendChild(empty);
    for (const item of data) {
      const opt = document.createElement("option");
      opt.value = item.id || item.value;
      opt.textContent = item.label || item.value;
      select.appendChild(opt);
    }
  } catch (err) {
    select.innerHTML = "<option value=''>Failed to load options</option>";
  }
}

async function loadTool(toolId) {
  if (!toolId) {
    toolForm.classList.add("hidden");
    placeholder.classList.remove("hidden");
    currentTool = null;
    return;
  }

  clearLog();

  const resp = await fetch(`/api/tool/${toolId}`);
  currentTool = await resp.json();

  toolDescription.textContent = currentTool.description || "";

  if (currentTool.help) {
    toolHelp.innerHTML = "<strong>What this tool does</strong><p>" + escapeHtml(currentTool.help) + "</p>";
    toolHelp.classList.remove("hidden");
  } else {
    toolHelp.classList.add("hidden");
    toolHelp.innerHTML = "";
  }

  if (currentTool.warning) {
    toolWarning.textContent = currentTool.warning;
    toolWarning.classList.remove("hidden");
  } else {
    toolWarning.classList.add("hidden");
  }

  fieldsContainer.innerHTML = "";
  if (currentTool.template) {
    const linkWrap = document.createElement("div");
    linkWrap.className = "template-link";
    linkWrap.innerHTML = `<a class="btn secondary" href="/download-template/${toolId}">Download CSV Template</a>`;
    fieldsContainer.appendChild(linkWrap);
  }

  for (const field of currentTool.fields || []) {
    fieldsContainer.appendChild(renderField(field));
  }

  if (currentTool.mutating && !DEMO_MODE) {
    executeSection.classList.remove("hidden");
    setExecuteUi(false);
  } else if (executeSection) {
    executeSection.classList.add("hidden");
    setExecuteUi(false);
  }

  toolForm.classList.remove("hidden");
  placeholder.classList.add("hidden");
}

toolSelect.addEventListener("change", () => loadTool(toolSelect.value));

if (executeMode) {
  executeMode.addEventListener("change", () => {
    const enabled = executeMode.checked;
    if (enabled && !confirm("Enable execute mode? This allows live changes to Genesys Cloud.")) {
      setExecuteUi(false);
      return;
    }
    setExecuteUi(enabled);
  });
}

toolForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!currentTool) return;

  const submitter = event.submitter;
  const execute = !DEMO_MODE && submitter && submitter.dataset.execute === "1";

  if (execute) {
    const confirmVal = document.getElementById("execute-confirm").value.trim().toUpperCase();
    if (confirmVal !== "EXECUTE") {
      alert("Type EXECUTE in the confirmation field before executing.");
      return;
    }
    if (!confirm(`FINAL CONFIRMATION\n\nExecute "${currentTool.name}" against Genesys Cloud?\n\nThis will apply live changes.`)) {
      return;
    }
  }

  executeModeHidden.value = execute ? "1" : "0";

  if (clearBeforeRun.checked) {
    clearLog();
  }

  appendLog(`\n[INFO] Starting ${currentTool.name} (${DEMO_MODE ? "DEMO PREVIEW" : execute ? "EXECUTE" : "DRY RUN"})\n`);

  const resp = await fetch(`/run/${currentTool.id}`, { method: "POST", body: new FormData(toolForm) });
  if (!resp.ok) {
    appendLog(await resp.text());
    return;
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    appendLog(decoder.decode(value));
  }
});

document.getElementById("btn-health").addEventListener("click", async () => {
  appendLog("\n[INFO] Checking auth…\n");
  const resp = await fetch("/health");
  appendLog(JSON.stringify(await resp.json(), null, 2) + "\n");
});

document.getElementById("btn-copy-log").addEventListener("click", () => {
  navigator.clipboard.writeText(logEl.innerText);
});

document.getElementById("btn-clear-log").addEventListener("click", () => {
  clearLog();
});
