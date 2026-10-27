import React, { useCallback, useEffect, useRef, useState } from 'react';
import { motion, AnimatePresence, useReducedMotion, animate } from 'framer-motion';
import { Pause, Play } from 'lucide-react';
import { cn } from '../../../../lib/utils';

// The hero centerpiece: a self-running ledger that watches raw bank lines
// get minted into Tally ledgers, balance to zero, and take the seal.

const inrFormatter = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 });
const formatINR = (value) => `₹${inrFormatter.format(Math.round(value))}`;

const ROWS = [
  { raw: 'NEFT/AXIS000812/TATA STEEL/PO-1182', ledger: 'Tata Steel Ltd', side: 'CR', amount: 472500, confidence: 98 },
  { raw: 'UPI/BHARTI AIRTEL/9921837465', ledger: 'Bharti Airtel Ltd', side: 'DR', amount: 11820, confidence: 96 },
  { raw: 'IMPS/PAYROLL/JUN/SALARY BATCH', ledger: 'Salary A/c', side: 'DR', amount: 386400, confidence: 94 },
  { raw: 'NEFT/GSTN/3B-052026/CHLN 88231', ledger: 'GST Payable', side: 'DR', amount: 74280, confidence: 97 },
];
const TOTAL = 472500; // Dr 11,820 + 3,86,400 + 74,280 = Cr 4,72,500

const HIDDEN = 0;
const RAW = 1;
const MINTED = 2;

const INITIAL_STAGE = { rows: [HIDDEN, HIDDEN, HIDDEN, HIDDEN], totals: false, diff: false, seal: false };
const FINAL_STAGE = { rows: [MINTED, MINTED, MINTED, MINTED], totals: true, diff: true, seal: true };

const setRow = (index, value) => (stage) => {
  const rows = stage.rows.slice();
  rows[index] = value;
  return { ...stage, rows };
};

// [ms after cycle start, stage updater]
const TIMELINE = [
  [400, setRow(0, RAW)],
  [1300, setRow(0, MINTED)],
  [1450, setRow(1, RAW)],
  [2350, setRow(1, MINTED)],
  [2500, setRow(2, RAW)],
  [3400, setRow(2, MINTED)],
  [3550, setRow(3, RAW)],
  [4450, setRow(3, MINTED)],
  [5500, (s) => ({ ...s, totals: true })],
  [6300, (s) => ({ ...s, diff: true })],
  [7000, (s) => ({ ...s, seal: true })],
];
const CYCLE_END_MS = 10500; // seal at 7s + ~3.5s hold, then the page turns

const GRID_COLS =
  'grid-cols-[minmax(0,1.25fr)_minmax(0,1fr)_3.9rem_4.6rem] sm:grid-cols-[minmax(0,1.25fr)_minmax(0,1fr)_5rem_5.5rem]';
const TH = 'font-mono text-[10px] uppercase tracking-[0.14em] text-gilt-soft';

// Counts up by writing textContent into a ref'd span — no per-frame re-renders.
// Children stay a stable "₹0" so React reconciliation never clobbers the counter.
function AmountCounter({ value, active, instant, className }) {
  const ref = useRef(null);

  useEffect(() => {
    if (!active || instant) return undefined;
    const node = ref.current;
    const controls = animate(0, value, {
      duration: 0.6,
      ease: 'easeOut',
      onUpdate: (latest) => {
        if (node) node.textContent = formatINR(latest);
      },
    });
    return () => controls.stop();
  }, [active, instant, value]);

  return (
    <span ref={ref} className={cn('tabular-nums', className)}>
      {instant ? formatINR(value) : formatINR(0)}
    </span>
  );
}

// A minted coin: reeded (dashed) edge, inner ring, center dot.
function CoinBadge({ confidence, minted, instant }) {
  return (
    <motion.span
      className="inline-flex shrink-0 items-center gap-1"
      initial={instant ? false : 'hidden'}
      animate={minted ? 'minted' : 'hidden'}
      variants={{
        hidden: { opacity: 0, scale: 1.4, rotate: -10 },
        minted: { opacity: 1, scale: 1, rotate: 0 },
      }}
      transition={{ type: 'spring', stiffness: 380, damping: 20 }}
    >
      {/* SVG presentation attributes can't take var() — colors go via style */}
      <svg width="15" height="15" viewBox="0 0 20 20" fill="none" className="shrink-0">
        <circle cx="10" cy="10" r="9" strokeWidth="1.4" strokeDasharray="1.1 1.5" style={{ stroke: 'rgb(var(--gl-gold-deep))' }} />
        <circle cx="10" cy="10" r="6.2" strokeWidth="1" style={{ fill: 'rgb(var(--gl-gold) / 0.14)', stroke: 'rgb(var(--gl-gold))' }} />
        <circle cx="10" cy="10" r="1.7" style={{ fill: 'rgb(var(--gl-gold-deep))' }} />
      </svg>
      <span className="font-mono text-[10px] leading-none text-gilt-brass">{confidence}%</span>
    </motion.span>
  );
}

