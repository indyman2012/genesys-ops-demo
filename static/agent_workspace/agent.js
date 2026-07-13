(function () {
  const POLL_REALTIME_MS = 5000;
  const POLL_STATS_MS = 30 * 60 * 1000;

  let state = null;
  let queueSelectDirty = false;
  let listeningCallId = null;

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

  function formatWait(seconds) {
    const total = Math.max(0, parseInt(seconds, 10) || 0);
    const mins = Math.floor(total / 60);
    const secs = total % 60;
    if (mins === 0) return secs + "s";
    return mins + ":" + String(secs).padStart(2, "0");
  }

  function memberLabel(status) {
    return {
      on_queue: "On queue",
      on_call: "On call",
      off_queue: "Off queue",
      away: "Away",
    }[status] || status;
  }

  function needsAuth(state) {
    const pilotMode = window.AGENT_DEMO_MODE !== true && window.AGENT_DEMO_MODE !== "true";
    return pilotMode && (!!state.auth_required || !state.authenticated);
  }

  function setAuthGate(visible) {
    const gate = $("auth-gate");
    const workspace = $("agent-workspace");
    const footer = document.querySelector(".footer-stats");
    if (gate) gate.classList.toggle("hidden", !visible);
    if (workspace) workspace.classList.toggle("hidden", visible);
    if (footer) footer.classList.toggle("hidden", visible);
  }

  function render(state) {
    const authBlocked = needsAuth(state);

    $("agent-name").textContent = authBlocked ? "Sign in required" : state.agent_name;
    $("updated-at").textContent = authBlocked ? "" : "Updated " + formatTime(state.updated_at);

    const nav = state.nav || {};
    const roleNav = $("role-nav");
    const demoNav = window.AGENT_DEMO_MODE === true || window.AGENT_DEMO_MODE === "true";
    const showSupervisor = !authBlocked && (demoNav || !!nav.supervisor);
    const showAdmin = !authBlocked && (demoNav || !!nav.admin);
    if (roleNav) {
      roleNav.classList.toggle("hidden", !showSupervisor && !showAdmin);
      const supervisorLink = $("nav-supervisor");
      if (supervisorLink) supervisorLink.classList.toggle("hidden", !showSupervisor);
      const adminLink = $("nav-admin");
      if (adminLink) adminLink.classList.toggle("hidden", !showAdmin);
    }

    setAuthGate(authBlocked);
    if (authBlocked) {
      return;
    }

    const select = $("queue-select");
    const queueErr = state.queues_error || "";
    const queueErrEl = $("queue-load-error");
    if (queueErrEl) {
      queueErrEl.textContent = queueErr;
      queueErrEl.classList.toggle("hidden", !queueErr);
    }
    const noQueues = !state.queues || state.queues.length === 0;
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
    select.disabled = noQueues || !!queueErr;
    $("queue-status-text").textContent = onQueue ? "On queue — receiving work" : "Off queue";
    $("queue-status-text").className = "status-pill " + (onQueue ? "on" : "off");
    $("btn-on-queue").disabled = onQueue || noQueues || !!queueErr;
    $("btn-off-queue").disabled = !onQueue || noQueues || !!queueErr;

    if ($("webrtc-status") && state.media) {
      $("webrtc-status").textContent = state.media.webrtc_ready ? "WebRTC ready" : "WebRTC not ready";
      $("webrtc-status").className = state.media.webrtc_ready ? "webrtc-ok" : "hint warn";
    }

    const canDial = !state.current_call;
    $("btn-dial").disabled = !canDial;
    $("dial-hint").classList.toggle("hidden", canDial);
    const simInbound = $("btn-sim-inbound");
    if (simInbound) simInbound.disabled = !onQueue || !!state.current_call;

    const call = state.current_call;
    const waitingPanel = $("call-waiting");
    const panelTitle = $("call-panel-title");
    waitingPanel.classList.toggle("hidden", !!call);
    $("call-active").classList.toggle("hidden", !call);
    panelTitle.textContent = call ? "Current interaction" : "Calls waiting";

    if (!call) {
      const waiting = state.calls_waiting || [];
      $("waiting-count").textContent =
        waiting.length === 1 ? "1 call waiting" : waiting.length + " calls waiting";
      const list = $("waiting-list");
      list.innerHTML = "";
      $("waiting-empty").classList.toggle("hidden", waiting.length > 0);
      list.classList.toggle("hidden", waiting.length === 0);
      waiting.forEach((item, index) => {
        const li = document.createElement("li");
        li.className = "waiting-item" + (index === 0 ? " leading" : "");
        if (listeningCallId && item.id === listeningCallId) {
          li.classList.add("is-monitored");
        }
        const waitLabel = formatWait(item.wait_sec);
        const longWait = (item.wait_sec || 0) >= 120;
        const listening = listeningCallId && item.id === listeningCallId;
        const listenBtn = showSupervisor
          ? '<button type="button" class="btn btn-secondary btn-listen' +
            (listening ? " is-listening" : "") +
            '" data-listen-call="1" data-call-id="' +
            escapeHtml(item.id) +
            '"' +
            (listening ? " disabled" : "") +
            ">" +
            (listening ? "Listening…" : "Listen") +
            "</button>"
          : "";
        li.innerHTML =
          '<div class="waiting-caller">' +
          (item.is_callback ? '<span class="waiting-badge">Callback</span>' : "") +
          escapeHtml(item.caller_name) +
          "</div>" +
          '<div class="waiting-number">' +
          escapeHtml(item.caller_number) +
          "</div>" +
          '<div class="waiting-side"><span class="waiting-wait' +
          (longWait ? " long" : "") +
          '">' +
          waitLabel +
          "</span>" +
          listenBtn +
          "</div>";
        list.appendChild(li);
      });
    }

    if (call) {
      listeningCallId = null;
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
    fitCallDisplay();
  }

  function fitCallDisplay() {
    requestAnimationFrame(() => {
      const card = document.querySelector(".panel-center .call-card");
      if (!card) return;

      const display = document.querySelector(".call-display");
      const title = card.querySelector("h2");
      const titleHeight = title ? title.offsetHeight + 12 : 0;

      if ($("call-waiting") && !$("call-waiting").classList.contains("hidden")) {
        return;
      }

      if (!display || $("call-active").classList.contains("hidden")) return;

      ["call-caller", "call-number", "call-on-behalf"].forEach((id) => {
        const el = $(id);
        if (el) el.style.fontSize = "";
      });

      const maxHeight = display.clientHeight;
      if (maxHeight <= 0) return;

      const targets = [
        { id: "call-caller", min: 14, max: 38 },
        { id: "call-number", min: 12, max: 19 },
        { id: "call-on-behalf", min: 11, max: 14 },
      ];

      targets.forEach(({ id, min, max }) => {
        const el = $(id);
        if (!el || el.classList.contains("hidden") || !el.textContent) return;
        let size = max;
        el.style.fontSize = size + "px";
        while (size > min && display.scrollHeight > maxHeight) {
          size -= 1;
          el.style.fontSize = size + "px";
        }
      });

      let passes = 0;
      while (display.scrollHeight > maxHeight && passes++ < 30) {
        targets.forEach(({ id, min }) => {
          const el = $(id);
          if (!el || el.classList.contains("hidden")) return;
          const size = parseFloat(window.getComputedStyle(el).fontSize);
          if (size > min) el.style.fontSize = size - 1 + "px";
        });
      }
    });
  }

  let resizeTimer;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(fitCallDisplay, 100);
  });

  if (window.ResizeObserver) {
    const card = document.querySelector(".panel-center .call-card");
    if (card) {
      new ResizeObserver(() => fitCallDisplay()).observe(card);
    }
  }

  function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
  }

  const MEDIA_STORAGE_KEY = "archwell_agent_media_v1";
  const DEFAULT_DEVICE = { deviceId: "default", label: "System default audio" };
  const DEMO_FALLBACK = {
    audioinput: [
      DEFAULT_DEVICE,
      { deviceId: "builtin-mic", label: "Built-in Microphone" },
      { deviceId: "headset-mic", label: "Headset Microphone" },
    ],
    audiooutput: [
      DEFAULT_DEVICE,
      { deviceId: "builtin-speaker", label: "Built-in Speakers" },
      { deviceId: "headset-speaker", label: "Headset Earphone" },
    ],
  };

  let knownDeviceIds = new Set();
  let pendingDevicePrompt = null;
  let mediaPrefs = loadMediaPrefs();
  let mediaInitialized = false;

  function loadMediaPrefs() {
    try {
      const raw = localStorage.getItem(MEDIA_STORAGE_KEY);
      if (!raw) {
        return { micId: "default", speakerId: "default" };
      }
      const parsed = JSON.parse(raw);
      return {
        micId: parsed.micId || "default",
        speakerId: parsed.speakerId || "default",
      };
    } catch (_) {
      return { micId: "default", speakerId: "default" };
    }
  }

  function saveMediaPrefs() {
    localStorage.setItem(MEDIA_STORAGE_KEY, JSON.stringify(mediaPrefs));
  }

  function deviceLabel(device) {
    if (!device || device.deviceId === "default") return "System default audio";
    return device.label || "Audio device";
  }

  async function listAudioDevices() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.enumerateDevices) {
      return { ...DEMO_FALLBACK };
    }
    try {
      if (navigator.mediaDevices.getUserMedia) {
        try {
          const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
          stream.getTracks().forEach((track) => track.stop());
        } catch (_) {
          /* labels may stay empty without permission */
        }
      }
      const devices = await navigator.mediaDevices.enumerateDevices();
      const grouped = { audioinput: [], audiooutput: [] };
      devices.forEach((device) => {
        if (device.kind !== "audioinput" && device.kind !== "audiooutput") return;
        const label = device.label || (device.deviceId === "default" ? "System default audio" : "Audio device");
        grouped[device.kind].push({ deviceId: device.deviceId, label });
      });
      ["audioinput", "audiooutput"].forEach((kind) => {
        if (!grouped[kind].some((item) => item.deviceId === "default")) {
          grouped[kind].unshift(DEFAULT_DEVICE);
        }
        if (grouped[kind].length === 0) {
          grouped[kind] = DEMO_FALLBACK[kind].slice();
        }
      });
      return grouped;
    } catch (_) {
      return { ...DEMO_FALLBACK };
    }
  }

  function populateMediaSelect(selectEl, kind, devices, selectedId) {
    if (!selectEl) return;
    const available = devices[kind] || [];
    const hasSelected = available.some((device) => device.deviceId === selectedId);
    const value = hasSelected ? selectedId : "default";
    if (!hasSelected && selectedId !== "default") {
      if (kind === "audioinput") mediaPrefs.micId = "default";
      if (kind === "audiooutput") mediaPrefs.speakerId = "default";
      saveMediaPrefs();
    }
    selectEl.innerHTML = "";
    available.forEach((device) => {
      const opt = document.createElement("option");
      opt.value = device.deviceId;
      opt.textContent = deviceLabel(device);
      selectEl.appendChild(opt);
    });
    selectEl.value = value;
    return { value, label: deviceLabel(available.find((d) => d.deviceId === value) || DEFAULT_DEVICE) };
  }

  async function refreshMediaSelect(kind) {
    const selectEl = kind === "audioinput" ? $("media-mic-select") : $("media-speaker-select");
    if (!selectEl) return;
    const devices = await listAudioDevices();
    trackKnownDevices(devices);
    const selectedId = kind === "audioinput" ? mediaPrefs.micId : mediaPrefs.speakerId;
    const applied = populateMediaSelect(selectEl, kind, devices, selectedId);
    syncMediaToServer(kind, applied.value, applied.label);
    return devices;
  }

  function syncMediaToServer(kind, deviceId, label) {
    const body =
      kind === "audioinput"
        ? { mic_id: deviceId, mic_label: label }
        : { speaker_id: deviceId, speaker_label: label };
    return api("/api/media", { method: "POST", body: JSON.stringify(body) })
      .then((data) => {
        state = data;
      })
      .catch((err) => console.error("media sync failed", err));
  }

  function trackKnownDevices(devices) {
    const ids = new Set();
    ["audioinput", "audiooutput"].forEach((kind) => {
      (devices[kind] || []).forEach((device) => {
        if (device.deviceId && device.deviceId !== "default") ids.add(device.deviceId);
      });
    });
    knownDeviceIds = ids;
  }

  function findNewDevices(devices) {
    const fresh = [];
    ["audioinput", "audiooutput"].forEach((kind) => {
      (devices[kind] || []).forEach((device) => {
        if (!device.deviceId || device.deviceId === "default") return;
        if (!knownDeviceIds.has(device.deviceId)) {
          fresh.push({ ...device, kind });
        }
      });
    });
    return fresh;
  }

  function showDeviceModal(device) {
    pendingDevicePrompt = device;
    const modal = $("device-modal");
    const text = $("device-modal-text");
    if (!modal || !text) return;
    text.textContent =
      'New audio device detected — "' + deviceLabel(device) + '". Would you like to use it?';
    modal.classList.remove("hidden");
  }

  function hideDeviceModal() {
    $("device-modal")?.classList.add("hidden");
    pendingDevicePrompt = null;
  }

  async function applyMediaChoice(kind, deviceId) {
    if (kind === "audioinput") mediaPrefs.micId = deviceId;
    else mediaPrefs.speakerId = deviceId;
    saveMediaPrefs();
    await refreshMediaSelect(kind);
  }

  async function initMedia() {
    if (mediaInitialized) return;
    mediaInitialized = true;
    const micSelect = $("media-mic-select");
    const speakerSelect = $("media-speaker-select");
    if (!micSelect || !speakerSelect) return;

    const devices = await listAudioDevices();
    trackKnownDevices(devices);
    populateMediaSelect(micSelect, "audioinput", devices, mediaPrefs.micId);
    populateMediaSelect(speakerSelect, "audiooutput", devices, mediaPrefs.speakerId);

    micSelect.addEventListener("focus", () => {
      refreshMediaSelect("audioinput");
    });
    speakerSelect.addEventListener("focus", () => {
      refreshMediaSelect("audiooutput");
    });
    micSelect.addEventListener("change", () => {
      mediaPrefs.micId = micSelect.value;
      saveMediaPrefs();
      const label = micSelect.options[micSelect.selectedIndex]?.textContent || "System default audio";
      syncMediaToServer("audioinput", micSelect.value, label);
    });
    speakerSelect.addEventListener("change", () => {
      mediaPrefs.speakerId = speakerSelect.value;
      saveMediaPrefs();
      const label =
        speakerSelect.options[speakerSelect.selectedIndex]?.textContent || "System default audio";
      syncMediaToServer("audiooutput", speakerSelect.value, label);
    });

    $("device-modal-yes")?.addEventListener("click", async () => {
      if (!pendingDevicePrompt) return hideDeviceModal();
      const device = pendingDevicePrompt;
      hideDeviceModal();
      await applyMediaChoice(device.kind, device.deviceId);
    });
    $("device-modal-no")?.addEventListener("click", () => {
      if (pendingDevicePrompt) knownDeviceIds.add(pendingDevicePrompt.deviceId);
      hideDeviceModal();
    });

    if (navigator.mediaDevices && navigator.mediaDevices.addEventListener) {
      navigator.mediaDevices.addEventListener("devicechange", async () => {
        const updated = await listAudioDevices();
        const newcomers = findNewDevices(updated);
        trackKnownDevices(updated);
        await refreshMediaSelect("audioinput");
        await refreshMediaSelect("audiooutput");
        if (newcomers.length > 0 && $("device-modal")?.classList.contains("hidden")) {
          showDeviceModal(newcomers[0]);
        }
      });
    }
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

  $("btn-sim-inbound")?.addEventListener("click", () => {
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

  const waitingList = $("waiting-list");
  if (waitingList) {
    waitingList.addEventListener("click", (event) => {
      const button = event.target.closest("[data-listen-call]");
      if (!button || button.disabled) return;
      listeningCallId = button.getAttribute("data-call-id");
      if (state) render(state);
    });
  }

  function openOAuthPopup() {
    const popup = window.open(
      "/agent/oauth/login",
      "genesys-oauth",
      "width=520,height=720,menubar=no,toolbar=no,location=yes,status=no,resizable=yes,scrollbars=yes"
    );
    if (!popup) {
      window.alert("Allow popups for this site to sign in with Genesys.");
    }
  }

  window.addEventListener("message", (event) => {
    if (event.origin !== window.location.origin) return;
    if (event.data && event.data.type === "genesys-oauth-success") {
      refresh(false);
      return;
    }
    if (event.data && event.data.type === "genesys-oauth-error") {
      window.alert(event.data.message || "Genesys sign-in failed.");
    }
  });

  $("btn-sign-in")?.addEventListener("click", openOAuthPopup);

  refresh().then(() => {
    if (!state || !needsAuth(state)) {
      initMedia();
      fitCallDisplay();
    }
  });
  setInterval(() => {
    if (state && needsAuth(state)) {
      refresh(false);
      return;
    }
    refresh();
  }, POLL_REALTIME_MS);
  setInterval(() => {
    if (state && needsAuth(state)) return;
    post("/api/stats/refresh", {});
  }, POLL_STATS_MS);
})();
