import React, {
    createContext,
    useCallback,
    useContext,
    useEffect,
    useRef,
    useState,
} from "react";
import { useLocation } from "react-router-dom";
import { tallyApi } from "../../services/api";
import { getToken } from "../../services/auth";

// Routes where the Tally connection chip should be VISIBLE. Note: status now
// streams in the background regardless of route (so the chip is accurate the
// instant the user lands here) — this list only gates the chip's visibility.
const TALLY_ROUTE_PREFIXES = ["/dashboard/tally"];

const API_URL = process.env.REACT_APP_API_URL;

// While the SSE stream is healthy, status is push-based and these don't run.
// They only kick in as a fallback when the stream is down.
const POLL_FALLBACK_MS = 15000;
const SSE_RECONNECT_BASE = 3000;
const SSE_RECONNECT_CAP = 20000;

// When a healthy connection drops, hold "reconnecting" for this long before
// committing to "offline" — absorbs brief bridge reconnects so the chip
// doesn't flap, and gives the honest "reconnecting" state its moment.
const GRACE_MS = 8000;

// Only meaningful in poll-fallback mode; an open SSE stream is self-evidently live.
const STALE_THRESHOLD_MS = 90000;

const TallyConnectionContext = createContext(null);

const DEFAULT_STATE = {
    status: "connecting",   // 'connecting' | 'connected' | 'reconnecting' | 'offline'
    connected: false,       // both legs up (bridge + tally)
    bridge: false,          // desktop app ↔ backend websocket
    tally: false,           // desktop app ↔ Tally (local HTTP)
    company: null,
    method: null,           // 'websocket' | 'direct' | null
    source: null,           // 'sse' | 'poll'
    streaming: false,       // SSE currently open
    checking: false,
    lastChecked: null,      // epoch ms
};

const uidFromToken = (token) => {
    try {
        return JSON.parse(atob(token.split(".")[1])).uid || null;
    } catch (_) {
        return null;
    }
};

