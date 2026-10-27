import React, { useCallback, useEffect, useRef, useState } from "react";
import CoinMark from "../Pages/Landing/gilt/CoinMark";
import { cn } from "../../lib/utils";

// Circular progress geometry for the seal ring (r = 9).
const RING_R = 9;
const RING_C = 2 * Math.PI * RING_R; // ≈ 56.55

/**
 * PressTheSeal — the Gilt Ledger destructive-confirmation gesture.
 *
 * Red is reserved for debit/financial meaning, so a consequential action is
 * committed by pressing AND HOLDING the wax seal: a brass progress ring sweeps
 * around the coin while a brass wash fills the button, and the action fires
 * only if the hold completes. Releasing early cancels — deliberate, hard to do
 * by accident, and on-theme. Works with pointer and keyboard (hold Enter/Space).
 */
export function PressTheSeal({
  onConfirm,
  label = "Press & hold the seal",
  holdingLabel = "Keep holding…",
  holdMs = 900,
  disabled = false,
  className,
}) {
  const [holding, setHolding] = useState(false);
  const timerRef = useRef(null);

  const cancel = useCallback(() => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    setHolding(false);
  }, []);

  const start = useCallback(() => {
    if (disabled || timerRef.current) return;
    setHolding(true);
    timerRef.current = setTimeout(() => {
      timerRef.current = null;
      setHolding(false);
      onConfirm?.();
    }, holdMs);
  }, [disabled, holdMs, onConfirm]);

  useEffect(() => () => timerRef.current && clearTimeout(timerRef.current), []);

  return (
    <button
      type="button"
      disabled={disabled}
      onPointerDown={(e) => {
        e.preventDefault();
        start();
      }}
      onPointerUp={cancel}
      onPointerLeave={cancel}
      onKeyDown={(e) => {
        if ((e.key === "Enter" || e.key === " ") && !e.repeat) {
          e.preventDefault();
          start();
        }
      }}
      onKeyUp={cancel}
      onBlur={cancel}
      aria-label={label}
      className={cn(
        "relative inline-flex select-none items-center justify-center gap-2 overflow-hidden rounded-md",
        "border border-gilt-brass/60 bg-gilt-card px-4 py-2 text-sm font-semibold text-gilt-brass",
        "transition-[transform,border-color] duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-gilt-brass focus-visible:ring-offset-2 focus-visible:ring-offset-gilt-paper",
        "disabled:cursor-not-allowed disabled:opacity-50",
        holding && "scale-[0.99] border-gilt-brass",
        className
      )}
    >
      {/* Brass wash that fills over the hold. */}
      <span
        aria-hidden="true"
        className="absolute inset-y-0 left-0 bg-gilt-brass/15"
        style={{
          width: holding ? "100%" : "0%",
          transition: `width ${holding ? holdMs : 220}ms linear`,
        }}
      />

      {/* The wax seal: coin + circular progress ring. */}
      <span className="relative inline-flex h-[22px] w-[22px] shrink-0 items-center justify-center">
        <svg
          width="22"
          height="22"
          viewBox="0 0 22 22"
          className="absolute inset-0 -rotate-90"
          aria-hidden="true"
        >
          <circle cx="11" cy="11" r={RING_R} fill="none" stroke="currentColor" strokeOpacity="0.2" strokeWidth="2" />
          <circle
            cx="11"
            cy="11"
            r={RING_R}
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeDasharray={RING_C}
            strokeDashoffset={holding ? 0 : RING_C}
            style={{ transition: `stroke-dashoffset ${holding ? holdMs : 220}ms linear` }}
          />
        </svg>
        <CoinMark size={12} className="relative" />
      </span>

      <span className="relative whitespace-nowrap">{holding ? holdingLabel : label}</span>
    </button>
  );
}

export default PressTheSeal;
