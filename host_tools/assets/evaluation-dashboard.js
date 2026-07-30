"use strict";

(() => {
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  const tabs = Array.from(document.querySelectorAll('[role="tab"]'));
  const panels = new Map(
    Array.from(document.querySelectorAll('[role="tabpanel"]')).map((panel) => [panel.id, panel]),
  );

  function activateTab(tab, moveFocus = true) {
    tabs.forEach((candidate) => {
      const selected = candidate === tab;
      candidate.setAttribute("aria-selected", selected ? "true" : "false");
      candidate.tabIndex = selected ? 0 : -1;
      const panel = panels.get(candidate.getAttribute("aria-controls"));
      if (panel) {
        panel.hidden = !selected;
      }
    });
    if (moveFocus) {
      tab.focus();
    }
  }

  tabs.forEach((tab, index) => {
    tab.addEventListener("click", () => activateTab(tab, false));
    tab.addEventListener("keydown", (event) => {
      let nextIndex = null;
      if (event.key === "ArrowRight") {
        nextIndex = (index + 1) % tabs.length;
      } else if (event.key === "ArrowLeft") {
        nextIndex = (index - 1 + tabs.length) % tabs.length;
      } else if (event.key === "Home") {
        nextIndex = 0;
      } else if (event.key === "End") {
        nextIndex = tabs.length - 1;
      }
      if (nextIndex !== null) {
        event.preventDefault();
        activateTab(tabs[nextIndex]);
      }
    });
  });

  const evidenceTab = document.getElementById("tab-evidence");
  let previousTarget = null;
  document.querySelectorAll("[data-evidence-ref]").forEach((button) => {
    button.addEventListener("click", () => {
      const evidenceId = button.getAttribute("data-evidence-ref");
      const target = Array.from(document.querySelectorAll("[data-evidence-id]")).find(
        (candidate) => candidate.getAttribute("data-evidence-id") === evidenceId,
      );
      if (!target || !evidenceTab) {
        return;
      }
      activateTab(evidenceTab, false);
      if (previousTarget) {
        previousTarget.classList.remove("is-targeted");
      }
      target.open = true;
      target.classList.add("is-targeted");
      target.scrollIntoView({ behavior: reducedMotion.matches ? "auto" : "smooth", block: "center" });
      target.querySelector("summary")?.focus({ preventScroll: true });
      previousTarget = target;
    });
  });
})();
