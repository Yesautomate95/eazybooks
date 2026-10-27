import { useEffect, useState } from 'react';

const STORAGE_KEY = 'gl-theme';
const THEME_COLORS = { light: '#F8F4EA', dark: '#171310' };

const getInitialTheme = () => {
  try {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    if (saved === 'light' || saved === 'dark') return saved;
    if (typeof window.matchMedia === 'function') {
      return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    }
  } catch {
    // storage blocked or unavailable (private mode, jsdom) — fall through
  }
  return 'light';
};

// Theme for the Gilt Ledger public pages only — scoped via the gl-dark class
// on the page root, independent of Tailwind's dark: variant so the dashboard
// is unaffected. Follows the system preference until the user toggles.
export default function useGiltTheme() {
  const [theme, setTheme] = useState(getInitialTheme);

  // keep the browser chrome (Android address bar etc.) on the page's palette
  useEffect(() => {
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute('content', THEME_COLORS[theme]);
  }, [theme]);

  const toggleTheme = () =>
    setTheme((current) => {
      const next = current === 'dark' ? 'light' : 'dark';
      try {
        window.localStorage.setItem(STORAGE_KEY, next);
      } catch {
        // non-persistent is fine
      }
      return next;
    });

  return [theme, toggleTheme];
}
