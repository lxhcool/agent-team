/**
 * Global API client with auth token injection.
 * Import and use `apiFetch` instead of raw `fetch` for authenticated API calls.
 * Alternatively, the interceptor below patches global `fetch` on the client side.
 */

const TOKEN_KEY = "agent_team_token";

/**
 * Authenticated fetch - adds Bearer token from localStorage.
 * This works even outside of React components (no hook needed).
 */
export async function apiFetch(url: string, options: RequestInit = {}): Promise<Response> {
  const token = typeof window !== "undefined" ? localStorage.getItem(TOKEN_KEY) : null;

  const headers = new Headers(options.headers || {});
  if (token && url.startsWith("/api/")) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  if (!headers.has("Content-Type") && options.body && typeof options.body === "string") {
    // Don't set Content-Type for FormData
  }

  const res = await fetch(url, { ...options, headers });

  // Auto-logout on 401
  if (res.status === 401 && typeof window !== "undefined") {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem("agent_team_user");
    if (!window.location.pathname.startsWith("/login")) {
      window.location.href = "/login";
    }
  }

  return res;
}

/**
 * Install a global fetch interceptor that auto-adds auth headers to /api/ requests.
 * Call this once in the app initialization.
 */
export function installFetchInterceptor() {
  if (typeof window === "undefined") return;

  const originalFetch = window.fetch;
  const tokenKey = TOKEN_KEY;

  window.fetch = function (input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;

    // Only intercept /api/ requests
    if (url.startsWith("/api/")) {
      const token = localStorage.getItem(tokenKey);
      if (token) {
        const headers = new Headers(init?.headers || {});
        headers.set("Authorization", `Bearer ${token}`);
        init = { ...init, headers };
      }
    }

    return originalFetch.call(this, input, init).then((res) => {
      // Auto-logout on 401
      if (res.status === 401 && url.startsWith("/api/") && !window.location.pathname.startsWith("/login")) {
        localStorage.removeItem(tokenKey);
        localStorage.removeItem("agent_team_user");
        window.location.href = "/login";
      }
      return res;
    });
  };
}
