// Tiny DOM builder. Text always goes in as text nodes (never innerHTML), so
// names and descriptions typed by players can't inject markup.

export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs ?? {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") el.className = value;
    else if (key.startsWith("on") && typeof value === "function") {
      el.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (value === true) el.setAttribute(key, "");
    else el.setAttribute(key, String(value));
  }
  append(el, children);
  return el;
}

function append(el, children) {
  for (const child of children) {
    if (child === null || child === undefined || child === false) continue;
    if (Array.isArray(child)) append(el, child);
    else if (child instanceof Node) el.appendChild(child);
    else el.appendChild(document.createTextNode(String(child)));
  }
}

export function mount(target, ...children) {
  target.replaceChildren();
  append(target, children);
}

export const signed = (n) => (n >= 0 ? `+${n}` : `${n}`);

// Gear slots without a trailing ".0", like the bot (1.0 -> "1", 0.5 -> "0.5").
export const slots = (n) => String(Number(n.toFixed(2)));
