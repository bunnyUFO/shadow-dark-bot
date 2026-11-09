// Read-only DrawCraft maps. Only the bot's signed-in API can hand out a
// per-drawing view token. Keep it in memory, never in a link or browser storage.
import { inActivity } from "./activity.js";
import { api } from "./api.js";
import { h, mount } from "./dom.js";

const DRAWCRAFT = "https://draw.bunnyufo.net";
const MAP_ID = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/;

export async function renderMapsList(app, isCurrent) {
  mount(app, h("p", { class: "loading" }, "Loading maps…"));
  const maps = await api("/maps");
  if (!isCurrent()) return;
  mount(app,
    h("header", { class: "page-head" }, h("h1", {}, "Maps"),
      h("p", { class: "page-meta" }, "View the shared maps. Pan and zoom to explore.")),
    h("ul", { class: "picker" }, maps.map((map) =>
      h("li", {}, h("a", { class: "pick", href: `#/maps/${map.id}` },
        h("span", { class: "pick-name" }, map.title))))),
  );
}

export async function renderMap(app, id, isCurrent) {
  if (!MAP_ID.test(id)) throw new Error("Map not found.");
  mount(app, h("p", { class: "loading" }, "Loading map…"));
  const access = await api(`/maps/${id}/access`);
  if (!isCurrent()) return null;
  if (access.id !== id || typeof access.token !== "string") throw new Error("Map access failed.");

  // Discord's /drawcraft URL mapping makes the modules and view same-origin.
  // The standalone site uses DrawCraft's narrowly allowed CORS view routes.
  const base = inActivity ? "/drawcraft" : DRAWCRAFT;
  const host = h("div", { class: "map-canvas", "aria-label": access.title });
  const fit = h("button", { type: "button", disabled: true }, "Fit map");
  const expand = h("button", { type: "button", disabled: true, "aria-pressed": "false" }, "Full screen");
  const head = h("header", { class: "page-head map-head" },
    h("div", {}, h("a", { href: "#/maps" }, "← All maps"), h("h1", {}, access.title)),
    h("div", { class: "map-actions" }, fit, expand));
  mount(app,
    head,
    host,
    h("p", { class: "page-meta" }, "Drag to pan. Scroll or pinch to zoom."),
  );
  const { mountViewer } = await import(`${base}/web/viewer.js?v=20261109-image-fit`);
  if (!isCurrent()) return null;
  const viewer = await mountViewer(host, {
    url: `${base}/api/drawings/${id}/view`,
    token: access.token,
  });
  if (!isCurrent()) {
    viewer.destroy();
    return null;
  }
  fit.disabled = false;
  expand.disabled = false;
  const fitMap = () => {
    if (!isCurrent()) return;
    if (document.body.classList.contains("map-expanded")) return viewer.fit();
    const nav = document.getElementById("bottomnav");
    viewer.fit({ top: head.getBoundingClientRect().height + 8,
      bottom: (nav?.getBoundingClientRect().height ?? 0) + 8 });
  };
  fit.addEventListener("click", fitMap);
  const setExpanded = (expanded) => {
    document.body.classList.toggle("map-expanded", expanded);
    expand.textContent = expanded ? "Exit full screen" : "Full screen";
    expand.setAttribute("aria-pressed", String(expanded));
    requestAnimationFrame(fitMap);
  };
  expand.addEventListener("click", () => setExpanded(!document.body.classList.contains("map-expanded")));
  const onKeyDown = (event) => {
    if (event.key === "Escape" && document.body.classList.contains("map-expanded")) setExpanded(false);
  };
  document.addEventListener("keydown", onKeyDown);
  const destroy = viewer.destroy.bind(viewer);
  viewer.destroy = () => {
    document.removeEventListener("keydown", onKeyDown);
    document.body.classList.remove("map-expanded");
    destroy();
  };
  requestAnimationFrame(fitMap);
  return viewer;
}
