/**
 * TallyStatusChip — persistent connection indicator for the Tally workflow.
 *
 * Renders a small chip fixed in the top-right of the viewport whenever the
 * user is on a /dashboard/tally* route. Auto-hides everywhere else. The chip
 * is purely informational: clicking it triggers a manual recheck.
 *
 * Powered by TallyConnectionContext, which streams status over SSE (with a
 * poll fallback). It surfaces four honest states — connecting / connected /
 * reconnecting / offline — rather than a sticky boolean. Per the Gilt Ledger
 * theme: connected is banker's green, transitional states use the spinning
 * coin-edge dial in brass, and OFFLINE is neutral ink — red is reserved for
 * debit and must never signal "offline".
 */

import React from "react";
import { Tooltip } from "@mui/material";
import { Wifi, WifiOff } from "lucide-react";
import { useTallyConnection } from "../../../context/TallyConnectionContext";
import { CoinDial } from "../../../ui/CoinDial";

const formatSeconds = (ms) => {
    if (!ms || ms < 1000) return "just now";
    const s = Math.floor(ms / 1000);
    if (s < 60) return `${s}s ago`;
    const m = Math.floor(s / 60);
    if (m < 60) return `${m}m ago`;
    return `${Math.floor(m / 60)}h ago`;
};

// Visual per status. Note: offline uses neutral ink (gilt-faint / hairline),
// never gilt-debit — red is the debit colour only.
const VARIANTS = {
    connected: {
        text: "text-gilt-credit",
        border: "border-gilt-credit/40",
        dot: "bg-gilt-credit",
        label: "Tally connected",
        dial: false,
    },
    connecting: {
        text: "text-gilt-brass",
        border: "border-gilt-brass/40",
        dot: null, // dial replaces the dot
        label: "Connecting…",
        dial: true,
    },
    reconnecting: {
        text: "text-gilt-brass",
        border: "border-gilt-brass/40",
        dot: null,
        label: "Reconnecting…",
        dial: true,
    },
    offline: {
        text: "text-gilt-faint",
        border: "border-gilt-hairline",
        dot: "bg-gilt-faint",
        label: "Tally offline",
        dial: false,
    },
};

const legLabel = (ok) => (ok ? "connected" : "not connected");

const TallyStatusChip = () => {
    const {
        status, connected, bridge, tally, method, company,
        checking, lastChecked, isStale, onTallyRoute, refresh,
    } = useTallyConnection();

    if (!onTallyRoute) return null;

    const variant = VARIANTS[status] || VARIANTS.offline;

    const lastCheckedAt = lastChecked ? formatSeconds(Date.now() - lastChecked) : "—";
    const methodLabel = method === "direct"
        ? "Direct (localhost)"
        : method === "websocket"
            ? "Desktop bridge"
            : "—";

    const tooltipContent = (
        <div className="text-[11px] leading-snug">
            <div className="font-medium">
                {status === "connected" && "Tally is reachable"}
                {status === "connecting" && "Establishing connection…"}
                {status === "reconnecting" && "Connection lost — recovering…"}
                {status === "offline" && "Cannot reach Tally"}
            </div>
            {/* Two legs surfaced separately so the desktop app and the web app
                can't silently disagree about which link is down (RC4). */}
            <div>Desktop bridge: {legLabel(bridge)}</div>
            <div>Tally (local): {legLabel(tally)}</div>
            <div>Method: {methodLabel}</div>
            {company && <div>Company: {company}</div>}
            <div>Last update: {lastCheckedAt}{isStale ? " (stale)" : ""}</div>
            {(status === "offline" || status === "reconnecting") && (
                <div className="mt-1 text-amber-200">
                    Open the Eazybooks Desktop app and make sure Tally is running.
                </div>
            )}
            <div className="mt-1 opacity-70">Click to recheck.</div>
        </div>
    );

    // Compact mode = healthy + connected: just the dot + Wi-Fi icon, no label,
    // so the chip stays out of the editor toolbar's way. Every other state
    // stays expanded so the user immediately notices.
    const isCompact = status === "connected" && !isStale;

    return (
        <Tooltip title={tooltipContent} arrow placement="bottom-end">
            <button
                type="button"
                onClick={refresh}
                disabled={checking}
                aria-label={`Tally connection: ${variant.label}. Click to recheck.`}
                className={`fixed top-3 right-4 z-50 inline-flex items-center gap-1.5 rounded-full border bg-gilt-card transition-opacity disabled:cursor-wait hover:opacity-90 ${variant.text} ${variant.border} ${
                    isCompact
                        ? "px-1.5 py-1"
                        : "px-2.5 py-1 font-mono text-[10px] uppercase tracking-[0.14em]"
                }`}
            >
                {variant.dial ? (
                    <CoinDial size={12} />
                ) : (
                    <>
                        <span className={`w-2 h-2 rounded-full ${variant.dot}`} />
                        {status === "connected"
                            ? <Wifi className="h-3 w-3" />
                            : <WifiOff className="h-3 w-3" />}
                    </>
                )}
                {!isCompact && <span>{variant.label}</span>}
                {!isCompact && status === "connected" && company && (
                    <span className="text-gilt-faint font-normal max-w-[140px] truncate">
                        · {company}
                    </span>
                )}
            </button>
        </Tooltip>
    );
};

export default TallyStatusChip;
