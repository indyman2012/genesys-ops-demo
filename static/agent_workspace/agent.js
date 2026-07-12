(function () {
  const POLL_REALTIME_MS = 5000;
  const POLL_STATS_MS = 30 * 60 * 1000;

  let state = null;
  let queueSelectDirty = false;

  const $ = (id) => document.getElementById(id);

  function api(path, options) {
    return fetch("/agent" + path, {
      headers: { "Content-Type": "application/json" },
      ...options,
    }).then((r) => {
      if (!r.ok) {
        return r.json().then((body) => Promise.reject(new Error(body.error || r.statusText)));
      }
      return r.json();
    });
  }

  function formatTime(iso) {
    if (!iso) return "";
    try {
      return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    } catch (_) {
      return "";
    }
  }

  function memberLabel(status) {
    return {
      on_queue: "On queue",
      on_call: "On call",
      off_queue: "Off queue",
      away: "Away",
    }[status] || status;
  }

  function render(state) {
    $("agent-name").textContent = state.agent_name;
    $("updated-at").textContent = "Updated " + formatTime(state.updated_at);

    const select = $("queue-select");
    if (!queueSelectDirty || select.options.length === 0) {
      const prev = select.value;
      select.innerHTML = "";
      state.queues.forEach((q) => {
        const opt = document.createElement("option");
        opt.value = q.id;
        opt.textContent = q.name;
        select.appendChild(opt);
      });
      select.value = state.selected_queue_id;
      if (prev && prev !== select.value) queueSelectDirty = false;
    } else {
      select.value = state.selected_queue_id;
    }

    const onQueue = state.on_queue;
    $("queue-status-text").textContent = onQueue ? "On queue — receiving work" : "Off queue";
    $("queue-status-text").className = "status-pill " + (onQueue ? "on" : "off");
    $("btn-on-queue").disabled = onQueue;
    $("btn-off-queue").disabled = !onQueue;

    $("media-mic").textContent = state.media.mic;
    $("media-speaker").textContent = state.media.speaker;
    $("webrtc-status").textContent = state.media.webrtc_ready ? "WebRTC ready" : "WebRTC not ready";
    $("webrtc-status").className = state.media.webrtc_ready ? "webrtc-ok" : "hint warn";

    const canDial = onQueue && !state.current_call;
    $("btn-dial").disabled = !canDial;
    $("dial-hint").classList.toggle("hidden", canDial);
    $("btn-sim-inbound").disabled = !onQueue || !!state.current_call;

    const call = state.current_call;
    $("call-empty").classList.toggle("hidden", !!call);
    $("call-active").classList.toggle("hidden", !call);

    if (call) {
      $("call-direction").textContent = call.direction;
      $("call-queue").textContent = call.queue_name || "";
      $("call-caller").textContent = call.caller_name || "Unknown";
      $("call-number").textContent = call.caller_number || "";
      const onBehalf = $("call-on-behalf");
      if (call.on_behalf_of_queue) {
        onBehalf.textContent = "Dialing on behalf of: " + call.on_behalf_of_queue;
        onBehalf.classList.remove("hidden");
      } else {
        onBehalf.classList.add("hidden");
      }
      const ringing = call.direction === "inbound" && !call.connected;
      $("btn-answer").classList.toggle("hidden", !ringing);
      $("btn-hangup").classList.toggle("hidden", ringing);
      $("wrapup-panel").classList.toggle("hidden", ringing || !call.connected);
    }

    const wrapupSelect = $("wrapup-select");
    if (wrapupSelect.options.length === 0 && state.wrapup_codes) {
      state.wrapup_codes.forEach((code) => {
        const opt = document.createElement("option");
        opt.value = code.id;
        opt.textContent = code.label;
        wrapupSelect.appendChild(opt);
      });
    }

    const chatBox = $("chat-messages");
    chatBox.innerHTML = "";
    (state.chat || []).forEach((msg) => {
      const div = document.createElement("div");
      div.className = "chat-msg";
      div.innerHTML =
        '<span class="author">' +
        escapeHtml(msg.author) +
        '</span><span class="time">' +
        formatTime(msg.at) +
        "</span><div>" +
        escapeHtml(msg.text) +
        "</div>";
      chatBox.appendChild(div);
    });
    chatBox.scrollTop = chatBox.scrollHeight;

    const members = $("membership-list");
    members.innerHTML = "";
    (state.membership || []).forEach((m) => {
      const li = document.createElement("li");
      li.innerHTML =
        "<span>" +
        escapeHtml(m.name) +
        '</span><span class="member-status ' +
        m.status +
        '">' +
        memberLabel(m.status) +
        "</span>";
      members.appendChild(li);
    });

    const act = state.activity || {};
    $("metric-cip").textContent = act.calls_in_progress ?? "—";
    $("metric-sl").textContent =
      act.service_level_pct != null ? act.service_level_pct + "%" : "—";
    $("metric-asa").textContent =
      act.asa_sec != null ? act.asa_sec + "s" : "—";

    const stats = state.agent_stats || {};
    $("stat-handled").textContent = stats.handled_today ?? "—";
    $("stat-aht").textContent = stats.avg_handle ?? "—";
    $("stat-talk").textContent = stats.avg_talk ?? "—";
    $("stat-acw").textContent = stats.avg_acw ?? "—";
    $("stat-callbacks").textContent = stats.callbacks_due ?? "—";
    $("stats-updated").textContent = state.stats_updated_at
      ? "last " + formatTime(state.stats_updated_at)
      : "";
  }

  function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
  }

  function refresh(jitter) {
    const qs = jitter === false ? "?jitter=0" : "";
    return api("/api/state" + qs)
      .then((data) => {
        state = data;
        render(state);
      })
      .catch((err) => console.error("refresh failed", err));
  }

  function post(path, body) {
    return api(path, { method: "POST", body: JSON.stringify(body || {}) }).then((data) => {
      state = data.state || data;
      render(state);
    });
  }

  $("queue-select").addEventListener("change", (e) => {
    queueSelectDirty = true;
    post("/api/queue", { queue_id: e.target.value }).catch(alert);
  });

  $("btn-on-queue").addEventListener("click", () => {
    post("/api/on-queue", { on_queue: true }).catch(alert);
  });

  $("btn-off-queue").addEventListener("click", () => {
    post("/api/on-queue", { on_queue: false }).catch(alert);
  });

  $("btn-dial").addEventListener("click", () => {
    const phone = $("dial-number").value.trim();
    if (!phone) {
      alert("Enter a phone number.");
      return;
    }
    post("/api/call/dial", { phone_number: phone }).catch(alert);
  });

  $("btn-sim-inbound").addEventListener("click", () => {
    api("/api/call/inbound", { method: "POST", body: "{}" })
      .then((data) => {
        state = data;
        render(state);
      })
      .catch(alert);
  });

  $("btn-answer").addEventListener("click", () => {
    api("/api/call/answer", { method: "POST", body: "{}" })
      .then((data) => {
        state = data;
        render(state);
      })
      .catch(alert);
  });

  $("btn-hangup").addEventListener("click", () => {
    $("wrapup-panel").classList.remove("hidden");
    $("btn-hangup").classList.add("hidden");
  });

  $("btn-wrapup").addEventListener("click", () => {
    const wrapup_code_id = $("wrapup-select").value;
    api("/api/call/end", { method: "POST", body: JSON.stringify({ wrapup_code_id }) })
      .then((data) => {
        state = data;
        render(state);
        $("btn-hangup").classList.remove("hidden");
      })
      .catch(alert);
  });

  $("chat-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const text = $("chat-input").value.trim();
    if (!text) return;
    post("/api/chat", { text }).then(() => {
      $("chat-input").value = "";
    }).catch(alert);
  });

  $("btn-refresh-stats").addEventListener("click", () => {
    post("/api/stats/refresh", {}).catch(alert);
  });

  refresh();
  setInterval(() => refresh(), POLL_REALTIME_MS);
  setInterval(() => post("/api/stats/refresh", {}), POLL_STATS_MS);
})();
