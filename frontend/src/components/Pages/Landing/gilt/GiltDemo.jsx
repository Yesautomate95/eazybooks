import React, { useCallback, useEffect, useRef, useState } from 'react';
import { motion, useReducedMotion } from 'framer-motion';
import { cn } from '../../../../lib/utils';
import CoinMark from './CoinMark';

// "The Counting Room" — a user-driven demo of the real product flow:
// pick a sample document -> run extraction -> rows resolve to Tally
// ledgers with confidence -> push to Tally -> stamped. Unlike the hero's
// auto-playing ledger, nothing here moves until the visitor acts.

const EASE_OUT = [0.16, 1, 0.3, 1];

const SCAN_MS = 1200; // gold scan line sweep
const ROW_LEAD = 250; // pause after the scan before the first row lands
const ROW_STAGGER = 350; // per-row reveal
const PUSH_LEAD = 350; // pause before the first entry posts
const PUSH_STEP = 550; // per-entry posting cadence
const DONE_LAG = 650; // beat between last entry and the stamp

const MAX_ROW_SLOTS = 4; // fixed slots so switching samples never reflows

const inrFormatter = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 });
const formatINR = (value) => `₹${inrFormatter.format(Math.round(value))}`;

const SAMPLES = [
  {
    id: 'bank',
    label: 'Bank Statement',
    kind: 'statement',
    doc: {
      title: 'HDFC Bank · Statement',
      ref: 'A/C ····4521',
      party: ['Sharma Trading Co.', 'Current Account · Jul 2026'],
      items: [
        { l: '02 Jul', m: 'NEFT/RELIANCE RETAIL/PO-2291', r: '2,84,000 Cr', tone: 'credit' },
        { l: '05 Jul', m: 'UPI/JIO FIBER/884521', r: '4,499 Dr', tone: 'debit' },
        { l: '07 Jul', m: 'IMPS/RENT/JUL/MEHTA PROP', r: '85,000 Dr', tone: 'debit' },
      ],
      taxes: [],
      total: { label: 'Closing Balance', value: '₹8,12,406 Cr', tone: 'credit' },
    },
    rows: [
      { field: 'NEFT/RELIANCE RETAIL/PO-2291', ledger: 'Reliance Retail Ltd', side: 'CR', amount: 284000, confidence: 97 },
      { field: 'UPI/JIO FIBER/884521', ledger: 'Jio Fiber A/c', side: 'DR', amount: 4499, confidence: 96 },
      { field: 'IMPS/RENT/JUL/MEHTA PROP', ledger: 'Rent A/c', side: 'DR', amount: 85000, confidence: 98 },
    ],
    total: { label: 'Net Movement', value: 'Cr ₹1,94,501', tone: 'credit' },
  },
  {
    id: 'sales',
    label: 'Sales Invoice',
    kind: 'invoice',
    doc: {
      title: 'Tax Invoice',
      ref: '№ SI-1042',
      party: ['Sharma Trading Co.', 'GSTIN 27AABCS1429B1ZD'],
      items: [
        { l: 'Copper Wire 8mm', m: '120 kg × ₹450', r: '54,000' },
        { l: 'PVC Conduit 25mm', m: '300 m × ₹120', r: '36,000' },
      ],
      taxes: [
        { l: 'Taxable Value', r: '90,000' },
        { l: 'CGST @ 9%', r: '8,100' },
        { l: 'SGST @ 9%', r: '8,100' },
      ],
      total: { label: 'Invoice Total', value: '₹1,06,200' },
    },
    rows: [
      { field: 'Party Ledger', ledger: 'Sharma Trading Co.', confidence: 98 },
      { field: 'Sales Ledger', ledger: 'Sales - Local', amount: 90000, confidence: 95 },
      { field: 'CGST @ 9%', ledger: 'CGST A/c', amount: 8100, confidence: 97 },
      { field: 'SGST @ 9%', ledger: 'SGST A/c', amount: 8100, confidence: 97 },
    ],
    total: { label: 'Invoice Total', value: '₹1,06,200' },
  },
  {
    id: 'purchase',
    label: 'Purchase Invoice',
    kind: 'invoice',
    doc: {
      title: 'Tax Invoice',
      ref: '№ PI-0883',
      party: ['Patel Industries', 'GSTIN 24AAACP3344C1Z7'],
      items: [
        { l: 'MS Angle 50×50', m: '2.0 MT × ₹32,000', r: '64,000' },
        { l: 'MS Flat 40mm', m: '0.5 MT × ₹32,000', r: '16,000' },
      ],
      taxes: [
        { l: 'Taxable Value', r: '80,000' },
        { l: 'IGST @ 18%', r: '14,400' },
      ],
      total: { label: 'Invoice Total', value: '₹94,400' },
    },
    rows: [
      { field: 'Party Ledger', ledger: 'Patel Industries', confidence: 97 },
      { field: 'Purchase Ledger', ledger: 'Purchases - Interstate', amount: 80000, confidence: 94 },
      { field: 'IGST @ 18%', ledger: 'IGST A/c', amount: 14400, confidence: 96 },
      { field: 'TDS 194Q', ledger: 'TDS Payable', amount: 720, confidence: 92 },
    ],
    total: { label: 'Invoice Total', value: '₹94,400' },
  },
];

