(function () {
  "use strict";

  var PICKER_ORIGIN = window.location.origin;
  var PICKER_WINDOW_NAME = "genesys-site-picker";
  var appsUrl = "https://apps.usw2.pure.cloud";

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
    if (typeof raw === "string") {
      var jwt = raw.match(/eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/);
      if (jwt) return jwt[0];
    }
    try {
      var parsed = JSON.parse(raw);
      return walkForToken(parsed);
    } catch (_e) {
      return null;
    }
  }

  function walkForToken(obj) {
    if (!obj || typeof obj !== "object") return null;
    if (typeof obj.accessToken === "string" && obj.accessToken.length > 20) return obj.accessToken;
    if (typeof obj.access_token === "string" && obj.access_token.length > 20) return obj.access_token;
    if (typeof obj.token === "string" && obj.token.length > 20) return obj.token;
    if (typeof obj.value === "string" && obj.value.indexOf("eyJ") === 0) return obj.value;
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

  function tokenFromSdkGlobals() {
    try {
      var clients = [
        window.purecloud && window.purecloud.ApiClient && window.purecloud.ApiClient.instance,
        window.platformClient && window.platformClient.ApiClient && window.platformClient.ApiClient.instance
      ];
      for (var i = 0; i < clients.length; i++) {
        var inst = clients[i];
        if (!inst || !inst.authentications) continue;
        var auth = inst.authentications.accessToken || inst.authentications.AccessToken;
        if (auth && auth.accessToken) return auth.accessToken;
      }
    } catch (_e) {}
    return null;
  }

  function buildBookmarkletHref() {
    var code = [
      "javascript:(function(){",
      "var ORIGIN=" + JSON.stringify(PICKER_ORIGIN) + ";",
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
      "if(!target){alert('Keep the Site Picker page open in another tab, then run this bookmark again on Genesys.');return;}",
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

  function openGenesysCloud() {
    window.open(appsUrl, "genesys-cloud", "noopener");
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

  window.addEventListener("message", function (event) {
    if (event.origin !== PICKER_ORIGIN) return;
    if (event.data && event.data.type === "genesys-oauth-success") {
      loadState();
    }
  });

  loadState();
})();
