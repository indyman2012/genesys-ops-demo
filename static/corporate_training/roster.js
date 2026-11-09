(function () {
  "use strict";

  var API_BASE = (function () {
    var path = window.location.pathname || "";
    var marker = "/corporate-training";
    var idx = path.indexOf(marker);
    if (idx >= 0) {
      return path.slice(0, idx + marker.length) + "/";
    }
    return "/corporate-training/";
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
    var summaryText =
      (payload.apply ? "APPLY" : "PREVIEW") +
      " — " +
      payload.input_count +
      " emails, " +
      stats.ok +
      " ok, " +
      (stats.error || 0) +
      " errors";
    if (payload.target_queue_name) {
      summaryText += " → " + payload.target_queue_name;
    }
    summary.textContent = summaryText;

    var table = document.createElement("table");
    var thead = document.createElement("thead");
    thead.innerHTML =
      "<tr><th>Input email</th><th>User</th><th>Status</th><th>Training</th><th>Production</th><th>Actions</th><th>Detail</th></tr>";
    table.appendChild(thead);
    var tbody = document.createElement("tbody");
    (payload.rows || []).forEach(function (row) {
      var tr = document.createElement("tr");
      if (row.status === "error") tr.className = "error";
      if (row.status === "warn") tr.className = "warn";
      tr.innerHTML =
        "<td>" + escapeHtml(row.input_email || row.input_name) + "</td>" +
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

  function postWorkflow(endpoint, fileInput, apply, resultId, applyBtn, extraFields) {
    var file = fileInput.files && fileInput.files[0];
    if (!file) {
      renderResult(resultId, { error: "Choose an Excel file first." }, true);
      return Promise.resolve();
    }
    var form = new FormData();
    form.append("file", file);
    if (extraFields) {
      Object.keys(extraFields).forEach(function (key) {
        form.append(key, extraFields[key]);
      });
    }
    if (apply) {
      form.append("apply", "1");
    }
    applyBtn.disabled = true;
    return fetch(apiUrl(endpoint), {
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

  function parseJsonResponse(res) {
    return res.text().then(function (text) {
      try {
        return { ok: res.ok, body: JSON.parse(text), status: res.status };
      } catch (_e) {
        return {
          ok: false,
          status: res.status,
          body: { error: res.status === 401 ? "Sign in required" : "Unexpected server response" },
        };
      }
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
            "Sign in on the <a href=\"" + escapeHtml(hub) + "\">main menu</a> to load training queues."
          );
          return;
        }
        if (!pack.ok) {
          showBanner("err", escapeHtml(pack.body.error || "Setup failed"));
          return;
        }
        var list = $("queue-list");
        var select = $("assign-queue");
        var queues = pack.body.training_queues || [];
        if (list) {
          var labels = queues.map(function (q) {
            return typeof q === "string" ? q : q.name;
          });
          list.innerHTML =
            "<strong>" +
            pack.body.training_queue_count +
            " corporate training queues</strong><br>" +
            labels.join("<br>");
        }
        if (select) {
          select.innerHTML = "";
          var placeholder = document.createElement("option");
          placeholder.value = "";
          placeholder.textContent = "— Select training queue —";
          select.appendChild(placeholder);
          queues.forEach(function (q) {
            if (!q || !q.id) return;
            var opt = document.createElement("option");
            opt.value = q.id;
            opt.textContent = q.name || q.id;
            select.appendChild(opt);
          });
          select.disabled = queues.length === 0;
        }
      })
      .catch(function (err) {
        showBanner(
          "err",
          "Could not reach the training API (" + escapeHtml(String(err)) + "). Use " +
            "<a href=\"/corporate-training/\">/corporate-training/</a> on the ops site."
        );
      });
  }

  document.addEventListener("DOMContentLoaded", function () {
    var fileStart = $("file-start");
    var fileReturn = $("file-return");
    var btnStartPreview = $("btn-start-preview");
    var btnStartApply = $("btn-start-apply");
    var btnReturnPreview = $("btn-return-preview");
    var btnReturnApply = $("btn-return-apply");
    var fileAssign = $("file-assign");
    var assignQueue = $("assign-queue");
    var btnAssignPreview = $("btn-assign-preview");
    var btnAssignApply = $("btn-assign-apply");

    loadSetup();

    function assignExtraFields() {
      var qid = assignQueue && assignQueue.value;
      if (!qid) {
        renderResult("result-assign", { error: "Select a training queue first." }, true);
        return null;
      }
      return { queue_id: qid };
    }

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

    btnAssignPreview.addEventListener("click", function () {
      var extra = assignExtraFields();
      if (!extra) return;
      btnAssignApply.disabled = true;
      postWorkflow("api/assign-training-queue", fileAssign, false, "result-assign", btnAssignApply, extra);
    });
    btnAssignApply.addEventListener("click", function () {
      var extra = assignExtraFields();
      if (!extra) return;
      if (!window.confirm("Assign roster to " + (assignQueue.selectedOptions[0].textContent || "training queue") + "?")) return;
      postWorkflow("api/assign-training-queue", fileAssign, true, "result-assign", btnAssignApply, extra);
    });
    fileAssign.addEventListener("change", function () {
      btnAssignApply.disabled = true;
    });
    if (assignQueue) {
      assignQueue.addEventListener("change", function () {
        btnAssignApply.disabled = true;
      });
    }
  });
})();