const RESULT_COLS =
  'grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)_4.5rem] sm:grid-cols-[minmax(0,1fr)_minmax(0,1.25fr)_5.5rem]';
const TH = 'font-mono text-[10px] uppercase tracking-[0.14em] text-gilt-soft';

const makeVariants = (reduced) => ({
  container: {
    hidden: {},
    show: { transition: { staggerChildren: reduced ? 0 : 0.1 } },
  },
  fadeUp: reduced
    ? { hidden: { opacity: 1, y: 0 }, show: { opacity: 1, y: 0 } }
    : {
        hidden: { opacity: 0, y: 24 },
        show: { opacity: 1, y: 0, transition: { duration: 0.7, ease: EASE_OUT } },
      },
});

/* ---------------------------------------------------------------- */
/* Subcomponents                                                     */
/* ---------------------------------------------------------------- */

function SampleChip({ label, selected, onSelect, onKeyDown, chipRef }) {
  return (
    <button
      ref={chipRef}
      type="button"
      role="radio"
      aria-checked={selected}
      tabIndex={selected ? 0 : -1}
      onClick={onSelect}
      onKeyDown={onKeyDown}
      className={cn(
        'inline-flex min-h-[40px] shrink-0 items-center gap-2 whitespace-nowrap rounded-md border px-4 py-2 font-mono text-[11px] uppercase tracking-[0.14em] transition-colors duration-200',
        selected
          ? 'border-gilt-brass bg-gilt-card text-gilt-brass shadow-gl-card'
          : 'border-gilt-hairline text-gilt-soft hover:border-gilt-brass hover:text-gilt-brass'
      )}
    >
      <span
        aria-hidden="true"
        className={cn(
          'inline-block h-1.5 w-1.5 rounded-full transition-colors duration-200',
          selected ? 'bg-gilt-gold' : 'bg-gilt-hairline'
        )}
      />
      {label}
    </button>
  );
}

function PrimaryButton({ children, onClick, disabled, className }) {
  return (
    <button
      type="button"
      aria-disabled={disabled || undefined}
      onClick={disabled ? undefined : onClick}
      className={cn(
        'gl-gilt-edge relative overflow-hidden rounded-lg bg-gilt-ink px-6 py-3.5 font-body text-[15px] font-medium text-gilt-paper shadow-gl-card transition-transform duration-200 ease-gl-out',
        disabled
          ? 'cursor-not-allowed opacity-40'
          : 'hover:-translate-y-0.5 active:translate-y-0',
        className
      )}
    >
      {children}
    </button>
  );
}

/* The source document being read: a bank statement on ruled ledger
   paper with the madder margin rule, or a tiny invoice mock. Fixed
   height so switching samples never shifts the layout. */
