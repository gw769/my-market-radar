import React, { createContext, useContext, useState, useEffect, useCallback } from "react";

interface UserInfo {
  name: string;
  email: string;
}

interface AuthContextType {
  isAuthenticated: boolean;
  user: UserInfo | null;
  token: string | null;
  login: (token: string, user?: UserInfo) => void;
  logout: () => void;
  refreshAuth: () => boolean;
}

const AuthContext = createContext<AuthContextType | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [token, setToken] = useState<string | null>(() => localStorage.getItem("token"));
  const [user, setUser] = useState<UserInfo | null>(() => {
    const name = localStorage.getItem("user_name");
    const email = localStorage.getItem("user_email");
    return name || email ? { name: name || "", email: email || "" } : null;
  });

  const isAuthenticated = !!token;

  const login = useCallback((newToken: string, userInfo?: UserInfo) => {
    localStorage.setItem("token", newToken);
    setToken(newToken);
    if (userInfo) {
      localStorage.setItem("user_name", userInfo.name);
      localStorage.setItem("user_email", userInfo.email);
      setUser(userInfo);
    }
  }, []);

  const logout = useCallback(() => {
    localStorage.removeItem("token");
    localStorage.removeItem("user_name");
    localStorage.removeItem("user_email");
    setToken(null);
    setUser(null);
  }, []);

  const refreshAuth = useCallback(() => {
    const t = localStorage.getItem("token");
    const name = localStorage.getItem("user_name");
    const email = localStorage.getItem("user_email");
    setToken(t);
    setUser(name || email ? { name: name || "", email: email || "" } : null);
    return !!t;
  }, []);

  useEffect(() => {
    const handleStorageChange = (e: StorageEvent) => {
      if (e.key === "token" || e.key === "user_name" || e.key === "user_email") {
        refreshAuth();
      }
    };
    window.addEventListener("storage", handleStorageChange);
    return () => window.removeEventListener("storage", handleStorageChange);
  }, [refreshAuth]);

  return (
    <AuthContext.Provider value={{ isAuthenticated, user, token, login, logout, refreshAuth }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
