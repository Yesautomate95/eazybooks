import React, { useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { motion } from "framer-motion";
import { ArrowRight, Sun, Moon } from "lucide-react";
import { PLATFORMS } from "../../../../config/platforms";
import { useWorkspace } from "../../../context/WorkspaceContext";
import { useRbac } from "../../../rbac";
import CoinMark from "../../Landing/gilt/CoinMark";
import useGiltTheme from "../../Landing/gilt/useGiltTheme";
import { cn } from "../../../../lib/utils";
import "../../../../styles/gilt.css";

/**
 * "Choose your ledger" — the post-login workspace selector.
 *
 * Deliberately spare: pick the platform you work in and step inside. Connection
 * state, syncing, OAuth — all of that lives within the workspace (and under
 * Settings → Connections), not here. A platform that isn't linked yet shows its
 * own connect prompt once you're in.
 */
export default function PlatformPicker() {
  const navigate = useNavigate();
  const [theme, toggleTheme] = useGiltTheme();
  const { switchPlatform, isConnected } = useWorkspace();
  const { hasPermission, loading: rbacLoading } = useRbac();

  const visible = PLATFORMS.filter(
    (p) => !p.permission || rbacLoading || hasPermission(p.permission)
  );
  const enter = useCallback((p) => switchPlatform(p.key, navigate), [switchPlatform, navigate]);

  return (
    <div
      className={cn(
        "gl-root gl-grain gl-gilt-edge min-h-screen w-full overflow-y-auto",
        theme === "dark" && "gl-dark"
      )}
    >
      <div className="relative z-10 mx-auto flex min-h-screen max-w-4xl flex-col px-5 py-6 sm:px-8">
        {/* Masthead */}
        <header className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <CoinMark size={24} className="text-gilt-gold" />
            <span className="gl-display text-[19px] leading-none text-gilt-ink">EazyBooks</span>
          </div>
          <button
            type="button"
            onClick={toggleTheme}
            aria-label={theme === "dark" ? "Switch to daylight" : "Switch to lamplight"}
            className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-gilt-hairline bg-gilt-card text-gilt-soft transition-colors hover:border-gilt-brass hover:text-gilt-brass"
          >
            {theme === "dark" ? <Sun size={16} /> : <Moon size={16} />}
          </button>
        </header>

        {/* Heading */}
        <motion.div
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.45, ease: [0.16, 1, 0.3, 1] }}
          className="mt-16 text-center sm:mt-24"
        >
          <p className="gl-eyebrow">Workspace</p>
          <h1 className="gl-display mt-2 text-[34px] leading-[1.1] text-gilt-ink sm:text-[40px]">
            Choose your <span className="gl-wonk text-gilt-brass">ledger</span>
          </h1>
          <p className="mx-auto mt-3 max-w-md text-sm text-gilt-soft">
            Pick the platform you work in. You can switch anytime from the sidebar.
          </p>
        </motion.div>

        {/* Ledger cards */}
        <div className="mx-auto mt-10 grid w-full max-w-3xl grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {visible.map((p, i) => (
            <LedgerCard
              key={p.key}
              platform={p}
              connected={isConnected(p.key)}
              onEnter={() => enter(p)}
              index={i}
            />
          ))}
        </div>

        <div className="flex-1" />
        <footer className="pt-12 text-center text-2xs text-gilt-faint">
          Connect or manage platforms anytime under Settings → Connections.
        </footer>
      </div>
    </div>
  );
}

function LedgerCard({ platform, connected, onEnter, index }) {
  return (
    <motion.button
      type="button"
      onClick={onEnter}
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, delay: 0.06 * index, ease: [0.16, 1, 0.3, 1] }}
      className={cn(
        "group relative flex flex-col items-start overflow-hidden rounded-2xl border border-gilt-hairline bg-gilt-card p-5 text-left",
        "transition-all duration-200 hover:-translate-y-0.5 hover:border-gilt-brass hover:shadow-gl-lift",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-gilt-brass focus-visible:ring-offset-2 focus-visible:ring-offset-gilt-paper"
      )}
    >
      {connected && (
        <span
          title="Connected"
          aria-label="Connected"
          className="absolute right-4 top-4 h-1.5 w-1.5 rounded-full bg-gilt-brass"
        />
      )}

      <span className="flex h-12 w-12 items-center justify-center rounded-xl border border-gilt-hairline bg-gilt-sunken font-mono text-lg font-bold text-gilt-brass">
        {platform.shortLabel.charAt(0)}
      </span>

      <span className="mt-4 gl-display text-lg leading-tight text-gilt-ink">{platform.label}</span>

      <span className="mt-3 inline-flex items-center gap-1.5 text-sm font-medium text-gilt-brass">
        Enter
        <ArrowRight size={15} className="transition-transform duration-200 group-hover:translate-x-1" />
      </span>
    </motion.button>
  );
}