function SourcePane({ sample, scanning, reduced }) {
  const { doc } = sample;
  const isStatement = sample.kind === 'statement';

  return (
    <div className="gl-grain relative h-[22rem] overflow-hidden rounded-xl border border-gilt-hairline bg-gilt-card shadow-gl-card">
      {isStatement && (
        // madder-red margin rule, like real ledger paper
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-y-0 left-7 w-px"
          style={{ backgroundColor: 'rgb(var(--gl-debit) / 0.45)' }}
        />
      )}

      <div className={cn('relative flex h-full flex-col px-5 py-5 sm:px-6', isStatement && 'pl-10 sm:pl-11')}>
        <div className="flex items-baseline justify-between gap-3">
          <span className="gl-eyebrow">{doc.title}</span>
          <span className="font-mono text-[10px] text-gilt-soft">{doc.ref}</span>
        </div>

        <div className="mt-3">
          <p className="font-body text-[13px] font-medium text-gilt-ink">{doc.party[0]}</p>
          <p className="mt-0.5 font-mono text-[10px] text-gilt-soft">{doc.party[1]}</p>
        </div>

        <div
          className={cn('mt-3 border-t border-gilt-hairline', isStatement && 'gl-ruled')}
          style={isStatement ? { '--gl-rule-gap': '2.5rem' } : undefined}
        >
          {doc.items.map((item) =>
            isStatement ? (
              <div key={item.m} className="grid h-10 grid-cols-[2.4rem_minmax(0,1fr)_auto] items-center gap-x-2">
                <span className="font-mono text-[9px] uppercase text-gilt-soft">{item.l}</span>
                <span className="min-w-0 truncate font-mono text-[10px] text-gilt-soft sm:text-[11px]">{item.m}</span>
                <span
                  className={cn(
                    'text-right font-mono text-[10px] tabular-nums sm:text-[11px]',
                    item.tone === 'credit' ? 'text-gilt-credit' : 'text-gilt-debit'
                  )}
                >
                  {item.r}
                </span>
              </div>
            ) : (
              <div key={item.l} className="grid h-11 grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 border-b border-gilt-hairline">
                <span className="min-w-0">
                  <span className="block truncate font-body text-xs font-medium text-gilt-ink">{item.l}</span>
                  <span className="block truncate font-mono text-[9px] text-gilt-soft">{item.m}</span>
                </span>
                <span className="text-right font-mono text-[10px] tabular-nums text-gilt-soft">{item.r}</span>
              </div>
            )
          )}
        </div>

        {doc.taxes.length > 0 && (
          <div className="mt-2">
            {doc.taxes.map((tax) => (
              <div key={tax.l} className="flex h-7 items-center justify-between gap-3">
                <span className="font-mono text-[10px] uppercase tracking-[0.08em] text-gilt-soft">{tax.l}</span>
                <span className="font-mono text-[10px] tabular-nums text-gilt-soft">{tax.r}</span>
              </div>
            ))}
          </div>
        )}

        <div className="mt-auto">
          <div className="gl-double-rule flex items-center justify-between gap-3 border-t border-gilt-hairline pb-2 pt-2.5">
            <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-gilt-soft">{doc.total.label}</span>
            <span
              className={cn(
                'font-mono text-xs tabular-nums',
                doc.total.tone === 'credit' ? 'text-gilt-credit' : 'text-gilt-ink'
              )}
            >
              {doc.total.value}
            </span>
          </div>
        </div>
      </div>

      {/* thin gold scan line sweeping top -> bottom while extracting */}
      {scanning && !reduced && (
        <div aria-hidden="true" className="pointer-events-none absolute inset-0 z-[2] overflow-hidden">
          <motion.div
            className="absolute inset-0"
            initial={{ y: '-100%' }}
            animate={{ y: '0%' }}
            transition={{ duration: SCAN_MS / 1000, ease: 'easeInOut' }}
          >
            <div
              className="absolute inset-x-0 bottom-0 h-12"
              style={{ background: 'linear-gradient(to bottom, transparent, rgb(var(--gl-gold) / 0.12))' }}
            />
            <div className="absolute inset-x-0 bottom-0 h-[2px]" style={{ backgroundColor: 'rgb(var(--gl-gold))' }} />
          </motion.div>
        </div>
      )}
    </div>
  );
}

