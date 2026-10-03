// Talks to the bot's web API. Relative URLs only: inside Discord every request
// goes through the Activity proxy, so the same code works in both places.

const TOKEN_KEY = "sd.session";

let memoryToken = null; // fallback when storage is blocked (some iframes)

export function getToken() {
  try {
    return localStorage.getItem(TOKEN_KEY) ?? memoryToken;
  } catch {
    return memoryToken;
  }
}

export function setToken(token) {
  memoryToken = token;
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable: memory only */
  }
}

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

export async function api(path, { method = "GET", body } = {}) {
  const headers = { Accept: "application/json" };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  let resp;
  try {
    resp = await fetch(`/api${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "Can't reach the server. Is the bot running?");
  }
  const data = await resp.json().catch(() => null);
  if (!resp.ok) {
    const detail = data && typeof data.detail === "string" ? data.detail : resp.statusText;
    if (resp.status === 401) setToken(null);
    throw new ApiError(resp.status, detail);
  }
  return data;
}
