(function () {
  "use strict";

  var scriptEl = document.querySelector("script[src*='roster.js']");
  var API_BASE = "/corporate-training/";
  if (scriptEl && scriptEl.src) {
    try {
      API_BASE = new URL(scriptEl.src).pathname.replace(/assets\/roster\.js$/, "");
    } catch (_e) {
      API_BASE = "/corporate-training/";
    }
  }
  if (!API_BASE.endsWith("/")) {
    API_BASE += "/";
  }

  function $(id) {
    return document.getElementById(id);
  }

  function apiUrl(path) {
    return API_BASE + path.replace(/^\//, "");
  }

  function escapeHtml(text) {
    return String(text || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function showBanner(kind, html) {
    var el = $("auth-banner");
    if (!el) return;
    el.className = "banner " + (kind === "err" ? "err" : "info");
    el.innerHTML = html;
    el.style.display = "block";
  }

  function renderResult(containerId, payload, isError) {
    var el = $(containerId);
    if (!el) return;
    if (isError) {
      el.className = "result show err";
      el.textContent = payload.error || String(payload);
      return;
    }

    var stats = payload.stats || {};
    var summary = document.createElement("div");
    summary.className = "summary";
    summary.textContent =
      (payload.apply ? "APPLY" : "PREVIEW") +
      " — " +
      payload.input_count +
      " names, " +
      stats.ok +
      " ok, " +
      (stats.error || 0) +
      " errors";

    var table = document.createElement("table");
    var thead = document.createElement("thead");
    thead.innerHTML =
      "<tr><th>Input</th><th>User</th><th>Status</th><th>Training</th><th>Production</th><th>Actions</th><th>Detail</th></tr>";
    table.appendChild(thead);
    var tbody = document.createElement("tbody");
    (payload.rows || []).forEach(function (row) {
      var tr = document.createElement("tr");
      if (row.status === "error") tr.className = "error";
      if (row.status === "warn") tr.className = "warn";
      tr.innerHTML =
        "<td>" + escapeHtml(row.input_name) + "</td>" +
        "<td>" + escapeHtml(row.user_name) + "<br><span style=\"color:#666\">" + escapeHtml(row.user_email) + "</span></td>" +
        "<td>" + escapeHtml(row.status) + "</td>" +
        "<td>" + escapeHtml(row.training_queues) + "</td>" +
        "<td>" + escapeHtml(row.production_queues) + "</td>" +
        "<td>" + escapeHtml(row.actions) + "</td>" +
        "<td>" + escapeHtml(row.detail) + "</td>";
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);

    el.className = "result show ok";
    el.innerHTML = "";
    el.appendChild(summary);
    el.appendChild(table);
  }

  function postWorkflow(endpoint, fileInput, apply, resultId, applyBtn) {
    var file = fileInput.files && fileInput.files[0];
    if (!file) {
      renderResult(resultId, { error: "Choose an Excel file first." }, true);
      return Promise.resolve();
    }
    var form = new FormData();
    form.append("file", file);
    if (apply) {
      form.append("apply", "1");
    }
    applyBtn.disabled = true;
    return fetch(apiUrl(endpoint), {
      method: "POST",
      body: form,
      credentials: "same-origin",
    })
      .then(function (res) {
        return res.json().then(function (body) {
          return { ok: res.ok, body: body, status: res.status };
        });
      })
      .then(function (pack) {
        if (pack.status === 401 && pack.body.login_url) {
          showBanner(
            "err",
            "Sign in required. <a href=\"" + escapeHtml(pack.body.login_url) + "\">Sign in with Genesys</a>"
          );
          renderResult(resultId, { error: "Sign in required." }, true);
          return;
        }
        if (!pack.ok) {
          renderResult(resultId, pack.body, true);
          return;
        }
        renderResult(resultId, pack.body, false);
        if (!apply) {
          applyBtn.disabled = false;
        }
      })
      .catch(function (err) {
        renderResult(resultId, { error: String(err) }, true);
      })
      .finally(function () {
        if (apply) {
          applyBtn.disabled = true;
        }
      });
  }

  function loadSetup() {
    return fetch(apiUrl("api/setup"), { credentials: "same-origin" })
      .then(function (res) {
        return res.json().then(function (body) {
          return { ok: res.ok, body: body, status: res.status };
        });
      })
      .then(function (pack) {
        if (pack.status === 401 && pack.body.login_url) {
          showBanner(
            "info",
            "Sign in to load training queues. <a href=\"" + escapeHtml(pack.body.login_url) + "\">Sign in with Genesys</a>"
          );
          return;
        }
        if (!pack.ok) {
          showBanner("err", escapeHtml(pack.body.error || "Setup failed"));
          return;
        }
        var list = $("queue-list");
        if (!list) return;
        var names = (pack.body.training_queues || []).join("<br>");
        list.innerHTML =
          "<strong>" +
          pack.body.training_queue_count +
          " corporate training queues</strong><br>" +
          names;
      })
      .catch(function () {
        showBanner("err", "Could not reach the ops API. Open this page from /corporate-training/ on the toolkit server.");
      });
  }

  document.addEventListener("DOMContentLoaded", function () {
    var fileStart = $("file-start");
    var fileReturn = $("file-return");
    var btnStartPreview = $("btn-start-preview");
    var btnStartApply = $("btn-start-apply");
    var btnReturnPreview = $("btn-return-preview");
    var btnReturnApply = $("btn-return-apply");

    loadSetup();

    btnStartPreview.addEventListener("click", function () {
      btnStartApply.disabled = true;
      postWorkflow("api/start-training", fileStart, false, "result-start", btnStartApply);
    });
    btnStartApply.addEventListener("click", function () {
      if (!window.confirm("Apply start-training changes in Genesys?")) return;
      postWorkflow("api/start-training", fileStart, true, "result-start", btnStartApply);
    });

    btnReturnPreview.addEventListener("click", function () {
      btnReturnApply.disabled = true;
      postWorkflow("api/return-from-training", fileReturn, false, "result-return", btnReturnApply);
    });
    btnReturnApply.addEventListener("click", function () {
      if (!window.confirm("Apply return-from-training changes in Genesys?")) return;
      postWorkflow("api/return-from-training", fileReturn, true, "result-return", btnReturnApply);
    });

    fileStart.addEventListener("change", function () {
      btnStartApply.disabled = true;
    });
    fileReturn.addEventListener("change", function () {
      btnReturnApply.disabled = true;
    });
  });
})();