/* The minted-coin confidence badge: reeded (dashed) edge, inner ring,
   center dot — same idiom as the hero ledger. */
function CoinBadge({ confidence, minted, instant }) {
  return (
    // only the coin SVG is decorative — the confidence % is real content
    <motion.span
      className="inline-flex shrink-0 items-center gap-1"
      initial={instant ? false : 'hidden'}
      animate={minted ? 'minted' : 'hidden'}
      variants={{
        hidden: { opacity: 0, scale: 1.4, rotate: -10 },
        minted: { opacity: 1, scale: 1, rotate: 0 },
      }}
      transition={instant ? { duration: 0 } : { type: 'spring', stiffness: 380, damping: 20 }}
    >
      {/* SVG presentation attributes can't take var() — colors go via style */}
      <svg width="15" height="15" viewBox="0 0 20 20" fill="none" className="shrink-0" aria-hidden="true">
        <circle cx="10" cy="10" r="9" strokeWidth="1.4" strokeDasharray="1.1 1.5" style={{ stroke: 'rgb(var(--gl-gold-deep))' }} />
        <circle cx="10" cy="10" r="6.2" strokeWidth="1" style={{ fill: 'rgb(var(--gl-gold) / 0.14)', stroke: 'rgb(var(--gl-gold))' }} />
        <circle cx="10" cy="10" r="1.7" style={{ fill: 'rgb(var(--gl-gold-deep))' }} />
      </svg>
      <span className="font-mono text-[10px] leading-none text-gilt-brass">{confidence}%</span>
    </motion.span>
  );
}

function ResultRow({ row, shown, instant }) {
  const cellTransition = instant ? '' : 'transition-[opacity,transform] duration-500 ease-gl-out';
  const amountText =
    row.amount == null
      ? ''
      : row.side === 'CR'
        ? `Cr ${formatINR(row.amount)}`
        : row.side === 'DR'
          ? `Dr ${formatINR(row.amount)}`
          : formatINR(row.amount);

  return (
    // visibility (not just opacity) so unrevealed rows are also hidden
    // from the accessibility tree until extraction reveals them
    <div
      aria-hidden={!shown}
      className={cn('grid h-12 items-center gap-x-2 sm:gap-x-3', RESULT_COLS)}
      style={{ visibility: shown ? 'visible' : 'hidden' }}
    >
      <span
        className={cn('min-w-0 truncate font-mono text-[10px] text-gilt-soft sm:text-[11px]', cellTransition)}
        style={{
          opacity: shown ? 1 : 0,
          transform: shown ? 'translateY(0)' : 'translateY(4px)',
        }}
      >
        {row.field}
      </span>

      <span className="flex min-w-0 items-center gap-1.5">
        <span
          className={cn(
            'min-w-0 truncate font-body text-xs font-medium text-gilt-ink sm:text-[13px]',
            !instant && 'transition-[opacity,transform] duration-500 ease-gl-spring'
          )}
          style={{
            opacity: shown ? 1 : 0,
            transform: shown ? 'translateX(0)' : 'translateX(-6px)',
          }}
        >
          {row.ledger}
        </span>
        <CoinBadge confidence={row.confidence} minted={shown} instant={instant} />
      </span>

      <span
        className={cn(
          'text-right font-mono text-[10px] tabular-nums sm:text-xs',
          row.side === 'CR' ? 'text-gilt-credit' : row.side === 'DR' ? 'text-gilt-debit' : 'text-gilt-ink',
          cellTransition
        )}
        style={{ opacity: shown ? 1 : 0 }}
      >
        {amountText}
      </span>
    </div>
  );
}

/* The coin-edge as progress bar: hairline track, milled tick marks,
   gold fill animating scaleX only. */
