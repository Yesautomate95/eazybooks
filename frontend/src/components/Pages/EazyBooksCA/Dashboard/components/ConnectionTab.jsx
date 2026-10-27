import React from "react";
import { motion } from "framer-motion";
import { Link2 } from "lucide-react";
import { PlatformButton } from "./PlatformButton";
import { PLATFORMS } from "../services/dashboardService";

/**
 * The single connection panel. Renders one tile per platform; the parent
 * page owns the connection state via useConnections() and just plumbs
 * handlers through.
 */
export function ConnectionTab({ statuses, onConnect, onDisconnect, onResync, busyIds = new Set() }) {
  return (
    <motion.section
      className="rounded-xl border border-gilt-hairline bg-gilt-card p-5"
    >
      <div className="mb-4 flex items-start justify-between gap-4 flex-wrap">
        <div className="flex items-center gap-2">
          <span className="inline-flex h-8 w-8 items-center justify-center rounded-lg border border-gilt-hairline bg-gilt-sunken text-gilt-brass">
            <Link2 size={16} />
          </span>
          <div>
            <h2 className="text-sm font-semibold text-gilt-ink">
              Connections
            </h2>
            <p className="mt-0.5 text-xs text-gilt-soft">
              Connect any of your accounting platforms — data flows in automatically.
            </p>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {PLATFORMS.map((p) => (
          <PlatformButton
            key={p.id}
            platform={p}
            state={statuses[p.id]}
            busy={busyIds.has(p.id)}
            onConnect={onConnect}
            onDisconnect={onDisconnect}
            onResync={onResync}
          />
        ))}
      </div>
    </motion.section>
  );
}
