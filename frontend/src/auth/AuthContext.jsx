import { createContext } from 'preact';
import { useContext, useEffect, useState } from 'preact/hooks';
import { api } from '../api/adapter';

const AuthContext = createContext(null);
export const useAuth = () => useContext(AuthContext);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [ready, setReady] = useState(false); // false until the silent session restore has finished

  useEffect(() => {
    api.onSessionExpired(() => setUser(null));
    api.restoreSession().then(setUser).finally(() => setReady(true));
  }, []);

  const login = async (username, password) => setUser(await api.login(username, password));
  const logout = async () => { await api.logout(); setUser(null); };
  const register = async (username, email, password) => setUser(await api.register(username, email, password));

  return <AuthContext.Provider value={{ user, ready, login, register, logout }}>{children}</AuthContext.Provider>;
}