function ReededProgress({ value, instant }) {
  return (
    <div
      aria-hidden="true"
      className="relative h-[7px] overflow-hidden rounded-sm"
      style={{ backgroundColor: 'rgb(var(--gl-hairline) / 0.55)' }}
    >
      <motion.div
        className="absolute inset-0 origin-left"
        style={{ backgroundColor: 'rgb(var(--gl-gold))' }}
        initial={false}
        animate={{ scaleX: value }}
        transition={instant ? { duration: 0 } : { duration: 0.5, ease: EASE_OUT }}
      />
      {/* milled ticks cut through the gold like a reeded coin edge */}
      <div
        className="absolute inset-0"
        style={{
          backgroundImage:
            'repeating-linear-gradient(90deg, rgb(var(--gl-card)) 0 1px, transparent 1px 7px)',
        }}
      />
    </div>
  );
}

function StampMark({ instant }) {
  return (
    <motion.div
      aria-hidden="true"
      className="pointer-events-none absolute inset-0 z-[3] flex items-center justify-center"
      initial={instant ? false : { opacity: 0, scale: 1.4 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={instant ? { duration: 0 } : { type: 'spring', stiffness: 380, damping: 22 }}
    >
      <span
        className="-rotate-[4deg] whitespace-nowrap rounded px-4 py-2 font-mono text-xs font-medium uppercase tracking-[0.2em] text-gilt-brass"
        style={{
          border: '1.5px solid rgb(var(--gl-brass) / 0.8)',
          backgroundColor: 'rgb(var(--gl-paper) / 0.85)',
        }}
      >
        Pushed to Tally &#10003;
      </span>
    </motion.div>
  );
}

/* ---------------------------------------------------------------- */
/* GiltDemo                                                          */
/* ---------------------------------------------------------------- */

export default function GiltDemo({ onGetStarted, className }) {
  const reduced = useReducedMotion();
  const { container, fadeUp } = makeVariants(reduced);

  // idle -> extracting -> review -> pushing -> done
  const [sampleId, setSampleId] = useState(SAMPLES[0].id);
  const [phase, setPhase] = useState('idle');
  const [rowCount, setRowCount] = useState(0); // rows revealed during review
  const [pushed, setPushed] = useState(0); // entries posted during pushing

  const sample = SAMPLES.find((s) => s.id === sampleId) ?? SAMPLES[0];
  const totalRows = sample.rows.length;
  const rowsComplete = rowCount >= totalRows && rowCount > 0;
  const canPush = phase === 'review' && rowsComplete;

  // Every timer lives in this ref; cleared on unmount, reset and re-run,
  // so nothing fires late and nothing sets state after unmount.
  const timersRef = useRef([]);
  const clearTimers = useCallback(() => {
    timersRef.current.forEach(clearTimeout);
    timersRef.current = [];
  }, []);
  useEffect(() => clearTimers, [clearTimers]);

  const schedule = useCallback((fn, ms) => {
    timersRef.current.push(window.setTimeout(fn, ms));
  }, []);

  const selectSample = (id) => {
    if (id === sampleId) return;
    clearTimers();
    setPhase('idle');
    setRowCount(0);
    setPushed(0);
    setSampleId(id);
  };

  const runExtraction = () => {
    if (phase !== 'idle') return;
    clearTimers();
    if (reduced) {
      // same clicks, no motion: everything resolves at once
      setRowCount(totalRows);
      setPhase('review');
      return;
    }
    setPhase('extracting');
    schedule(() => setPhase('review'), SCAN_MS);
    sample.rows.forEach((_, i) => {
      schedule(() => setRowCount(i + 1), SCAN_MS + ROW_LEAD + i * ROW_STAGGER);
    });
  };

  const pushToTally = () => {
    if (!canPush) return;
    clearTimers();
    if (reduced) {
      setPushed(totalRows);
      setPhase('done');
      return;
    }
    setPhase('pushing');
    for (let i = 1; i <= totalRows; i += 1) {
      schedule(() => setPushed(i), PUSH_LEAD + (i - 1) * PUSH_STEP);
    }
    schedule(() => setPhase('done'), PUSH_LEAD + (totalRows - 1) * PUSH_STEP + DONE_LAG);
  };

  const resetDemo = () => {
    clearTimers();
    setPhase('idle');
    setRowCount(0);
    setPushed(0);
  };

  // When 'done' unmounts the focused Push button, hand focus to the reset
  // control so keyboard/SR users don't fall back to <body>. Only when focus
  // was actually dropped — never steal it from elsewhere on the page.
  const resetBtnRef = useRef(null);
  useEffect(() => {
    if (phase === 'done' && document.activeElement === document.body) {
      resetBtnRef.current?.focus();
    }
  }, [phase]);

  // Roving tabindex + arrow keys over the radiogroup
  const chipRefs = useRef([]);
  const onChipKeyDown = (event) => {
    const index = SAMPLES.findIndex((s) => s.id === sampleId);
    let next = null;
    if (event.key === 'ArrowRight' || event.key === 'ArrowDown') next = (index + 1) % SAMPLES.length;
    if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') next = (index - 1 + SAMPLES.length) % SAMPLES.length;
    if (next === null) return;
    event.preventDefault();
    selectSample(SAMPLES[next].id);
    chipRefs.current[next]?.focus();
  };

  const statusText =
    phase === 'extracting' || (phase === 'review' && !rowsComplete)
      ? 'Extracting…'
      : phase === 'review'
        ? `${totalRows} entries matched`
        : phase === 'pushing'
          ? 'Pushing to Tally…'
          : phase === 'done'
            ? 'Pushed to Tally'
            : '';

  const revealTransition = reduced ? '' : 'transition-[opacity,transform] duration-500 ease-gl-out';

  return (
    <section
      id="demo"
      aria-labelledby="demo-heading"
      className={cn('border-y border-gilt-hairline bg-gilt-sunken py-24 lg:py-32', className)}
    >
      <div className="mx-auto max-w-6xl px-6">
        {/* phase announcements for screen readers */}
        <p role="status" aria-live="polite" className="sr-only">
          {statusText}
        </p>

        <motion.div
          variants={container}
          initial="hidden"
          whileInView="show"
          viewport={{ once: true, margin: '-80px' }}
        >
          <motion.p variants={fadeUp} className="gl-eyebrow flex items-center gap-2.5">
            <span aria-hidden="true" className="inline-block h-1.5 w-1.5 rounded-full bg-gilt-gold" />
            Try It Yourself
          </motion.p>
          <motion.h2
            variants={fadeUp}
            id="demo-heading"
            className="gl-display mt-6 max-w-3xl text-gl-h2 text-gilt-ink"
          >
            Run a sample through the <span className="gl-wonk text-gilt-brass">books</span>.
          </motion.h2>
          <motion.p variants={fadeUp} className="mt-5 max-w-2xl font-body text-gl-lead text-gilt-soft">
            Pick a sample document and watch EazyBooks post it. No sign-up needed.
          </motion.p>

          <motion.div
            variants={fadeUp}
            role="radiogroup"
            aria-label="Sample document"
            className="mt-10 flex gap-3 overflow-x-auto pb-1"
          >
            {SAMPLES.map((s, i) => (
              <SampleChip
                key={s.id}
                chipRef={(node) => {
                  chipRefs.current[i] = node;
                }}
                label={s.label}
                selected={s.id === sampleId}
                onSelect={() => selectSample(s.id)}
                onKeyDown={onChipKeyDown}
              />
            ))}
          </motion.div>

          <motion.div variants={fadeUp} className="mt-8 grid items-start gap-8 lg:grid-cols-2">
            {/* left: the source document */}
            <SourcePane sample={sample} scanning={phase === 'extracting'} reduced={reduced} />

            {/* right: extraction results + controls */}
            <div>
              <div className="gl-gilt-edge gl-grain relative overflow-hidden rounded-xl border border-gilt-hairline bg-gilt-card px-5 py-5 shadow-gl-card sm:px-6">
                <div className="flex items-center justify-between gap-3">
                  <span className="flex items-center gap-2">
                    <CoinMark size={16} className="shrink-0 text-gilt-gold" />
                    <span className="gl-eyebrow">Extraction</span>
                  </span>
                  <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-gilt-soft">
                    {sample.label}
                  </span>
                </div>

                <div className={cn('mt-4 grid items-center gap-x-2 border-b border-gilt-hairline pb-1.5 sm:gap-x-3', RESULT_COLS)}>
                  <span className={TH}>Source</span>
                  <span className={TH}>Tally Ledger</span>
                  <span className={cn(TH, 'text-right')}>Amount</span>
                </div>

                {/* fixed-height slots — rows fade in, nothing reflows */}
                <div className="gl-ruled" style={{ '--gl-rule-gap': '3rem' }}>
                  {sample.rows.map((row, i) => (
                    <ResultRow key={`${sample.id}-${row.field}`} row={row} shown={i < rowCount} instant={reduced} />
                  ))}
                  {Array.from({ length: MAX_ROW_SLOTS - sample.rows.length }).map((_, i) => (
                    <div key={`pad-${i}`} className="h-12" aria-hidden="true" />
                  ))}
                </div>

                <div className="h-11">
                  <div
                    aria-hidden={!rowsComplete}
                    className={cn('gl-double-rule grid h-full items-center gap-x-2 sm:gap-x-3', RESULT_COLS, revealTransition)}
                    style={{
                      visibility: rowsComplete ? 'visible' : 'hidden',
                      opacity: rowsComplete ? 1 : 0,
                      transform: rowsComplete ? 'translateY(0)' : 'translateY(4px)',
                    }}
                  >
                    <span className="gl-eyebrow col-span-2">{sample.total.label}</span>
                    <span
                      className={cn(
                        'text-right font-mono text-[10px] tabular-nums sm:text-xs',
                        sample.total.tone === 'credit' ? 'text-gilt-credit' : 'text-gilt-ink'
                      )}
                    >
                      {sample.total.value}
                    </span>
                  </div>
                </div>

                {/* fixed slot for the push caption / the closing annotation */}
                <div className="mt-3 h-12">
                  {phase === 'pushing' && (
                    <div>
                      <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-gilt-soft">
                        Pushing to Tally · {pushed} of {totalRows}
                      </p>
                      <div className="mt-2">
                        <ReededProgress value={totalRows > 0 ? pushed / totalRows : 0} instant={reduced} />
                      </div>
                    </div>
                  )}
                  {phase === 'done' && (
                    <p className="pt-1 font-mono text-[10px] uppercase tracking-[0.14em] text-gilt-soft">
                      Manual entry ~14 min · <span className="text-gilt-brass">EazyBooks 00:09</span>
                    </p>
                  )}
                </div>

                {phase === 'done' && <StampMark instant={reduced} />}
              </div>

              {/* controls — fixed min height so phase changes never reflow */}
              <div className="mt-6 flex min-h-[3.25rem] flex-wrap items-center gap-x-5 gap-y-3">
                {phase === 'done' ? (
                  <>
                    <button
                      ref={resetBtnRef}
                      type="button"
                      onClick={resetDemo}
                      className="font-body text-[15px] font-medium text-gilt-soft transition-colors duration-200 hover:text-gilt-brass"
                    >
                      Run another sample
                    </button>
                    <PrimaryButton onClick={onGetStarted}>Get Started</PrimaryButton>
                  </>
                ) : (
                  <>
                    <PrimaryButton onClick={runExtraction} disabled={phase !== 'idle'}>
                      Run extraction
                    </PrimaryButton>
                    <PrimaryButton onClick={pushToTally} disabled={!canPush}>
                      Push to Tally
                    </PrimaryButton>
                  </>
                )}
              </div>
            </div>
          </motion.div>
        </motion.div>
      </div>
    </section>
  );
}
