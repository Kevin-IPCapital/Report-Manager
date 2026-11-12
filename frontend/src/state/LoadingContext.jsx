import { createContext } from 'preact';
import { useCallback, useContext, useState } from 'preact/hooks';

const Ctx = createContext(null);
export const useLoading = () => useContext(Ctx);

// Counter so overlapping requests keep the overlay up until all finish.
export function LoadingProvider({ children }) {
  const [count, setCount] = useState(0);
  const track = useCallback(() => {
    let done = false;
    setCount((c) => c + 1);
    return () => { if (!done) { done = true; setCount((c) => c - 1); } };
  }, []);
  return <Ctx.Provider value={{ loading: count > 0, track }}>{children}</Ctx.Provider>;
}
