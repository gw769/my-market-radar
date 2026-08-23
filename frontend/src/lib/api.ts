const BASE = "/api";

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

export function clearAuthOn401(): void {
  const token = localStorage.getItem("token");
  if (token) {
    localStorage.removeItem("token");
    localStorage.removeItem("user_name");
    localStorage.removeItem("user_email");
    window.dispatchEvent(new CustomEvent(AUTH_EXPIRED_EVENT));
  }
}

export function redirectToLogin(delayMs = 0): void {
  const currentPath = window.location.pathname;
  if (currentPath !== "/login") {
    if (delayMs > 0) {
      setTimeout(() => { window.location.href = "/login?reason=expired"; }, delayMs);
    } else {
      window.location.href = "/login?reason=expired";
    }
  }
}

export function installAuthExpiredListener(): () => void {
  const handler = () => redirectToLogin(500);
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

function detailText(detail: unknown): string | null {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const messages = detail.map((item: any) => item?.msg || item?.message).filter(Boolean);
    return messages.length ? messages.join("；") : null;
  }
  return null;
}

async function responseError(res: Response): Promise<Error> {
  const data = await res.json().catch(() => ({} as any));
  const message = detailText(data?.detail) || detailText(data?.message) || `HTTP ${res.status}`;
  return new Error(message);
}

async function parseResponse<T>(res: Response): Promise<T> {
  if (res.status === 401) {
    clearAuthOn401();
    throw new UnauthorizedError();
  }
  if (!res.ok) throw await responseError(res);
  return res.json();
}

export async function apiGet<T = any>(url: string): Promise<T> {
  return parseResponse<T>(await fetch(`${BASE}${url}`, { headers: headers() }));
}

export async function apiPatch<T = any>(url: string, body: any): Promise<T> {
  return parseResponse<T>(await fetch(`${BASE}${url}`, { method: "PATCH", headers: headers(), body: JSON.stringify(body) }));
}

export async function apiPost<T = any>(url: string, body?: any): Promise<T> {
  return parseResponse<T>(await fetch(`${BASE}${url}`, {
    method: "POST",
    headers: headers(),
    body: body === undefined ? undefined : JSON.stringify(body),
  }));
}

export async function apiDelete<T = any>(url: string): Promise<T> {
  return parseResponse<T>(await fetch(`${BASE}${url}`, { method: "DELETE", headers: headers() }));
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
  if (res.status === 401) {
    clearAuthOn401();
    throw new UnauthorizedError();
  }
  if (!res.ok) throw await responseError(res);
  const blob = await res.blob();
  const href = URL.createObjectURL(blob);
  try {
    const a = document.createElement("a");
    a.href = href;
    a.download = filename;
    a.click();
  } finally {
    URL.revokeObjectURL(href);
  }
}
