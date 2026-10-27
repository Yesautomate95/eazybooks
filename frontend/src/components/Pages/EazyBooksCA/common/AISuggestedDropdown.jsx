/**
 * AISuggestedDropdown - Enhanced searchable dropdown with AI suggestion support.
 *
 * Shows AI-suggested options pinned at the top with confidence badges,
 * separated from the full alphabetical list. Auto-selects when confidence >= 90%.
 *
 * Props:
 * - options: string[] — full list of ledger names
 * - value: string — currently selected value
 * - onChange: (value: string) => void
 * - onCreateNew: () => void — opens create-new modal
 * - placeholder: string
 * - label: string
 * - aiSuggestion: { recommended_ledger, confidence, reasoning, match_type, alternatives?, create_new?, suggested_new_name?, usage_count?, score_breakdown? } | null
 * - onCreateSuggested: (name: string) => void — called when user clicks "Create New: <name>"
 * - onOverride: (pickedLedger: string, aiSuggestion) => void — called when user picks a ledger different from the AI's suggestion. Parents use this to fire submitSuggestionFeedback with full context (vendor, GSTIN, amount, voucher_type).
 * - loading: boolean — show shimmer while AI is processing
 * - ledgerSource: 'live' | 'cache' — when 'cache', renders an "offline / cached" chip next to the AI suggestion so the user knows Tally wasn't reachable when this suggestion was computed.
 * - cacheRefreshedAt: ISO-8601 string | null — drives the tooltip on the offline chip ("Cached 3 hours ago").
 */

