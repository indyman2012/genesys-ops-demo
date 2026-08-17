(function () {
  "use strict";

  var PICKER_WINDOW_NAME = "genesys-site-picker";
  var appsUrl = "https://apps.usw2.pure.cloud";

  var scriptEl = document.querySelector("script[src*='site-picker.js']");
  var API_BASE = "/";
  if (scriptEl && scriptEl.src) {
    try {
      API_BASE = new URL(scriptEl.src).pathname.replace(/assets\/site-picker\.js$/, "");
    } catch (_e) {
      API_BASE = "/";
    }
  }
  if (!API_BASE.endsWith("/")) {
    API_BASE += "/";
  }

  var PICKER_ORIGIN = window.location.origin;
  var PICKER_URL = PICKER_ORIGIN + API_BASE;

  window.name = PICKER_WINDOW_NAME;

  function $(id) {
    return document.getElementById(id);
  }

  function apiUrl(path) {
    return API_BASE + path.replace(/^\//, "");
  }

  function showPanel(name) {
    $("panel-connect").classList.toggle("active", name === "connect");
    $("panel-main").classList.toggle("active", name === "main");
  }

  function showConnectStatus(kind, message) {
    var el = $("connect-status");
    if (!el) return;
    if (!message) {
      el.className = "result";
      el.textContent = "";
      return;
    }
    el.className = "result show " + (kind === "ok" ? "ok" : kind === "info" ? "info" : "err");
    el.textContent = message;
  }

  function showResult(kind, message) {
    var el = $("result");
    el.className = "result show " + (kind === "ok" ? "ok" : "err");
    el.textContent = message;
  }

  function clearResult() {
    var el = $("result");
    el.className = "result";
    el.textContent = "";
  }

  function sendBridgeAck(event, ok, errorMessage) {
    if (!event.source || !event.source.postMessage) return;
    try {
      event.source.postMessage({
        type: "genesys-token-bridge-ack",
        ok: ok,
        error: errorMessage || ""
      }, event.origin);
    } catch (_e) {}
  }

  function loadSetup() {
    return fetch(apiUrl("api/setup"), { credentials: "same-origin" })
      .then(function (res) { return res.json(); })
      .then(function (setup) {
        var warning = $("setup-warning");
        if (!warning) return setup;

        if (setup.oauth_ready) {
          warning.style.display = "none";
          return setup;
        }

        var lines = [
          "Sign-in is not configured on this server yet.",
          "This app expects agent OAuth env vars on the same Render service:",
          "GENESYS_AGENT_CLIENT_ID, GENESYS_AGENT_CLIENT_SECRET"
        ];
        if (setup.env_probe) {
          var unset = Object.keys(setup.env_probe).filter(function (key) {
            return !setup.env_probe[key];
          });
          if (unset.length) {
            lines.push("Unset on this service: " + unset.join(", "));
          }
        }
        if (setup.service_url && setup.service_url.indexOf("ops-demo") !== -1) {
          lines.push("OAuth may already exist on genesys-agent-pilot — copy those GENESYS_AGENT_* vars here, or use /site-picker on that service.");
        }
        if (setup.redirect_uri) {
          lines.push("Register this OAuth redirect URI in Genesys Admin:");
          lines.push(setup.redirect_uri);
        }
        warning.innerHTML = lines.map(function (line) {
          return "<div>" + line + "</div>";
        }).join("");
        warning.style.display = "block";
        return setup;
      })
      .catch(function () { return null; });
  }

  function buildBookmarkletHref() {
    var code = [
      "javascript:(function(){",
      "var ORIGIN=" + JSON.stringify(PICKER_ORIGIN) + ";",
      "var PICKER_URL=" + JSON.stringify(PICKER_URL) + ";",
      "var NAME=" + JSON.stringify(PICKER_WINDOW_NAME) + ";",
      "var host=location.hostname||'';",
      "var onGenesys=host.indexOf('pure.cloud')!==-1||host.indexOf('mypurecloud.com')!==-1;",
      "if(!onGenesys){alert('Wrong tab. Open Genesys Cloud (apps.usw2.pure.cloud), then click this bookmark while you are on that tab. Or use Sign in with Genesys on the Site Picker page.');return;}",
      "function walk(o){if(!o||typeof o!=='object')return null;",
      "if(typeof o.accessToken==='string'&&o.accessToken.length>20)return o.accessToken;",
      "if(typeof o.access_token==='string'&&o.access_token.length>20)return o.access_token;",
      "if(typeof o.token==='string'&&o.token.length>20)return o.token;",
      "if(typeof o.value==='string'&&o.value.indexOf('eyJ')===0)return o.value;",
      "if(Array.isArray(o)){for(var i=0;i<o.length;i++){var f=walk(o[i]);if(f)return f;}return null;}",
      "for(var k in o){if(Object.prototype.hasOwnProperty.call(o,k)){var n=walk(o[k]);if(n)return n;}}return null;}",
      "function fromRaw(raw){if(!raw)return null;",
      "var m=String(raw).match(/eyJ[A-Za-z0-9_-]+\\.eyJ[A-Za-z0-9_-]+\\.[A-Za-z0-9_-]+/);",
      "if(m)return m[0];try{return walk(JSON.parse(raw));}catch(e){}return null;}",
      "function fromSdk(){try{var inst=(window.purecloud&&window.purecloud.ApiClient&&window.purecloud.ApiClient.instance)||(window.platformClient&&window.platformClient.ApiClient&&window.platformClient.ApiClient.instance);",
      "if(inst&&inst.authentications){var a=inst.authentications.accessToken||inst.authentications.AccessToken;",
      "if(a&&a.accessToken)return a.accessToken;}}catch(e){}return null;}",
      "function findToken(){var t=fromSdk();if(t)return t;var stores=[localStorage,sessionStorage];",
      "for(var s=0;s<stores.length;s++){var store=stores[s];if(!store)continue;",
      "for(var i=0;i<store.length;i++){t=fromRaw(store.getItem(store.key(i)));if(t)return t;}}return null;}",
      "var token=findToken();",
      "if(!token){alert('Could not read a Genesys session from this tab. Use Sign in with Genesys on the Site Picker page instead.');return;}",
      "var target=window.open('',NAME);",
      "if(!target||target.closed){target=window.open(PICKER_URL,NAME);}",
      "if(!target){alert('Allow pop-ups, keep the Site Picker page open, then run this bookmark again.');return;}",
      "var sent=false,attempts=0,maxAttempts=12;",
      "function send(){attempts++;try{target.postMessage({type:'genesys-token-bridge',access_token:token},ORIGIN);}catch(e){}}",
      "function done(ok,err){window.removeEventListener('message',onAck);clearInterval(retry);",
      "if(ok){alert('Linked to Site Picker. Switch back to that tab.');return;}",
      "alert(err||'Site Picker did not confirm the link. Switch to that tab and read the error shown there.');}",
      "function onAck(ev){if(ev.origin!==ORIGIN)return;var d=ev.data||{};if(d.type!=='genesys-token-bridge-ack')return;",
      "sent=true;done(!!d.ok,d.error||'');}",
      "window.addEventListener('message',onAck);",
      "send();",
      "var retry=setInterval(function(){if(sent){clearInterval(retry);return;}if(attempts>=maxAttempts){done(false);return;}try{target.focus();}catch(e){}send();},500);",
      "})();"
    ].join("");
    return code;
  }

  function bridgeToken(accessToken) {
    return fetch(apiUrl("api/bridge"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ access_token: accessToken })
    }).then(function (res) {
      return res.json().then(function (data) {
        if (!res.ok || !data.ok) {
          throw new Error((data && data.error) || "Could not link Genesys session");
        }
        return data;
      });
    });
  }

  function loadState() {
    return fetch(apiUrl("api/state"), { credentials: "same-origin" })
      .then(function (res) { return res.json(); })
      .then(renderState)
      .catch(function (err) {
        showConnectStatus("err", err.message || String(err));
        throw err;
      });
  }

  function renderState(state) {
    if (state && state.apps_url) {
      appsUrl = state.apps_url;
      var hostEl = $("genesys-host");
      if (hostEl) {
        try {
          hostEl.textContent = new URL(appsUrl).host;
        } catch (_e) {
          hostEl.textContent = appsUrl;
        }
      }
    }

    if (!state.authenticated) {
      showPanel("connect");
      return state;
    }

    showConnectStatus("", "");
    showPanel("main");
    $("user-name").textContent = (state.user && state.user.name) || "Signed in";
    $("user-email").textContent = (state.user && state.user.email) || "";

    var phone = state.phone;
    var phoneMeta = $("phone-meta");
    if (phone && phone.id) {
      phoneMeta.textContent =
        "WebRTC phone: " + phone.name +
        (phone.site_name ? " · Current site: " + phone.site_name : "");
    } else {
      phoneMeta.textContent = "No WebRTC phone found for your account.";
    }

    var select = $("site-select");
    select.innerHTML = "";
    var sites = state.sites || [];
    if (!sites.length) {
      var opt = document.createElement("option");
      opt.value = "";
      opt.textContent = state.error || "No sites available";
      select.appendChild(opt);
      $("btn-change").disabled = true;
    } else {
      sites.forEach(function (site) {
        var option = document.createElement("option");
        option.value = site.id;
        option.textContent = site.name;
        if (phone && phone.site_id && phone.site_id === site.id) {
          option.selected = true;
        }
        select.appendChild(option);
      });
      $("btn-change").disabled = !(phone && phone.id);
    }

    if (state.error) {
      showResult("err", state.error);
    } else {
      clearResult();
    }

    return state;
  }

  function changeSite() {
    var siteId = $("site-select").value;
    if (!siteId) return;

    $("btn-change").disabled = true;
    clearResult();

    fetch(apiUrl("api/change-site"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ site_id: siteId })
    })
      .then(function (res) {
        return res.json().then(function (data) {
          if (!res.ok || !data.ok) {
            throw new Error((data && data.error) || "Site change failed");
          }
          return data;
        });
      })
      .then(function (data) {
        var phone = data.phone || {};
        showResult(
          "ok",
          "Location updated. Your WebRTC phone is now on " + (phone.site_name || "the selected site") + "."
        );
        return loadState();
      })
      .catch(function (err) {
        showResult("err", err.message || String(err));
      })
      .finally(function () {
        $("btn-change").disabled = false;
      });
  }

  function openOAuthPopup() {
    var popup = window.open(apiUrl("oauth/login?popup=1"), "genesys-oauth", "width=520,height=720");
    if (!popup) {
      window.location.href = apiUrl("oauth/login");
    }
  }

  function openGenesysCloud() {
    window.open(appsUrl, "genesys-cloud", "noopener");
  }

  function signOut() {
    fetch(apiUrl("oauth/logout"), { method: "POST", credentials: "same-origin" })
      .then(function () { return loadState(); });
  }

  function handleTokenBridge(event) {
    if (event.origin.indexOf("pure.cloud") === -1 &&
        event.origin.indexOf("mypurecloud.com") === -1) {
      return;
    }
    var data = event.data || {};
    if (data.type !== "genesys-token-bridge" || !data.access_token) return;

    showConnectStatus("info", "Link received from Genesys tab. Signing you in…");

    bridgeToken(data.access_token)
      .then(function () { return loadState(); })
      .then(function (state) {
        if (state.authenticated) {
          showConnectStatus("ok", "Linked successfully.");
          sendBridgeAck(event, true, "");
        } else {
          throw new Error("Session was not created. Configure Sign in with Genesys on Render.");
        }
      })
      .catch(function (err) {
        var message = err.message || String(err);
        showConnectStatus("err", message);
        sendBridgeAck(event, false, message);
      });
  }

  var bookmarklet = $("bookmarklet");
  bookmarklet.href = buildBookmarkletHref();
  bookmarklet.addEventListener("click", function (event) {
    event.preventDefault();
    alert(
      "Do not click this on the Site Picker page.\n\n" +
      "Drag it to your bookmarks bar, open Genesys Cloud in another tab, " +
      "then click the bookmark while you are on the Genesys tab.\n\n" +
      "Or use Sign in with Genesys above — that is easier."
    );
  });

  $("btn-oauth").addEventListener("click", openOAuthPopup);
  $("btn-open-genesys").addEventListener("click", openGenesysCloud);
  $("btn-change").addEventListener("click", changeSite);
  $("btn-refresh").addEventListener("click", loadState);
  $("btn-sign-out").addEventListener("click", signOut);

  window.addEventListener("message", handleTokenBridge);

  window.addEventListener("message", function (event) {
    if (event.origin !== PICKER_ORIGIN) return;
    if (event.data && event.data.type === "genesys-oauth-success") {
      loadState();
    }
    if (event.data && event.data.type === "genesys-oauth-error") {
      showConnectStatus("err", event.data.message || "Sign-in failed.");
    }
  });

  if (!window.location.pathname.endsWith("/")) {
    window.history.replaceState(null, "", window.location.pathname + "/" + window.location.search + window.location.hash);
  }

  loadSetup();
  loadState();
})();
