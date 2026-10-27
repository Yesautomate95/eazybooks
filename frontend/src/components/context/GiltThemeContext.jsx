/**
 * GiltThemeContext — shares the dashboard's Daylight/Lamplight theme so both
 * the shell (which applies the `gl-dark` class) and Settings → Account (which
 * now hosts the toggle, relocated from the sidebar) drive the SAME state.
 *
 * Wraps the existing useGiltTheme hook (localStorage-backed) once, instead of
 * each consumer calling it independently and getting its own disconnected copy.
 */
import React, { createContext, useContext } from "react";
import useGiltTheme from "../Pages/Landing/gilt/useGiltTheme";

const GiltThemeContext = createContext(null);

export function GiltThemeProvider({ children }) {
  const [theme, toggleTheme] = useGiltTheme();
  return (
    <GiltThemeContext.Provider value={{ theme, toggleTheme }}>
      {children}
    </GiltThemeContext.Provider>
  );
}

export function useGiltThemeCtx() {
  const ctx = useContext(GiltThemeContext);
  if (!ctx) return { theme: "light", toggleTheme: () => {} };
  return ctx;
}
