/* Resize only the presentation layout; notebook state stays independent. */
(() => {
  const tabs = [...document.querySelectorAll('.workspace-tabs [role="tab"]')];
  const activate = (tab) => {
    tabs.forEach((item) => {
      const selected = item === tab;
      item.setAttribute("aria-selected", String(selected));
      item.tabIndex = selected ? 0 : -1;
      document.getElementById(item.getAttribute("aria-controls")).hidden = !selected;
    });
    window.dispatchEvent(new Event("resize"));
  };
  tabs.forEach((tab, index) => {
    tab.addEventListener("click", () => activate(tab));
    tab.addEventListener("keydown", (event) => {
      const next = { ArrowRight: (index + 1) % tabs.length,
        ArrowLeft: (index + tabs.length - 1) % tabs.length, Home: 0, End: tabs.length - 1 };
      if (!(event.key in next)) return;
      event.preventDefault();
      tabs[next[event.key]].focus();
      activate(tabs[next[event.key]]);
    });
  });
  const layout = document.querySelector(".dashboard-layout");
  const sidebar = document.querySelector(".record-navigation");
  const divider = document.querySelector(".dashboard-divider");
  if (!layout || !sidebar || !divider) return;

  const narrow = matchMedia("(max-width: 900px)");
  let drag = null;
  const resize = (width) => {
    if (narrow.matches || !layout.clientWidth) return;
    const maximum = Math.min(600, layout.clientWidth - 390);
    const next = Math.round(Math.max(240, Math.min(maximum, width)));
    layout.style.setProperty("--record-sidebar-width", `${next}px`);
    divider.setAttribute("aria-valuemin", "240");
    divider.setAttribute("aria-valuemax", String(maximum));
    divider.setAttribute("aria-valuenow", String(next));
  };

  divider.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 || narrow.matches) return;
    drag = { x: event.clientX, width: sidebar.getBoundingClientRect().width };
    divider.setPointerCapture(event.pointerId);
    layout.classList.add("resizing");
    event.preventDefault();
  });
  divider.addEventListener("pointermove", (event) => {
    if (drag) resize(drag.width + event.clientX - drag.x);
  });
  const stop = () => {
    drag = null;
    layout.classList.remove("resizing");
    divider.blur();
  };
  divider.addEventListener("pointerup", stop);
  divider.addEventListener("pointercancel", stop);
  divider.addEventListener("lostpointercapture", stop);
  divider.addEventListener("keydown", (event) => {
    const width = sidebar.getBoundingClientRect().width;
    const widths = { ArrowLeft: width - 20, ArrowRight: width + 20, Home: 240, End: 600 };
    if (!(event.key in widths)) return;
    event.preventDefault();
    resize(widths[event.key]);
  });
  divider.addEventListener("dblclick", () => {
    layout.style.removeProperty("--record-sidebar-width");
    resize(sidebar.getBoundingClientRect().width);
  });
  window.addEventListener("resize", () => resize(sidebar.getBoundingClientRect().width));
  resize(sidebar.getBoundingClientRect().width);
})();
