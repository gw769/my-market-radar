const BASE = "/api";

/** Global event: dispatched on 401 so the app can redirect to login */
export const AUTH_EXPIRED_EVENT = "auth:expired";

export function hasAuthToken(): boolean {
  return Boolean(localStorage.getItem("token"));
}

export class UnauthorizedError extends Error {
  status = 401;
  constructor(message = "Unauthorized (not logged in or token expired)") {
    super(message);
    this.name = "UnauthorizedError";
  }
}

/**
 * Handle 401 response: clear invalid token and dispatch event for redirect.
 */
export function clearAuthOn401(): void {
  const token = localStorage.getItem("token");
  if (token) {
    localStorage.removeItem("token");
    localStorage.removeItem("user_name");
    localStorage.removeItem("user_email");
    // Notify the app that auth has expired so it can redirect to login
    window.dispatchEvent(new CustomEvent(AUTH_EXPIRED_EVENT));
  }
}

/**
 * Redirect to login page. Called by the global event listener or directly.
 */
export function redirectToLogin(delayMs = 0): void {
  const currentPath = window.location.pathname;
  if (currentPath !== "/login") {
    if (delayMs > 0) {
      setTimeout(() => {
        window.location.href = "/login?reason=expired";
      }, delayMs);
    } else {
      window.location.href = "/login?reason=expired";
    }
  }
}

/**
 * Install a global listener for auth-expired events.
 * Call once during app initialization (in main.tsx or App.tsx).
 */
export function installAuthExpiredListener(): () => void {
  const handler = () => {
    redirectToLogin(500);
  };
  window.addEventListener(AUTH_EXPIRED_EVENT, handler);
  return () => window.removeEventListener(AUTH_EXPIRED_EVENT, handler);
}

function headers() {
  const token = localStorage.getItem("token");
  return {
    "Content-Type": "application/json",
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
}

export async function apiGet<T = any>(url: string): Promise<T> {
  const res = await fetch(`${BASE}${url}`, { headers: headers() });
  if (res.status === 401) {
    clearAuthOn401();
    throw new UnauthorizedError();
  }
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || data.message || `HTTP ${res.status}`);
  }
  return res.json();
}

export async function apiPatch<T = any>(url: string, body: any): Promise<T> {
  const res = await fetch(`${BASE}${url}`, { method: "PATCH", headers: headers(), body: JSON.stringify(body) });
  if (res.status === 401) { clearAuthOn401(); throw new UnauthorizedError(); }
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || data.message || `HTTP ${res.status}`);
  }
  return res.json();
}

export async function apiPost<T = any>(url: string, body?: any): Promise<T> {
  const res = await fetch(`${BASE}${url}`, {
    method: "POST",
    headers: headers(),
    body: body ? JSON.stringify(body) : undefined,
  });
  if (res.status === 401) {
    clearAuthOn401();
    throw new UnauthorizedError();
  }
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function apiDelete<T = any>(url: string): Promise<T> {
  const res = await fetch(`${BASE}${url}`, {
    method: "DELETE",
    headers: headers(),
  });
  if (res.status === 401) {
    clearAuthOn401();
    throw new UnauthorizedError();
  }
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export function downloadBlob(url: string, filename: string) {
  const a = document.createElement("a");
  a.href = `${BASE}${url}`;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
}

export async function downloadAuthorized(url: string, filename: string) {
  const token = localStorage.getItem("token");
  const res = await fetch(`${BASE}${url}`, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  if (!res.ok) throw new Error(`下载失败：HTTP ${res.status}`);
  const blob = await res.blob();
  const href = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = href; a.download = filename; a.click(); URL.revokeObjectURL(href);
}
