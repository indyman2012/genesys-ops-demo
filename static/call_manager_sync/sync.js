(function () {
  "use strict";

  var API_BASE = (function () {
    var path = window.location.pathname || "";
    var marker = "/call-manager-sync";
    var idx = path.indexOf(marker);
    if (idx >= 0) {
      return path.slice(0, idx + marker.length) + "/";
    }
    return "/call-manager-sync/";
  })();

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
      " rows, " +
      stats.ok +
      " ok, " +
      (stats.error || 0) +
      " errors" +
      (payload.elapsed_seconds != null ? " — " + payload.elapsed_seconds + "s" : "") +
      (payload.datatable_name ? " — table: " + payload.datatable_name : "");

    var table = document.createElement("table");
    var thead = document.createElement("thead");
    thead.innerHTML =
      "<tr><th>Email</th><th>Queue</th><th>Phone</th><th>User</th><th>Status</th><th>CenterQueue</th><th>Data table</th><th>Actions</th><th>Detail</th></tr>";
    table.appendChild(thead);
    var tbody = document.createElement("tbody");
    (payload.rows || []).forEach(function (row) {
      var tr = document.createElement("tr");
      if (row.status === "error") tr.className = "error";
      tr.innerHTML =
        "<td>" + escapeHtml(row.input_email) + "</td>" +
        "<td>" + escapeHtml(row.queue_name) + "</td>" +
        "<td>" + escapeHtml(row.phone) + "</td>" +
        "<td>" + escapeHtml(row.user_name) + "</td>" +
        "<td>" + escapeHtml(row.status) + "</td>" +
        "<td>" + escapeHtml(row.center_queue) + "</td>" +
        "<td>" + escapeHtml(row.datatable_row) + "</td>" +
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

  function parseJsonResponse(res) {
    return res.text().then(function (text) {
      try {
        return { ok: res.ok, body: JSON.parse(text), status: res.status };
      } catch (_e) {
        var snippet = (text || "").replace(/\s+/g, " ").trim().slice(0, 200);
        var hint =
          res.status === 401
            ? "Sign in required"
            : "Unexpected server response (HTTP " + res.status + "). " + snippet;
        return {
          ok: false,
          status: res.status,
          body: { error: hint },
        };
      }
    });
  }

  function setBusy(busy, label) {
    var preview = $("btn-sync-preview");
    var applyBtn = $("btn-sync-apply");
    if (preview) preview.disabled = busy;
    if (applyBtn && !applyBtn.dataset.appliedOnce) applyBtn.disabled = busy || applyBtn.disabled;
    var el = $("result-sync");
    if (busy && el) {
      el.className = "result show ok";
      el.textContent = label || "Working… this usually takes a few seconds per row.";
    }
  }

  function postSync(apply) {
    var fileInput = $("file-sync");
    var applyBtn = $("btn-sync-apply");
    var file = fileInput.files && fileInput.files[0];
    if (!file) {
      renderResult("result-sync", { error: "Choose an Excel file first." }, true);
      return Promise.resolve();
    }
    var form = new FormData();
    form.append("file", file);
    if (apply) form.append("apply", "1");
    setBusy(true, apply ? "Applying changes in Genesys…" : "Running preview…");
    if (apply) applyBtn.disabled = true;
    return fetch(apiUrl("api/sync"), {
      method: "POST",
      body: form,
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    })
      .then(parseJsonResponse)
      .then(function (pack) {
        if (pack.status === 401) {
          var hub = (pack.body && (pack.body.hub_url || pack.body.login_url)) || "/";
          showBanner(
            "err",
            "Sign in on the <a href=\"" + escapeHtml(hub) + "\">main menu</a> first (Supervisor role)."
          );
          renderResult("result-sync", { error: "Sign in required." }, true);
          return;
        }
        if (!pack.ok) {
          renderResult("result-sync", pack.body, true);
          return;
        }
        renderResult("result-sync", pack.body, false);
        if (!apply) applyBtn.disabled = false;
        if (apply) applyBtn.dataset.appliedOnce = "1";
      })
      .catch(function (err) {
        renderResult("result-sync", { error: String(err) }, true);
      })
      .finally(function () {
        setBusy(false);
        if (apply) applyBtn.disabled = true;
      });
  }

  function loadSetup() {
    return fetch(apiUrl("api/setup"), {
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    })
      .then(parseJsonResponse)
      .then(function (pack) {
        if (pack.status === 401) {
          var hub = (pack.body && (pack.body.hub_url || pack.body.login_url)) || "/";
          showBanner(
            "info",
            "Sign in on the <a href=\"" + escapeHtml(hub) + "\">main menu</a> to load setup (Supervisor role)."
          );
          return;
        }
        if (!pack.ok) {
          showBanner("err", escapeHtml(pack.body.error || "Setup failed"));
          return;
        }
        var el = $("setup-info");
        if (el) {
          var idLine = pack.body.datatable_id
            ? " <code>" + escapeHtml(pack.body.datatable_id) + "</code>"
            : " (verified on first Preview/Apply)";
          el.innerHTML =
            "<strong>Data table:</strong> " + escapeHtml(pack.body.datatable_name) + idLine;
        }
      })
      .catch(function (err) {
        showBanner("err", "Could not reach API: " + escapeHtml(String(err)));
      });
  }

  document.addEventListener("DOMContentLoaded", function () {
    loadSetup();
    $("btn-sync-preview").addEventListener("click", function () {
      postSync(false);
    });
    $("btn-sync-apply").addEventListener("click", function () {
      if (window.confirm("Apply live Genesys changes for every row in this file?")) {
        postSync(true);
      }
    });
  });
})();