function LedgerRow({ row, state, instant }) {
  const shown = state >= RAW;
  const minted = state >= MINTED;
  const cellTransition = 'transition-[opacity,transform] duration-500 ease-gl-out';

  return (
    <div className={cn('grid h-10 items-center gap-x-2 sm:gap-x-3', GRID_COLS)}>
      <span
        className={cn('min-w-0 truncate font-mono text-[10px] text-gilt-soft sm:text-[11px]', cellTransition)}
        style={{
          // recede once minted, but never below the AA-contrast floor
          opacity: shown ? (minted ? 0.8 : 1) : 0,
          transform: shown ? 'translateY(0)' : 'translateY(4px)',
        }}
      >
        {row.raw}
      </span>

      <span className="flex min-w-0 items-center gap-1.5">
        <span
          className="min-w-0 truncate font-body text-xs font-medium text-gilt-ink transition-[opacity,transform] duration-500 ease-gl-spring sm:text-[13px]"
          style={{
            opacity: minted ? 1 : 0,
            transform: minted ? 'translateX(0)' : 'translateX(-6px)',
          }}
        >
          {row.ledger}
        </span>
        <CoinBadge confidence={row.confidence} minted={minted} instant={instant} />
      </span>

      <span
        className={cn('text-right font-mono text-[10px] text-gilt-debit sm:text-xs', cellTransition)}
        style={{ opacity: minted && row.side === 'DR' ? 1 : 0 }}
      >
        {row.side === 'DR' && <AmountCounter value={row.amount} active={minted} instant={instant} />}
      </span>
      <span
        className={cn('text-right font-mono text-[10px] text-gilt-credit sm:text-xs', cellTransition)}
        style={{ opacity: minted && row.side === 'CR' ? 1 : 0 }}
      >
        {row.side === 'CR' && <AmountCounter value={row.amount} active={minted} instant={instant} />}
      </span>
    </div>
  );
}

// Gold seal re-cutting the brand's orbit mark, pressed into the paper.
function SealStamp({ stamped, instant }) {
  return (
    <motion.div
      className="pointer-events-none absolute bottom-0 right-1 flex flex-col items-center gap-1.5"
      initial={instant ? false : 'hidden'}
      animate={stamped ? 'stamped' : 'hidden'}
      variants={{
        hidden: { opacity: 0, scale: 1.6, rotate: 4 },
        stamped: { opacity: 1, scale: 1, rotate: -4 },
      }}
      transition={{ type: 'spring', stiffness: 420, damping: 22 }}
    >
      <svg width="60" height="60" viewBox="0 0 84 84" fill="none">
        <circle cx="42" cy="42" r="36" strokeWidth="2.4" strokeDasharray="2 2.6" style={{ fill: 'rgb(var(--gl-gold-hi) / 0.1)', stroke: 'rgb(var(--gl-gold-deep))' }} />
        <circle cx="42" cy="42" r="27" strokeWidth="1.5" style={{ stroke: 'rgb(var(--gl-gold))' }} />
        <path d="M42 8v5M42 71v5M8 42h5M71 42h5" strokeWidth="1.5" strokeLinecap="round" style={{ stroke: 'rgb(var(--gl-gold))' }} />
        <circle cx="42" cy="42" r="4" style={{ fill: 'rgb(var(--gl-gold-deep))' }} />
      </svg>
      <span
        className="whitespace-nowrap px-2 py-[3px] font-mono text-[9px] uppercase tracking-[0.18em] text-gilt-brass"
        style={{ border: '1.5px solid rgb(var(--gl-brass) / 0.7)', borderRadius: '3px' }}
      >
        Pushed to Tally &#10003;
      </span>
    </motion.div>
  );
}

