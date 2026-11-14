import { createContext, ReactNode, useContext, useEffect, useState } from "react";
import { api, setApiUser, User } from "./api";

// Demo auth: no passwords. The chosen user id is sent as X-User-Id on every request.
const KEY = "cancerflow.care.user";

function readStored(): string | null {
  try { return localStorage.getItem(KEY); } catch { return null; }
}
function writeStored(id: string | null) {
  try { id ? localStorage.setItem(KEY, id) : localStorage.removeItem(KEY); } catch { /* storage blocked */ }
}

type AuthCtx = { user: User | null; ready: boolean; login: (id: string) => Promise<void>; logout: () => void };
const Ctx = createContext<AuthCtx>(null as unknown as AuthCtx);
export const useAuth = () => useContext(Ctx);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const id = readStored();
    if (!id) { setReady(true); return; }
    setApiUser(id);
    api.me()
      .then(setUser)
      .catch((e) => {
        setApiUser(null);
        if (e?.status === 401 || e?.status === 404) writeStored(null); // unknown user; keep it if the server is just down
      })
      .finally(() => setReady(true));
  }, []);

  const login = async (id: string) => {
    const { user } = await api.login(id);
    setApiUser(user.id); writeStored(user.id); setUser(user);
    location.hash = user.role === "doctor" ? "/worklist" : "/";
  };
  const logout = () => { setApiUser(null); writeStored(null); setUser(null); location.hash = "/"; };

  return <Ctx.Provider value={{ user, ready, login, logout }}>{children}</Ctx.Provider>;
}
