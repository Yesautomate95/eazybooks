import React, { useState, useEffect, useContext } from "react";
import { Outlet, useLocation } from "react-router-dom";
import Sidebar from "./Sidebar";
import LoginModalCA from "./LoginModalCA";
import TallyStatusChip from "./common/TallyStatusChip";
import { EazyBooksContext } from "../../context/EazyBooksContext";
import { TallyConnectionProvider } from "../../context/TallyConnectionContext";
import { GiltThemeProvider, useGiltThemeCtx } from "../../context/GiltThemeContext";
import { cn } from "../../../lib/utils";
import "../../../styles/gilt.css";

function EazyBooksShell() {
  const [selectedComponent, setSelectedComponent] = useState(() => {
    return localStorage.getItem("selectedComponent") || "Advisor";
  });

  const location = useLocation();
  const { isOpen } = useContext(EazyBooksContext);
  const { theme } = useGiltThemeCtx();

  useEffect(() => {
    const path = location.pathname.substring(1) || "chat";
    const componentMap = {
      chat: "Advisor",
      lockr: "Lockr",
      clients: "Clients",
      history: "History",
      "notice-aid": "Notice Aid",
      cloud: "Cloud",
      tally: "Tally",
      "tally-chat": "Tally Chat",
      qb: "QuickBooks",
      "qb-chat": "QuickBooks Chat",
      mis: "MIS",
      analytics: "Analytics",
    };
    const component = componentMap[path] || "Advisor";
    setSelectedComponent(component);
    localStorage.setItem("selectedComponent", component);
  }, [location]);

  return (
    <div className={cn("gl-root flex h-screen overflow-hidden antialiased", theme === "dark" && "gl-dark")}>
      <Sidebar setSelectedComponent={setSelectedComponent} />
      <main className="flex-1 min-w-0 overflow-y-auto overflow-x-hidden">
        <Outlet />
      </main>
      {/* Persistent connection chip — fixed-position, route-aware, hidden
          on non-Tally pages. Polls every 30s while on Tally routes. */}
      <TallyStatusChip />
      {isOpen && <LoginModalCA />}
    </div>
  );
}

function EazyBooksCA() {
  return (
    <GiltThemeProvider>
      <TallyConnectionProvider>
        <EazyBooksShell />
      </TallyConnectionProvider>
    </GiltThemeProvider>
  );
}

export default EazyBooksCA;
