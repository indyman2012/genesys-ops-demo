(function () {
  const config = window.AWH_REPORTS || {};
  const genesysUrl = config.genesysUrl || "";
  const tabs = Array.from(document.querySelectorAll("[data-report-tab]"));
  const panels = Array.from(document.querySelectorAll("[data-report-panel]"));
  const storageKey = "awh-report-tab";

  function activate(tabId) {
    tabs.forEach((tab) => {
      const active = tab.dataset.reportTab === tabId;
      tab.classList.toggle("is-active", active);
      tab.setAttribute("aria-selected", active ? "true" : "false");
    });

    panels.forEach((panel) => {
      const active = panel.dataset.reportPanel === tabId;
      panel.classList.toggle("hidden", !active);
      if (!active) {
        return;
      }
      const iframe = panel.querySelector("iframe[data-lazy-src]");
      if (iframe && !iframe.getAttribute("src")) {
        iframe.setAttribute("src", iframe.dataset.lazySrc);
      }
    });

    try {
      localStorage.setItem(storageKey, tabId);
    } catch (_err) {
      /* ignore */
    }
  }

  tabs.forEach((tab) => {
    tab.addEventListener("click", () => activate(tab.dataset.reportTab));
  });

  document.querySelectorAll("[data-genesys-popup]").forEach((button) => {
    button.addEventListener("click", () => {
      if (!genesysUrl) {
        return;
      }
      window.open(genesysUrl, "genesys-analytics", "noopener,noreferrer,width=1280,height=900");
    });
  });

  let initialTab = config.defaultTab || "custom";
  try {
    const saved = localStorage.getItem(storageKey);
    if (saved && tabs.some((tab) => tab.dataset.reportTab === saved)) {
      initialTab = saved;
    }
  } catch (_err) {
    /* ignore */
  }

  activate(initialTab);
})();
