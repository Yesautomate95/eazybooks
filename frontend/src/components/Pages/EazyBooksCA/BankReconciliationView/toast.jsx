import React, { useEffect, useState } from "react";
import { CheckCircle, AlertTriangle, Info, X } from "lucide-react";

// Lightweight, dependency-free toast for the bank-recon flow. The app has no
// toast library, so this is a tiny singleton + a <ToastHost/> that renders the
// stack. Call toast.success/error/info from anywhere; mount <ToastHost/> once.

let _id = 0;
let _toasts = [];
const _listeners = new Set();

function _emit() {
  for (const l of _listeners) l([..._toasts]);
}

function dismissToast(id) {
  _toasts = _toasts.filter((t) => t.id !== id);
  _emit();
}

function push(type, message, ttl) {
  if (!message) return null;
  const id = ++_id;
  _toasts = [..._toasts, { id, type, message: String(message) }];
  _emit();
  if (ttl !== 0) {
    setTimeout(() => dismissToast(id), ttl ?? (type === "error" ? 6000 : 4000));
  }
  return id;
}

export const toast = {
  success: (message, ttl) => push("success", message, ttl),
  error: (message, ttl) => push("error", message, ttl),
  info: (message, ttl) => push("info", message, ttl),
  dismiss: dismissToast,
};

// Pull a human-readable message out of an axios error (FastAPI puts it on
// response.data.detail), falling back to a default.
export function errMsg(e, fallback = "Something went wrong.") {
  return (
    e?.response?.data?.detail ||
    e?.response?.data?.error ||
    e?.message ||
    fallback
  );
}

const STYLES = {
  success: { ring: "border-gilt-credit/40", bg: "bg-gilt-card", text: "text-gilt-ink", Icon: CheckCircle, icon: "text-gilt-credit" },
  error: { ring: "border-gilt-debit/40", bg: "bg-gilt-card", text: "text-gilt-ink", Icon: AlertTriangle, icon: "text-gilt-debit" },
  info: { ring: "border-gilt-hairline", bg: "bg-gilt-card", text: "text-gilt-ink", Icon: Info, icon: "text-gilt-brass" },
};

export function ToastHost() {
  const [items, setItems] = useState([]);
  useEffect(() => {
    _listeners.add(setItems);
    setItems([..._toasts]);
    return () => _listeners.delete(setItems);
  }, []);

  if (items.length === 0) return null;

  return (
    <div className="fixed bottom-4 right-4 z-[100] flex flex-col gap-2 w-80 max-w-[calc(100vw-2rem)]">
      {items.map((t) => {
        const s = STYLES[t.type] || STYLES.info;
        const Icon = s.Icon;
        return (
          <div
            key={t.id}
            role="status"
            className={`flex items-start gap-2.5 rounded-lg border ${s.ring} ${s.bg} px-3.5 py-3 shadow-sm animate-[fadeIn_0.15s_ease-out]`}
          >
            <Icon size={18} className={`${s.icon} flex-shrink-0 mt-0.5`} />
            <p className={`text-sm ${s.text} flex-1 leading-snug`}>{t.message}</p>
            <button
              onClick={() => dismissToast(t.id)}
              className="p-0.5 rounded hover:bg-gilt-sunken transition-colors duration-150 flex-shrink-0"
              aria-label="Dismiss"
            >
              <X size={14} className="text-gilt-faint" />
            </button>
          </div>
        );
      })}
    </div>
  );
}
