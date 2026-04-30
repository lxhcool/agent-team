"use client";

import React, { createContext, useContext, useState, useEffect, useCallback } from "react";
import { useRouter, usePathname } from "next/navigation";

type User = {
  id: string;
  username: string;
  email: string;
  display_name: string;
  role: string;
  created_at: string;
};

type AuthContextType = {
  user: User | null;
  token: string | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  register: (username: string, email: string, password: string, display_name?: string) => Promise<void>;
  logout: () => void;
};

const AuthContext = createContext<AuthContextType | null>(null);

const TOKEN_KEY = "agent_team_token";
const USER_KEY = "agent_team_user";

declare global {
  interface Window {
    teamAgentDesktop?: {
      isDesktop?: boolean;
      platform?: string;
      auth?: {
        get: () => Promise<{ token?: string; user?: User } | null>;
        set: (auth: { token: string; user: User }) => Promise<boolean>;
        clear: () => Promise<boolean>;
      };
      workspace?: {
        chooseDirectory?: () => Promise<{ path?: string } | null>;
      };
    };
  }
}

// Paths that don't require authentication
const PUBLIC_PATHS = ["/login", "/register"];

async function saveDesktopAuth(auth: { token: string; user: User }) {
  try {
    await window.teamAgentDesktop?.auth?.set(auth);
  } catch {
    // Desktop auth persistence should never block a successful web login.
  }
}

function clearDesktopAuth() {
  window.teamAgentDesktop?.auth?.clear().catch(() => undefined);
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const router = useRouter();
  const pathname = usePathname();

  // Load auth state from localStorage on mount
  useEffect(() => {
    let cancelled = false;

    const loadAuth = async () => {
      const savedToken = localStorage.getItem(TOKEN_KEY);
      const savedUser = localStorage.getItem(USER_KEY);
      try {
        if (savedToken && savedUser) {
          setToken(savedToken);
          setUser(JSON.parse(savedUser));
          return;
        }

        const desktopAuth = await window.teamAgentDesktop?.auth?.get();
        if (desktopAuth?.token && desktopAuth.user && !cancelled) {
          localStorage.setItem(TOKEN_KEY, desktopAuth.token);
          localStorage.setItem(USER_KEY, JSON.stringify(desktopAuth.user));
          setToken(desktopAuth.token);
          setUser(desktopAuth.user);
        }
      } catch {
        localStorage.removeItem(TOKEN_KEY);
        localStorage.removeItem(USER_KEY);
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    };

    loadAuth();

    return () => {
      cancelled = true;
    };
  }, []);

  // Route guard: redirect to login if not authenticated, or leave login when already authenticated.
  useEffect(() => {
    if (loading) return;
    const isPublic = PUBLIC_PATHS.some((p) => pathname.startsWith(p));
    if (!token && !isPublic) {
      router.replace("/login");
    } else if (token && isPublic) {
      router.replace("/");
    }
  }, [token, loading, pathname, router]);

  const login = useCallback(async (username: string, password: string) => {
    const res = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || "登录失败");
    }
    const data = await res.json();
    setToken(data.access_token);
    setUser(data.user);
    localStorage.setItem(TOKEN_KEY, data.access_token);
    localStorage.setItem(USER_KEY, JSON.stringify(data.user));
    await saveDesktopAuth({ token: data.access_token, user: data.user });
  }, []);

  const register = useCallback(async (username: string, email: string, password: string, display_name?: string) => {
    const res = await fetch("/api/auth/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, email, password, display_name: display_name || "" }),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || "注册失败");
    }
    const data = await res.json();
    setToken(data.access_token);
    setUser(data.user);
    localStorage.setItem(TOKEN_KEY, data.access_token);
    localStorage.setItem(USER_KEY, JSON.stringify(data.user));
    await saveDesktopAuth({ token: data.access_token, user: data.user });
  }, []);

  const logout = useCallback(() => {
    setToken(null);
    setUser(null);
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
    clearDesktopAuth();
    router.replace("/login");
  }, [router]);

  return (
    <AuthContext.Provider value={{ user, token, loading, login, register, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}

/**
 * Authenticated fetch wrapper - automatically adds Authorization header.
 * Usage: authFetch("/api/planning-sessions", { method: "POST", ... })
 */
export function useAuthFetch() {
  const { token, logout } = useAuth();

  return useCallback(
    async (url: string, options: RequestInit = {}): Promise<Response> => {
      const headers = new Headers(options.headers || {});
      if (token) {
        headers.set("Authorization", `Bearer ${token}`);
      }
      const res = await fetch(url, { ...options, headers });
      if (res.status === 401) {
        logout();
      }
      return res;
    },
    [token, logout]
  );
}