function LedgerScene({ instant, onComplete }) {
  const [stage, setStage] = useState(instant ? FINAL_STAGE : INITIAL_STAGE);
  const view = instant ? FINAL_STAGE : stage;
  const fadeUp = 'transition-[opacity,transform] duration-500 ease-gl-out';

  useEffect(() => {
    if (instant) return undefined;
    const timers = TIMELINE.map(([at, step]) => setTimeout(() => setStage(step), at));
    if (onComplete) timers.push(setTimeout(onComplete, CYCLE_END_MS));
    return () => timers.forEach(clearTimeout);
  }, [instant, onComplete]);

  return (
    <div>
      <div className="flex items-baseline justify-between gap-3">
        <span className="gl-eyebrow">Bank Statement &middot; June 2026</span>
        <span className="font-mono text-[11px] text-gilt-soft">HDFC BANK &middot;&middot;&middot;&middot;4521</span>
      </div>

      <div className={cn('mt-4 grid items-center gap-x-2 border-b border-gilt-hairline pb-1.5 sm:gap-x-3', GRID_COLS)}>
        <span className={TH}>Particulars</span>
        <span className={TH}>Ledger</span>
        <span className={cn(TH, 'text-right')}>Dr</span>
        <span className={cn(TH, 'text-right')}>Cr</span>
      </div>

      <div className="gl-ruled" style={{ '--gl-rule-gap': '2.5rem' }}>
        {ROWS.map((row, i) => (
          <LedgerRow key={row.raw} row={row} state={view.rows[i]} instant={instant} />
        ))}
      </div>

      <div className="h-11">
        <div
          className={cn('gl-double-rule grid h-full items-center gap-x-2 sm:gap-x-3', GRID_COLS, fadeUp)}
          style={{
            opacity: view.totals ? 1 : 0,
            transform: view.totals ? 'translateY(0)' : 'translateY(4px)',
          }}
        >
          <span className="gl-eyebrow col-span-2">Totals</span>
          <span className="text-right font-mono text-[10px] text-gilt-debit sm:text-xs">
            <AmountCounter value={TOTAL} active={view.totals} instant={instant} />
          </span>
          <span className="text-right font-mono text-[10px] text-gilt-credit sm:text-xs">
            <AmountCounter value={TOTAL} active={view.totals} instant={instant} />
          </span>
        </div>
      </div>

      {/* footer zone reserves room for the seal so the card never grows */}
      <div className="relative h-24">
        <div
          className={cn('flex items-center gap-2 pt-2.5', fadeUp)}
          style={{
            opacity: view.diff ? 1 : 0,
            transform: view.diff ? 'translateY(0)' : 'translateY(4px)',
          }}
        >
          <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-gilt-soft">Difference</span>
          <span className="font-mono text-[11px] tabular-nums text-gilt-credit">&#8377;0.00</span>
          <svg width="10" height="10" viewBox="0 0 12 12" fill="none" className="text-gilt-credit">
            <path d="M2 6.5l2.6 2.6L10 3.5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </div>

        <SealStamp stamped={view.seal} instant={instant} />
      </div>
    </div>
  );
}

export default function SelfBalancingLedger({ className }) {
  const prefersReducedMotion = useReducedMotion();
  const [paused, setPaused] = useState(false);
  const [cycle, setCycle] = useState(0);
  const handleComplete = useCallback(() => setCycle((c) => c + 1), []);
  // paused jumps to the completed ledger, same as the reduced-motion path
  const isStatic = prefersReducedMotion || paused;

  return (
    <div className={cn('w-full', className)}>
      <div
        role="img"
        aria-label="Animated demo: bank statement lines being matched to Tally ledgers, balancing to zero, and pushed to Tally."
        className="gl-grain gl-gilt-edge relative overflow-hidden rounded-xl border border-gilt-hairline bg-gilt-card shadow-gl-card"
      >
        <div aria-hidden="true">
          {/* madder-red margin rule, like real ledger paper */}
          <div
            className="pointer-events-none absolute inset-y-0 left-10 w-px"
            style={{ backgroundColor: 'rgb(var(--gl-debit) / 0.45)' }}
          />

          {/* grid-stack so the entering and exiting scenes overlap — height never collapses */}
          <div className="grid py-5 pl-[3.25rem] pr-4 sm:py-6 sm:pr-6">
            {isStatic ? (
              <div className="[grid-area:1/1]">
                <LedgerScene instant />
              </div>
            ) : (
              <AnimatePresence initial={false}>
                <motion.div
                  key={cycle}
                  className="[grid-area:1/1]"
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0, transition: { duration: 0.3, ease: 'easeOut', delay: 0.15 } }}
                  exit={{ opacity: 0, y: -8, transition: { duration: 0.25, ease: 'easeIn' } }}
                >
                  <LedgerScene instant={false} onComplete={handleComplete} />
                </motion.div>
              </AnimatePresence>
            )}
          </div>
        </div>
      </div>

      {/* WCAG 2.2.2: on-page mechanism to stop auto-playing motion */}
      {!prefersReducedMotion && (
        <div className="mt-2 flex justify-end">
          <button
            type="button"
            aria-pressed={paused}
            onClick={() => setPaused((p) => !p)}
            className="inline-flex items-center gap-1.5 rounded px-2 py-1 font-mono text-[10px] uppercase tracking-[0.14em] text-gilt-soft transition-colors duration-200 hover:text-gilt-ink"
          >
            {paused ? <Play size={11} aria-hidden="true" /> : <Pause size={11} aria-hidden="true" />}
            {paused ? 'Play demo' : 'Pause demo'}
          </button>
        </div>
      )}
    </div>
  );
}
