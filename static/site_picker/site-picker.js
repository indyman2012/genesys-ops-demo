(function () {
  "use strict";

  var PICKER_ORIGIN = window.location.origin;
  var PICKER_WINDOW_NAME = "genesys-site-picker";

  window.name = PICKER_WINDOW_NAME;

  function $(id) {
    return document.getElementById(id);
  }

  function showPanel(name) {
    $("panel-connect").classList.toggle("active", name === "connect");
    $("panel-main").classList.toggle("active", name === "main");
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

  function extractTokenFromValue(raw) {
    if (!raw) return null;
    try {
      var parsed = JSON.parse(raw);
      return walkForToken(parsed);
    } catch (_e) {
      if (typeof raw === "string" && raw.split(".").length === 3 && raw.length > 40) {
        return raw;
      }
    }
    return null;
  }

  function walkForToken(obj) {
    if (!obj || typeof obj !== "object") return null;
    if (typeof obj.accessToken === "string" && obj.accessToken.length > 20) return obj.accessToken;
    if (typeof obj.access_token === "string" && obj.access_token.length > 20) return obj.access_token;
    if (typeof obj.token === "string" && obj.token.length > 20) return obj.token;
    if (Array.isArray(obj)) {
      for (var i = 0; i < obj.length; i++) {
        var found = walkForToken(obj[i]);
        if (found) return found;
      }
      return null;
    }
    for (var key in obj) {
      if (!Object.prototype.hasOwnProperty.call(obj, key)) continue;
      var nested = walkForToken(obj[key]);
      if (nested) return nested;
    }
    return null;
  }

  function findGenesysAccessToken() {
    var stores = [window.localStorage, window.sessionStorage];
    for (var s = 0; s < stores.length; s++) {
      var store = stores[s];
      if (!store) continue;
      for (var i = 0; i < store.length; i++) {
        var token = extractTokenFromValue(store.getItem(store.key(i)));
        if (token) return token;
      }
    }
    return null;
  }

  function buildBookmarkletHref() {
    var code = [
      "javascript:(function(){",
      "var ORIGIN=" + JSON.stringify(PICKER_ORIGIN) + ";",
      "var NAME=" + JSON.stringify(PICKER_WINDOW_NAME) + ";",
      "function walk(o){if(!o||typeof o!=='object')return null;",
      "if(typeof o.accessToken==='string'&&o.accessToken.length>20)return o.accessToken;",
      "if(typeof o.access_token==='string'&&o.access_token.length>20)return o.access_token;",
      "if(typeof o.token==='string'&&o.token.length>20)return o.token;",
      "if(Array.isArray(o)){for(var i=0;i<o.length;i++){var f=walk(o[i]);if(f)return f;}return null;}",
      "for(var k in o){if(Object.prototype.hasOwnProperty.call(o,k)){var n=walk(o[k]);if(n)return n;}}return null;}",
      "function fromRaw(raw){if(!raw)return null;try{return walk(JSON.parse(raw));}catch(e){",
      "if(typeof raw==='string'&&raw.split('.').length===3&&raw.length>40)return raw;}return null;}",
      "function findToken(){var stores=[localStorage,sessionStorage];",
      "for(var s=0;s<stores.length;s++){var store=stores[s];if(!store)continue;",
      "for(var i=0;i<store.length;i++){var t=fromRaw(store.getItem(store.key(i)));if(t)return t;}}return null;}",
      "var token=findToken();",
      "if(!token){alert('No Genesys session token found on this tab. Sign in to Genesys Cloud first, or use Sign in with Genesys on the Site Picker page.');return;}",
      "var target=window.open('',NAME);",
      "if(!target){alert('Open the Site Picker page first, then run this bookmarklet again.');return;}",
      "target.postMessage({type:'genesys-token-bridge',access_token:token},ORIGIN);",
      "alert('Linked to Site Picker. Switch back to that tab.');",
      "})();"
    ].join("");
    return code;
  }

  function bridgeToken(accessToken) {
    return fetch("api/bridge", {
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
    return fetch("api/state", { credentials: "same-origin" })
      .then(function (res) { return res.json(); })
      .then(renderState);
  }

  function renderState(state) {
    if (!state.authenticated) {
      showPanel("connect");
      return state;
    }

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

    fetch("api/change-site", {
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
    var popup = window.open("oauth/login?popup=1", "genesys-oauth", "width=520,height=720");
    if (!popup) {
      window.location.href = "oauth/login";
    }
  }

  function signOut() {
    fetch("oauth/logout", { method: "POST", credentials: "same-origin" })
      .then(function () { return loadState(); });
  }

  window.addEventListener("message", function (event) {
    var allowed = [PICKER_ORIGIN];
    if (allowed.indexOf(event.origin) === -1 &&
        event.origin.indexOf("pure.cloud") === -1 &&
        event.origin.indexOf("mypurecloud.com") === -1) {
      return;
    }
    var data = event.data || {};
    if (data.type !== "genesys-token-bridge" || !data.access_token) return;

    bridgeToken(data.access_token)
      .then(function () { return loadState(); })
      .then(function () { showResult("ok", "Linked from your Genesys tab."); })
      .catch(function (err) { showResult("err", err.message || String(err)); });
  });

  $("bookmarklet").href = buildBookmarkletHref();
  $("btn-oauth").addEventListener("click", openOAuthPopup);
  $("btn-change").addEventListener("click", changeSite);
  $("btn-refresh").addEventListener("click", loadState);
  $("btn-sign-out").addEventListener("click", signOut);

  window.addEventListener("message", function (event) {
    if (event.origin !== PICKER_ORIGIN) return;
    if (event.data && event.data.type === "genesys-oauth-success") {
      loadState();
    }
  });

  loadState();
})();
