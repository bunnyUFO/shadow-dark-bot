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
  mount(app,
    h("header", { class: "page-head map-head" },
      h("div", {}, h("a", { href: "#/maps" }, "← All maps"), h("h1", {}, access.title)), fit),
    host,
    h("p", { class: "page-meta" }, "Drag to pan. Scroll or pinch to zoom."),
  );
  const { mountViewer } = await import(`${base}/web/viewer.js`);
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
  fit.addEventListener("click", () => viewer.fit());
  return viewer;
}
