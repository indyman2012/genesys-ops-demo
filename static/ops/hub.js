(function () {
  const input = document.getElementById("hub-search");
  if (!input) return;

  const cards = Array.from(document.querySelectorAll(".hub-card"));
  const sections = Array.from(document.querySelectorAll(".hub-section"));
  const empty = document.getElementById("hub-no-match");

  function applyFilter() {
    const q = (input.value || "").trim().toLowerCase();
    let visible = 0;

    cards.forEach((card) => {
      const hay = (card.getAttribute("data-search") || "").toLowerCase();
      const show = !q || hay.includes(q);
      card.classList.toggle("hidden-by-search", !show);
      if (show) visible += 1;
    });

    sections.forEach((section) => {
      const any = section.querySelector(".hub-card:not(.hidden-by-search)");
      section.classList.toggle("hidden-by-search", !any);
    });

    if (empty) {
      empty.classList.toggle("visible", q.length > 0 && visible === 0);
    }
  }

  input.addEventListener("input", applyFilter);

  const healthBtn = document.getElementById("btn-health");
  if (healthBtn) {
    healthBtn.addEventListener("click", async () => {
      try {
        const resp = await fetch("/health");
        const data = await resp.json();
        window.alert(JSON.stringify(data, null, 2));
      } catch (err) {
        window.alert(String(err));
      }
    });
  }
})();