export const TallyConnectionProvider = ({ children }) => {
    const location = useLocation();
    const onTallyRoute = TALLY_ROUTE_PREFIXES.some((p) =>
        location.pathname.startsWith(p)
    );

    const [state, setState] = useState(DEFAULT_STATE);

    const mountedRef = useRef(true);
    const esRef = useRef(null);
    const sseStateRef = useRef("connecting"); // 'connecting' | 'open' | 'error'
    const sseBackoffRef = useRef(SSE_RECONNECT_BASE);
    const sseReconnectTimerRef = useRef(null);
    const pollTimerRef = useRef(null);
    const graceTimerRef = useRef(null);
    const everConnectedRef = useRef(false);

    const commit = useCallback((next) => {
        if (!mountedRef.current) return;
        setState((prev) => ({ ...prev, ...next }));
    }, []);

    const cancelOffline = useCallback(() => {
        if (graceTimerRef.current) {
            clearTimeout(graceTimerRef.current);
            graceTimerRef.current = null;
        }
    }, []);

    const scheduleOffline = useCallback(() => {
        cancelOffline();
        graceTimerRef.current = setTimeout(() => {
            commit({ status: "offline" });
        }, GRACE_MS);
    }, [cancelOffline, commit]);

    // Reconcile any incoming status (push or poll) into the state machine.
    const applyPayload = useCallback(
        (p, source) => {
            const connected = !!p.connected;
            const base = {
                connected,
                bridge: !!p.bridge,
                tally: !!p.tally,
                company: p.company || null,
                source,
                checking: false,
                lastChecked: Date.now(),
                method: connected
                    ? source === "poll"
                        ? p.method || "websocket"
                        : "websocket"
                    : null,
            };

            if (connected) {
                everConnectedRef.current = true;
                cancelOffline();
                commit({ ...base, status: "connected" });
                return;
            }

            // Not connected. Once we've had a connection, or we've actively
            // lost the stream, this is a transient "reconnecting" (grace timer
            // downgrades to "offline" if it persists). On a cold start — never
            // connected, stream still coming up or freshly open — it's a
            // first-time "connecting" until the grace window elapses.
            const transient =
                everConnectedRef.current || sseStateRef.current === "error";
            commit({ ...base, status: transient ? "reconnecting" : "connecting" });
            scheduleOffline();
        },
        [cancelOffline, commit, scheduleOffline]
    );

    // ── Poll fallback (only while the SSE stream is down) ──────────────
    const doPoll = useCallback(async () => {
        commit({ checking: true });
        let payload = {
            connected: false,
            bridge: false,
            tally: false,
            company: null,
            method: null,
        };
        try {
            const direct = await tallyApi.checkDirectConnection();
            if (direct?.connected) {
                payload = {
                    connected: true,
                    bridge: true,
                    tally: true,
                    company: direct?.company || direct?.active_company || null,
                    method: "direct",
                };
            }
        } catch (_) {
            /* fall through to websocket probe */
        }
        if (!payload.connected) {
            try {
                const ws = await tallyApi.checkConnection();
                if (ws?.connected) {
                    payload = {
                        connected: true,
                        bridge: true,
                        tally: true,
                        company: ws?.company || ws?.active_company || null,
                        method: "websocket",
                    };
                }
            } catch (_) {
                /* both probes failed — stays disconnected */
            }
        }
        applyPayload(payload, "poll");
    }, [applyPayload, commit]);

    const stopPoll = useCallback(() => {
        if (pollTimerRef.current) {
            clearInterval(pollTimerRef.current);
            pollTimerRef.current = null;
        }
    }, []);

    const startPoll = useCallback(() => {
        if (pollTimerRef.current) return;
        doPoll();
        pollTimerRef.current = setInterval(doPoll, POLL_FALLBACK_MS);
    }, [doPoll]);

    // ── SSE stream (primary, push-based) ───────────────────────────────
    const closeSse = useCallback(() => {
        if (esRef.current) {
            try {
                esRef.current.close();
            } catch (_) {
                /* ignore */
            }
            esRef.current = null;
        }
    }, []);

    const connectSse = useCallback(() => {
        const token = getToken();
        const uid = token && uidFromToken(token);
        if (!token || !uid || !API_URL) return;

        closeSse();
        sseStateRef.current = "connecting";

        const url =
            `${API_URL}/ca/tally/status-stream` +
            `?token=${encodeURIComponent(token)}&uid=${encodeURIComponent(uid)}`;
        const es = new EventSource(url);
        esRef.current = es;

        const onOpen = () => {
            sseStateRef.current = "open";
            sseBackoffRef.current = SSE_RECONNECT_BASE;
            stopPoll();
            commit({ streaming: true });
        };

        es.onopen = onOpen;
        es.addEventListener("status", (e) => {
            onOpen();
            try {
                applyPayload(JSON.parse(e.data), "sse");
            } catch (_) {
                /* ignore malformed frame */
            }
        });

        es.onerror = () => {
            // EventSource would auto-retry with the same URL, but we manage it
            // ourselves so each reconnect uses the freshest token and we can
            // back off. We lost the realtime view → show reconnecting (if we
            // had a connection) and fall back to polling meanwhile.
            sseStateRef.current = "error";
            closeSse();
            commit({ streaming: false });
            setState((prev) =>
                prev.status === "connected"
                    ? { ...prev, status: "reconnecting" }
                    : prev
            );
            startPoll();

            if (sseReconnectTimerRef.current) clearTimeout(sseReconnectTimerRef.current);
            const delay = Math.min(sseBackoffRef.current, SSE_RECONNECT_CAP);
            sseBackoffRef.current = Math.min(
                sseBackoffRef.current * 2,
                SSE_RECONNECT_CAP
            );
            sseReconnectTimerRef.current = setTimeout(
                connectSse,
                delay + Math.floor(Math.random() * 500)
            );
        };
    }, [applyPayload, closeSse, commit, startPoll, stopPoll]);

    // Manual recheck (chip click) — always works, independent of the stream.
    const refresh = useCallback(() => {
        doPoll();
    }, [doPoll]);

    useEffect(() => {
        mountedRef.current = true;
        sseBackoffRef.current = SSE_RECONNECT_BASE;

        // Instant first reading while the stream warms up, then go realtime.
        doPoll();
        connectSse();

        const onVisibility = () => {
            if (document.visibilityState === "visible") {
                if (sseStateRef.current !== "open") connectSse();
                refresh();
            }
        };
        document.addEventListener("visibilitychange", onVisibility);

        return () => {
            mountedRef.current = false;
            document.removeEventListener("visibilitychange", onVisibility);
            closeSse();
            stopPoll();
            if (sseReconnectTimerRef.current) clearTimeout(sseReconnectTimerRef.current);
            if (graceTimerRef.current) clearTimeout(graceTimerRef.current);
        };
    }, [connectSse, doPoll, refresh, closeSse, stopPoll]);

    const isStale =
        !state.streaming &&
        state.lastChecked &&
        Date.now() - state.lastChecked > STALE_THRESHOLD_MS;

    return (
        <TallyConnectionContext.Provider
            value={{
                ...state,
                isStale,
                onTallyRoute,
                refresh,
            }}
        >
            {children}
        </TallyConnectionContext.Provider>
    );
};

// Returns the current Tally connection state. Returns the no-op default when
// used outside the provider so existing components can adopt the hook
// incrementally without crashing if someone forgets to wrap.
export const useTallyConnection = () => {
    const ctx = useContext(TallyConnectionContext);
    if (!ctx) {
        return {
            ...DEFAULT_STATE,
            status: "offline",
            isStale: false,
            onTallyRoute: false,
            refresh: () => {},
        };
    }
    return ctx;
};
