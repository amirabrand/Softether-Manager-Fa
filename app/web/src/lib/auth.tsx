"use client";

import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api, clearToken, getToken, setToken } from "./api";

interface AuthState {
  user: string | null;
  /** "admin" (the operator) or "reseller" (the reseller desk). */
  role: string;
  loading: boolean;
  /** No account exists yet: the sign-in screen offers to create the first one. */
  needsSetup: boolean;
  login: (u: string, p: string) => Promise<void>;
  setup: (u: string, p: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthState>(null as unknown as AuthState);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<string | null>(null);
  const [role, setRole] = useState<string>("admin");
  const [loading, setLoading] = useState(true);
  const [needsSetup, setNeedsSetup] = useState(false);

  useEffect(() => {
    let active = true;
    (async () => {
      if (getToken()) {
        try {
          const me = await api.me();
          if (active) {
            setUser(me.username);
            setRole(me.role ?? "admin");
            setLoading(false);
          }
          return;
        } catch {
          clearToken();
        }
      }
      try {
        const state = await api.authState();
        if (active) setNeedsSetup(state.setup_required);
      } catch {
        // The panel could not be asked; the login form is the safe assumption.
      } finally {
        if (active) setLoading(false);
      }
    })();

    const onUnauth = () => {
      setUser(null);
      setRole("admin");
    };
    window.addEventListener("sem-unauthorized", onUnauth);
    return () => {
      active = false;
      window.removeEventListener("sem-unauthorized", onUnauth);
    };
  }, []);

  const adopt = useCallback((token: { token: string; username: string; role?: string }) => {
    setToken(token.token);
    setUser(token.username);
    setRole(token.role ?? "admin");
    setNeedsSetup(false);
  }, []);

  const login = async (u: string, p: string) => adopt(await api.login(u, p));
  const setup = async (u: string, p: string) => adopt(await api.setup(u, p));

  const logout = () => {
    void api.logout().catch(() => {});
    clearToken();
    setUser(null);
    setRole("admin");
  };

  return (
    <AuthContext.Provider value={{ user, role, loading, needsSetup, login, setup, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export const useAuth = () => useContext(AuthContext);
