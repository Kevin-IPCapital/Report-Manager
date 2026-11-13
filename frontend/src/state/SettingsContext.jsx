import { createContext } from 'preact';
import { useContext, useEffect, useState } from 'preact/hooks';
import { accents, defaultPriorityColours } from '../theme';

const Ctx = createContext(null);
export const useSettings = () => useContext(Ctx);

function usePersisted(key, fallback) {
  const [value, setValue] = useState(() => {
    try { const s = localStorage.getItem(key); return s === null ? fallback : JSON.parse(s); } catch { return fallback; }
  });
  const set = (v) => { setValue(v); try { localStorage.setItem(key, JSON.stringify(v)); } catch { /* storage blocked */ } };
  return [value, set];
}

export function SettingsProvider({ children }) {
  const [theme, setTheme] = usePersisted('ops_theme', 'system');
  const [accent, setAccent] = usePersisted('ops_accent', 'indigo');
  const [savedPriority, setSavedPriority] = usePersisted('ops_priority_colours', defaultPriorityColours);
  const priorityColours = { ...defaultPriorityColours, ...savedPriority };
  const [navCollapsed, setNavCollapsed] = usePersisted('ops_nav_collapsed', matchMedia('(max-width: 48em)').matches);
  const [resolved, setResolved] = useState('light');
  const [squareCorners, setSquareCorners] = usePersisted('ops_square', false);
  const [cardShadows, setCardShadows] = usePersisted('ops_shadows', true);

  useEffect(() => {
    const mq = matchMedia('(prefers-color-scheme: dark)');
    const apply = () => {
      const r = theme === 'system' ? (mq.matches ? 'dark' : 'light') : theme;
      document.documentElement.dataset.theme = r;
      document.documentElement.style.setProperty('--accent', (accents[accent] || accents.teal)[r]);
      setResolved(r);
    };
    apply();
    mq.addEventListener('change', apply);
    return () => mq.removeEventListener('change', apply);
  }, [theme, accent]);

  useEffect(() => {
    for (const [k, v] of Object.entries(priorityColours)) document.documentElement.style.setProperty(`--priority-${k}`, v);
  }, [savedPriority]);

  useEffect(() => {
    document.documentElement.dataset.corners = squareCorners ? 'square' : 'round';
    document.documentElement.dataset.shadows = cardShadows ? 'on' : 'off';
  }, [squareCorners, cardShadows]);

  const setPriorityColour = (key, value) => setSavedPriority({ ...priorityColours, [key]: value });
  const reset = () => { setTheme('system'); setAccent('indigo'); setSavedPriority(defaultPriorityColours); setSquareCorners(false); setCardShadows(true); };
  return (
    <Ctx.Provider value={{ theme, setTheme, accent, setAccent, resolved, priorityColours, setPriorityColour, squareCorners, setSquareCorners, cardShadows, setCardShadows, navCollapsed, setNavCollapsed, reset }}>
      {children}
    </Ctx.Provider>
  );
}