import React, { useState, useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { Search, ChevronDown, Plus, Sparkles, X, RotateCcw, History, Check, AlertTriangle, Info, Fingerprint, Shapes, Bot, WifiOff } from "lucide-react";
import { Tooltip } from "@mui/material";

// Maps backend match_type to a short label, icon, and one-line tooltip.
const MATCH_TYPE_META = {
    history: { label: "History", icon: History, tip: "Matched on this user's past invoices." },
    gstin_exact: { label: "GSTIN match", icon: Fingerprint, tip: "Vendor GSTIN matches a Tally ledger exactly — deterministic." },
    mapping_rule: { label: "By rule", icon: Shapes, tip: "A user-defined mapping rule fired for this transaction." },
    fuzzy: { label: "Fuzzy", icon: Sparkles, tip: "Name-similarity match against existing ledgers." },
    fuzzy_fallback: { label: "Fuzzy", icon: Sparkles, tip: "Name-similarity fallback." },
    llm_assisted: { label: "AI inferred", icon: Bot, tip: "Picked by GPT after fuzzy candidates were ambiguous." },
};

const AUTOFILL_THRESHOLD = 0.9;

// Normalize confidence to 0-100 for display (API returns 0-1)
const toPercent = (c) => (c <= 1 ? Math.round(c * 100) : Math.round(c));

const getConfidenceStyle = (confidence) => {
  const pct = toPercent(confidence);
  if (pct >= 85) return { bg: "bg-emerald-50", text: "text-emerald-700", border: "border-emerald-200", dot: "bg-emerald-500", label: "High match" };
  if (pct >= 50) return { bg: "bg-amber-50", text: "text-amber-700", border: "border-amber-200", dot: "bg-amber-500", label: "Likely match" };
  return { bg: "bg-red-50", text: "text-red-700", border: "border-red-200", dot: "bg-red-500", label: "Low match" };
};

// Compact human-readable "X ago" for the offline-cache chip's tooltip.
// We don't pull in dayjs/date-fns just for this; the suggestion request itself
// is the only place this is shown.
const formatRelativeTime = (iso) => {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const diffSec = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (diffSec < 60) return "just now";
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return `${diffMin} minute${diffMin === 1 ? "" : "s"} ago`;
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return `${diffHr} hour${diffHr === 1 ? "" : "s"} ago`;
  const diffDay = Math.floor(diffHr / 24);
  return `${diffDay} day${diffDay === 1 ? "" : "s"} ago`;
};

const AISuggestedDropdown = ({
  options = [],
  value,
  onChange,
  onCreateNew,
  placeholder = "Select...",
  label,
  aiSuggestion = null,
  onCreateSuggested,
  onOverride,
  loading = false,
  ledgerSource = "live",
  cacheRefreshedAt = null,
  erpLabel = "Tally",
}) => {
  const [isOpen, setIsOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [dismissed, setDismissed] = useState(false);
  const [autoFilled, setAutoFilled] = useState(false);
  const [showWhy, setShowWhy] = useState(false);
  // Dropdown renders into a portal so it isn't clipped by ancestors with
  // `overflow-x-auto` / `overflow-y-auto` (the editor's table containers and
  // resizable panels both have these). Position is computed from the trigger's
  // bounding rect each open/scroll/resize.
  const [dropdownStyle, setDropdownStyle] = useState({});
  const triggerRef = useRef(null);
  const dropdownRef = useRef(null);

  // Wraps onChange. When the user picks a ledger that differs from the AI's
  // suggestion, fires onOverride so parents can record correction feedback.
  // Triggered only on real user interaction (clicking an option), never on
  // the autofill effect — that path doesn't go through this handler.
  const handlePick = (pickedValue) => {
    onChange(pickedValue);
    if (
      onOverride &&
      aiSuggestion &&
      aiSuggestion.recommended_ledger &&
      pickedValue &&
      pickedValue !== aiSuggestion.recommended_ledger
    ) {
      try {
        onOverride(pickedValue, aiSuggestion);
      } catch (_) {
        // never let a feedback handler break the UI
      }
    }
  };

  const filtered = (options || []).filter((o) =>
    o.toLowerCase().includes(search.toLowerCase())
  );

  // Close on outside click — the dropdown is portaled out of the trigger
  // subtree, so we have to check both the trigger and the portaled dropdown
  // explicitly.
  useEffect(() => {
    const handler = (e) => {
      if (triggerRef.current?.contains(e.target)) return;
      if (dropdownRef.current?.contains(e.target)) return;
      setIsOpen(false);
      setSearch("");
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  // Compute the portal'd dropdown's position from the trigger's bounding rect.
  // Flips above the trigger if there isn't enough room below; clamps width to
  // viewport so a near-edge field doesn't render off-screen.
  useEffect(() => {
    if (!isOpen || !triggerRef.current) return;
    const updatePosition = () => {
      const rect = triggerRef.current.getBoundingClientRect();
      const spaceBelow = window.innerHeight - rect.bottom;
      const spaceAbove = rect.top;
      const desired = 384;
      const style = {
        position: "fixed",
        left: rect.left,
        width: Math.max(rect.width, 240),
        zIndex: 9999,
      };
      if (spaceBelow < desired && spaceAbove > spaceBelow) {
        style.bottom = window.innerHeight - rect.top + 4;
        style.maxHeight = Math.max(160, spaceAbove - 16);
      } else {
        style.top = rect.bottom + 4;
        style.maxHeight = Math.max(160, spaceBelow - 16);
      }
      if (style.left + style.width > window.innerWidth - 8) {
        style.left = Math.max(8, window.innerWidth - style.width - 8);
      }
      setDropdownStyle(style);
    };
    updatePosition();
    window.addEventListener("scroll", updatePosition, true);
    window.addEventListener("resize", updatePosition);
    return () => {
      window.removeEventListener("scroll", updatePosition, true);
      window.removeEventListener("resize", updatePosition);
    };
  }, [isOpen]);

  // Reset dismissed/autoFilled state when suggestion changes
  useEffect(() => {
    setDismissed(false);
    setAutoFilled(false);
  }, [aiSuggestion?.recommended_ledger]);

  // Auto-fill at ≥ 90% confidence when the field is empty. The user can
  // immediately undo via the indicator beside the field.
  useEffect(() => {
    if (
      aiSuggestion &&
      aiSuggestion.recommended_ledger &&
      (aiSuggestion.confidence || 0) >= AUTOFILL_THRESHOLD &&
      !value &&
      !dismissed &&
      !autoFilled
    ) {
      onChange(aiSuggestion.recommended_ledger);
      setAutoFilled(true);
    }
  }, [aiSuggestion, value, dismissed, autoFilled, onChange]);

  const hasSuggestion = aiSuggestion && aiSuggestion.recommended_ledger && aiSuggestion.confidence > 0 && !dismissed;
  const isAutoSelected = hasSuggestion && value === aiSuggestion.recommended_ledger;
  const isHistoryMatch = hasSuggestion && aiSuggestion.match_type === "history";
  const showUndo = autoFilled && isAutoSelected;

  // Existence check: does the currently-selected value actually exist in the
  // user's Tally ledger list? Tells the user at a glance whether they're
  // picking an existing ledger or queueing a new one to be created.
  const valueExists =
    !!value &&
    (options || []).some((o) => o.toLowerCase() === value.toLowerCase());
  const showExistence = !!value;

  // Build AI suggestions list (primary + alternatives)
  const aiOptions = [];
  if (hasSuggestion) {
    aiOptions.push({
      name: aiSuggestion.recommended_ledger,
      confidence: aiSuggestion.confidence,
      reasoning: aiSuggestion.reasoning,
      isPrimary: true,
    });
    if (aiSuggestion.alternatives) {
      aiSuggestion.alternatives.forEach((alt) => {
        aiOptions.push({
          name: alt.ledger,
          confidence: alt.confidence,
          reasoning: alt.reason,
          isPrimary: false,
        });
      });
    }
  }

  // Filter AI options by search too
  const filteredAI = aiOptions.filter((o) =>
    o.name.toLowerCase().includes(search.toLowerCase())
  );

  // Exclude AI-suggested names from the main list to avoid duplication
  const aiNames = new Set(aiOptions.map((o) => o.name));
  const filteredMain = filtered.filter((o) => !aiNames.has(o));

  return (
    <div className="relative w-full">
      {/* Selected value display */}
      <div
        ref={triggerRef}
        className={`flex items-center justify-between px-3 py-2 text-sm border rounded-lg cursor-pointer transition-colors focus-within:ring-2 focus-within:ring-indigo-500 focus-within:border-indigo-500 ${
          value ? "border-gray-300 bg-white text-gray-900" : "border-gray-300 bg-white text-gray-400"
        } hover:border-indigo-400`}
        onClick={() => setIsOpen(!isOpen)}
      >
        <div className="flex items-center gap-2 truncate flex-1 min-w-0">
          <span className={`truncate ${value ? "text-gray-900" : "text-gray-400"}`}>
            {value || placeholder}
          </span>
          {/* Existence indicator — green if the value is a real Tally ledger,
              amber if it's a name that will be created on push. Always
              visible when a value is set, regardless of AI involvement. */}
          {showExistence && (
            <Tooltip
              title={
                valueExists
                  ? `This ledger already exists in ${erpLabel}.`
                  : `No ledger with this name exists in ${erpLabel} yet — it will be created when you push.`
              }
              placement="top"
              arrow
            >
              <span
                className={`inline-flex items-center gap-1 px-1.5 py-0.5 text-[10px] font-medium rounded-full border flex-shrink-0 ${
                  valueExists
                    ? "bg-emerald-50 text-emerald-700 border-emerald-200"
                    : "bg-amber-50 text-amber-700 border-amber-200"
                }`}
              >
                {valueExists ? (
                  <>
                    <Check className="h-2.5 w-2.5" />
                    in {erpLabel}
                  </>
                ) : (
                  <>
                    <AlertTriangle className="h-2.5 w-2.5" />
                    new ledger
                  </>
                )}
              </span>
            </Tooltip>
          )}
          {/* AI suggested chip */}
          {isAutoSelected && !dismissed && (() => {
            const meta = MATCH_TYPE_META[aiSuggestion.match_type] || MATCH_TYPE_META.fuzzy;
            const Icon = meta.icon;
            const tipParts = [meta.tip];
            if (isHistoryMatch && aiSuggestion.usage_count) {
              tipParts.push(`Used ${aiSuggestion.usage_count}× for this vendor`);
            }
            if (aiSuggestion.reasoning) {
              tipParts.push(aiSuggestion.reasoning);
            }
            return (
              <Tooltip title={tipParts.filter(Boolean).join(" · ")} placement="top" arrow>
                <span
                  className={`inline-flex items-center gap-1 px-1.5 py-0.5 text-[10px] font-medium rounded-full border flex-shrink-0 ${
                    isHistoryMatch
                      ? "bg-emerald-50 text-emerald-700 border-emerald-200"
                      : "bg-indigo-50 text-indigo-600 border-indigo-200"
                  }`}
                >
                  <Icon className="h-2.5 w-2.5" />
                  {meta.label} {toPercent(aiSuggestion.confidence)}%
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      setShowWhy((v) => !v);
                    }}
                    title="Why this suggestion?"
                    className={isHistoryMatch ? "ml-0.5 hover:text-emerald-800" : "ml-0.5 hover:text-indigo-800"}
                  >
                    <Info className="h-2.5 w-2.5" />
                  </button>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      setDismissed(true);
                    }}
                    className={isHistoryMatch ? "ml-0.5 hover:text-emerald-800" : "ml-0.5 hover:text-indigo-800"}
                  >
                    <X className="h-2.5 w-2.5" />
                  </button>
                </span>
              </Tooltip>
            );
          })()}
          {showUndo && (
            <button
              onClick={(e) => {
                e.stopPropagation();
                onChange("");
                setAutoFilled(false);
              }}
              className="inline-flex items-center gap-0.5 text-[10px] text-gray-500 hover:text-gray-800 flex-shrink-0"
            >
              <RotateCcw className="h-2.5 w-2.5" />
              undo
            </button>
          )}
          {/* Offline-cache chip — only rendered when an AI suggestion is shown
              AND the backend served it from the snapshot rather than a live
              Tally fetch. Tells the user "this came from your last sync"
              without making them dig through tooltips. */}
          {hasSuggestion && ledgerSource === "cache" && (
            <Tooltip
              title={
                cacheRefreshedAt
                  ? `Tally bridge offline — using cached ledgers from ${formatRelativeTime(cacheRefreshedAt)}. Reconnect the desktop app to refresh.`
                  : "Tally bridge offline — using cached ledgers. Reconnect the desktop app to refresh."
              }
              placement="top"
              arrow
            >
              <span className="inline-flex items-center gap-1 px-1.5 py-0.5 text-[10px] font-medium rounded-full border flex-shrink-0 bg-amber-50 text-amber-700 border-amber-200">
                <WifiOff className="h-2.5 w-2.5" />
                cached
              </span>
            </Tooltip>
          )}
        </div>
        {loading ? (
          <div className="h-4 w-4 flex-shrink-0 border-2 border-indigo-300 border-t-transparent rounded-full animate-spin" />
        ) : (
          <ChevronDown className={`h-4 w-4 text-gray-400 flex-shrink-0 transition-transform ${isOpen ? "rotate-180" : ""}`} />
        )}
      </div>

      {/* "Why this suggestion?" panel — opens only when user clicks the Info
          icon on the AI chip. Shows the backend's reasoning, all listed
          alternatives, and the scoring breakdown when present. */}
      {showWhy && hasSuggestion && (
        <div className="mt-1 p-2 text-[11px] bg-indigo-50/60 border border-indigo-100 rounded-md text-indigo-900">
          <div className="flex items-start justify-between gap-2">
            <div className="font-medium">Why this suggestion?</div>
            <button onClick={() => setShowWhy(false)} className="text-indigo-600 hover:text-indigo-800">
              <X className="h-3 w-3" />
            </button>
          </div>
          {aiSuggestion.reasoning && <div className="mt-1">{aiSuggestion.reasoning}</div>}
          {aiSuggestion.score_breakdown && (
            <div className="mt-1 text-[10px] text-indigo-700/80">
              tier {aiSuggestion.score_breakdown.tier ?? "?"} ·
              {" "}{aiSuggestion.score_breakdown.gstin_match ? "GSTIN ✓" : "GSTIN ✗"} ·
              {" "}freq {Math.round((aiSuggestion.score_breakdown.frequency_share || 0) * 100)}% ·
              {" "}{aiSuggestion.score_breakdown.days_since_used ?? "?"}d ago
            </div>
          )}
          {Array.isArray(aiSuggestion.alternatives) && aiSuggestion.alternatives.length > 0 && (
            <div className="mt-2">
              <div className="text-[10px] uppercase tracking-wide text-indigo-700/70 mb-0.5">
                Other candidates
              </div>
              <ul className="space-y-0.5">
                {aiSuggestion.alternatives.slice(0, 3).map((alt, idx) => (
                  <li key={idx} className="flex items-center justify-between">
                    <button
                      onClick={() => {
                        handlePick(alt.ledger);
                        setShowWhy(false);
                      }}
                      className="text-left hover:underline truncate"
                    >
                      {alt.ledger}
                    </button>
                    <span className="ml-2 text-indigo-700/70">{toPercent(alt.confidence)}%</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      {/* Dropdown — portaled to document.body so it escapes the editor's
          overflow-clipped table / panel containers. */}
      {isOpen && createPortal(
        <div
          ref={dropdownRef}
          style={dropdownStyle}
          className="bg-white border border-gray-200 rounded-lg shadow-lg overflow-hidden flex flex-col"
        >
          {/* Search */}
          <div className="p-2 border-b border-gray-100 shrink-0">
            <div className="flex items-center gap-2 px-2 py-1.5 bg-gray-50 rounded-md">
              <Search className="h-3.5 w-3.5 text-gray-400" />
              <input
                type="text"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="flex-1 text-sm bg-transparent outline-none text-gray-900 placeholder-gray-400"
                placeholder="Search..."
                autoFocus
                onClick={(e) => e.stopPropagation()}
              />
              <span className="text-xs text-gray-400">{filteredMain.length + filteredAI.length}</span>
            </div>
          </div>

          <div className="overflow-y-auto flex-1">
            {/* Create New option */}
            {onCreateNew && (
              <div
                className="flex items-center gap-2 px-3 py-2 text-sm text-indigo-600 hover:bg-indigo-50 cursor-pointer border-b border-gray-100"
                onClick={() => {
                  onCreateNew();
                  setIsOpen(false);
                  setSearch("");
                }}
              >
                <Plus className="h-3.5 w-3.5" />
                Create New
              </div>
            )}

            {/* Create Suggested (when AI says no match found) */}
            {aiSuggestion?.create_new && aiSuggestion.suggested_new_name && onCreateSuggested && (
              <div
                className="flex items-center gap-2 px-3 py-2 text-sm text-emerald-600 hover:bg-emerald-50 cursor-pointer border-b border-gray-100"
                onClick={() => {
                  onCreateSuggested(aiSuggestion.suggested_new_name);
                  setIsOpen(false);
                  setSearch("");
                }}
              >
                <Plus className="h-3.5 w-3.5" />
                <span>
                  Create New: <strong>{aiSuggestion.suggested_new_name}</strong>
                </span>
                <span className="ml-auto text-[10px] text-emerald-500 font-medium">AI suggested</span>
              </div>
            )}

            {/* AI Suggestions Section */}
            {filteredAI.length > 0 && (
              <>
                <div className="px-3 py-1.5 text-[10px] font-semibold text-indigo-500 uppercase tracking-wider bg-indigo-50/50 border-b border-indigo-100 flex items-center gap-1.5">
                  <Sparkles className="h-3 w-3" />
                  AI Suggestions
                </div>
                {filteredAI.map((opt, i) => {
                  const style = getConfidenceStyle(opt.confidence);
                  return (
                    <Tooltip
                      key={`ai-${i}`}
                      title={opt.reasoning || ""}
                      placement="left"
                      arrow
                    >
                      <div
                        className={`flex items-center justify-between px-3 py-2 text-sm cursor-pointer hover:bg-indigo-50 ${
                          opt.name === value ? "bg-indigo-50 font-medium text-indigo-700" : "text-gray-900"
                        }`}
                        onClick={() => {
                          handlePick(opt.name);
                          setIsOpen(false);
                          setSearch("");
                        }}
                      >
                        <span className="truncate">{opt.name}</span>
                        <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 text-[10px] font-medium rounded-full border flex-shrink-0 ml-2 ${style.bg} ${style.text} ${style.border}`}>
                          <span className={`w-1.5 h-1.5 rounded-full ${style.dot}`} />
                          {toPercent(opt.confidence)}%
                        </span>
                      </div>
                    </Tooltip>
                  );
                })}
                {/* Separator between AI and full list */}
                {filteredMain.length > 0 && (
                  <div className="px-3 py-1.5 text-[10px] font-semibold text-gray-400 uppercase tracking-wider bg-gray-50 border-y border-gray-100">
                    All Ledgers
                  </div>
                )}
              </>
            )}

            {/* Loading shimmer */}
            {loading && !hasSuggestion && (
              <div className="px-3 py-2 space-y-2">
                <div className="flex items-center gap-2">
                  <Sparkles className="h-3.5 w-3.5 text-indigo-300 animate-pulse" />
                  <span className="text-xs text-indigo-400">Finding best match...</span>
                </div>
                {[1, 2].map((i) => (
                  <div key={i} className="flex items-center justify-between">
                    <div className="h-4 bg-gray-100 rounded animate-pulse" style={{ width: `${60 + i * 15}%` }} />
                    <div className="h-4 w-10 bg-gray-100 rounded animate-pulse" />
                  </div>
                ))}
              </div>
            )}

            {/* Full list */}
            {filteredMain.length === 0 && filteredAI.length === 0 && !loading ? (
              <div className="px-3 py-2 text-sm text-gray-400 text-center">No results</div>
            ) : (
              filteredMain.map((opt, i) => (
                <div
                  key={i}
                  className={`px-3 py-2 text-sm cursor-pointer hover:bg-indigo-50 text-gray-900 ${
                    opt === value ? "bg-indigo-50 font-medium text-indigo-700" : ""
                  }`}
                  onClick={() => {
                    handlePick(opt);
                    setIsOpen(false);
                    setSearch("");
                  }}
                >
                  {opt}
                </div>
              ))
            )}
          </div>
        </div>,
        document.body
      )}
    </div>
  );
};

export default AISuggestedDropdown;
