import React, { useState, useEffect, useRef, useMemo } from "react";
import { createPortal } from "react-dom";
import {
  Search,
  X,
  ChevronDown,
  Check,
  Clock,
  Loader2,
  AlertCircle,
  RefreshCw,
  Plus,
} from "lucide-react";

const RECENT_LEDGERS_KEY = "aureus_recent_ledgers";

function SearchableSelect({
  value,
  options = [],
  onChange,
  placeholder = "Select...",
  isLoading = false,
  error = null,
  onRetry,
  onCreateNew,
  className = "",
  disabled = false,
  id,
  activeDropdownId,
  onDropdownToggle,
}) {
  const [isOpen, setIsOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [highlightedIndex, setHighlightedIndex] = useState(0);
  const [dropdownStyle, setDropdownStyle] = useState({});

  const triggerRef = useRef(null);
  const searchInputRef = useRef(null);
  const listRef = useRef(null);

  const createOffset = onCreateNew ? 1 : 0;

  // Close if another dropdown opened
  useEffect(() => {
    if (activeDropdownId !== undefined && activeDropdownId !== id && isOpen) {
      setIsOpen(false);
    }
  }, [activeDropdownId, id, isOpen]);

  // Recent selections from sessionStorage
  const recentSelections = useMemo(() => {
    try {
      const stored = sessionStorage.getItem(RECENT_LEDGERS_KEY);
      return stored ? JSON.parse(stored) : [];
    } catch {
      return [];
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen]);

  // Filtered options
  const filteredOptions = useMemo(() => {
    if (!searchQuery.trim()) return options;
    const q = searchQuery.toLowerCase().trim();
    return options.filter((opt) => opt.toLowerCase().includes(q));
  }, [options, searchQuery]);

  // Recent that exist in current options list (only shown when not searching)
  const recentInOptions = useMemo(() => {
    if (searchQuery) return [];
    return recentSelections
      .filter((r) => options.includes(r))
      .slice(0, 8);
  }, [recentSelections, options, searchQuery]);

  // Close on outside click
  useEffect(() => {
    if (!isOpen) return;
    const handleClick = (e) => {
      if (triggerRef.current && triggerRef.current.contains(e.target)) return;
      const dropdown = document.getElementById(`ss-dropdown-${id}`);
      if (dropdown && dropdown.contains(e.target)) return;
      setIsOpen(false);
    };
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [isOpen, id]);

  // Position dropdown relative to trigger
  useEffect(() => {
    if (!isOpen || !triggerRef.current) return;
    const updatePosition = () => {
      const rect = triggerRef.current.getBoundingClientRect();
      const spaceBelow = window.innerHeight - rect.bottom;
      const style = {
        position: "fixed",
        left: rect.left,
        width: Math.max(rect.width, 280),
        zIndex: 9999,
      };
      if (spaceBelow < 320 && rect.top > spaceBelow) {
        style.bottom = window.innerHeight - rect.top + 4;
      } else {
        style.top = rect.bottom + 4;
      }
      // Keep within viewport horizontally
      if (style.left + style.width > window.innerWidth - 8) {
        style.left = window.innerWidth - style.width - 8;
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

  // Focus search when opened, reset when closed
  useEffect(() => {
    if (isOpen) {
      setTimeout(() => searchInputRef.current?.focus(), 30);
    } else {
      setSearchQuery("");
      setHighlightedIndex(0);
    }
  }, [isOpen]);

  // Scroll highlighted item into view
  useEffect(() => {
    if (!listRef.current || !isOpen) return;
    const items = listRef.current.querySelectorAll("[data-ss-option]");
    const highlighted = items[highlightedIndex];
    if (highlighted) {
      highlighted.scrollIntoView({ block: "nearest" });
    }
  }, [highlightedIndex, isOpen]);

  const handleSelect = (val) => {
    onChange(val);
    setIsOpen(false);
    // Update recent selections
    try {
      const recent = JSON.parse(
        sessionStorage.getItem(RECENT_LEDGERS_KEY) || "[]"
      );
      const updated = [val, ...recent.filter((r) => r !== val)].slice(0, 10);
      sessionStorage.setItem(RECENT_LEDGERS_KEY, JSON.stringify(updated));
    } catch {}
  };

  const handleClear = (e) => {
    e.stopPropagation();
    onChange("");
  };

  const toggleOpen = () => {
    if (disabled) return;
    const next = !isOpen;
    setIsOpen(next);
    if (next && onDropdownToggle) {
      onDropdownToggle(id);
    }
  };

  const handleKeyDown = (e) => {
    const totalItems = createOffset + filteredOptions.length;
    if (totalItems === 0) return;

    switch (e.key) {
      case "ArrowDown":
        e.preventDefault();
        setHighlightedIndex((prev) =>
          prev < totalItems - 1 ? prev + 1 : 0
        );
        break;
      case "ArrowUp":
        e.preventDefault();
        setHighlightedIndex((prev) =>
          prev > 0 ? prev - 1 : totalItems - 1
        );
        break;
      case "Enter":
        e.preventDefault();
        if (onCreateNew && highlightedIndex === 0) {
          onCreateNew();
          setIsOpen(false);
        } else {
          const optIdx = highlightedIndex - createOffset;
          if (filteredOptions[optIdx]) {
            handleSelect(filteredOptions[optIdx]);
          }
        }
        break;
      case "Escape":
        e.preventDefault();
        setIsOpen(false);
        triggerRef.current?.focus();
        break;
      case "Tab": {
        const tabIdx = highlightedIndex - createOffset;
        if (tabIdx >= 0 && filteredOptions[tabIdx]) {
          handleSelect(filteredOptions[tabIdx]);
        }
        setIsOpen(false);
        break;
      }
      default:
        break;
    }
  };

  const valueExists = !value || options.includes(value);

  const dropdown =
    isOpen &&
    createPortal(
      <div
        id={`ss-dropdown-${id}`}
        style={dropdownStyle}
        className="bg-gilt-card rounded-lg shadow-sm border border-gilt-hairline flex flex-col overflow-hidden"
        role="listbox"
        aria-label="Ledger options"
      >
        {/* Search input */}
        <div className="p-2 border-b border-gilt-hairline">
          <div className="relative">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-gilt-faint" />
            <input
              ref={searchInputRef}
              type="text"
              value={searchQuery}
              onChange={(e) => {
                setSearchQuery(e.target.value);
                setHighlightedIndex(createOffset); // reset to first option
              }}
              onKeyDown={handleKeyDown}
              placeholder="Search ledgers..."
              className="w-full pl-8 pr-3 py-1.5 text-sm text-gilt-ink bg-gilt-paper border border-gilt-hairline rounded-md focus:border-gilt-brass focus:outline-none placeholder:text-gilt-faint transition-colors"
              aria-label="Search ledgers"
              autoComplete="off"
            />
          </div>
        </div>

        {/* Loading state */}
        {isLoading && (
          <div className="flex items-center justify-center gap-2 py-8 text-gilt-soft">
            <Loader2 className="h-4 w-4 animate-spin" />
            <span className="text-sm">Loading ledgers...</span>
          </div>
        )}

        {/* Error state */}
        {error && !isLoading && (
          <div className="flex flex-col items-center gap-2 py-6 px-4">
            <AlertCircle className="h-5 w-5 text-gilt-debit" />
            <span className="text-sm text-gilt-debit">Failed to load ledgers</span>
            {onRetry && (
              <button
                onClick={onRetry}
                className="flex items-center gap-1 px-3 py-1 text-sm text-gilt-brass hover:bg-gilt-sunken rounded-md transition-colors"
              >
                <RefreshCw className="h-3.5 w-3.5" />
                Retry
              </button>
            )}
          </div>
        )}

        {/* Options list */}
        {!isLoading && !error && (
          <div
            ref={listRef}
            className="max-h-60 overflow-y-auto"
          >
            {/* Create new option */}
            {onCreateNew && (
              <button
                data-ss-option
                onClick={() => {
                  onCreateNew();
                  setIsOpen(false);
                }}
                className={`w-full text-left px-3 py-2 text-sm flex items-center gap-2 border-b border-gilt-hairline transition-colors ${
                  highlightedIndex === 0
                    ? "bg-gilt-sunken text-gilt-brass"
                    : "text-gilt-brass hover:bg-gilt-sunken"
                }`}
                role="option"
              >
                <Plus className="h-3.5 w-3.5" />
                <span className="font-medium">Create New Ledger</span>
              </button>
            )}

            {/* Recently used section */}
            {recentInOptions.length > 0 && (
              <>
                <div className="px-3 py-1.5 font-mono text-[10px] font-semibold text-gilt-faint uppercase tracking-[0.14em] bg-gilt-sunken/60 flex items-center gap-1 select-none">
                  <Clock className="h-2.5 w-2.5" />
                  Recently Used
                </div>
                {recentInOptions.map((opt) => (
                  <button
                    key={`recent-${opt}`}
                    onClick={() => handleSelect(opt)}
                    className={`w-full text-left px-3 py-1.5 text-sm flex items-center justify-between transition-colors ${
                      value === opt
                        ? "bg-gilt-sunken text-gilt-brass"
                        : "text-gilt-soft hover:bg-gilt-sunken"
                    }`}
                    role="option"
                    aria-selected={value === opt}
                  >
                    <span className="truncate">{opt}</span>
                    {value === opt && (
                      <Check className="h-3.5 w-3.5 text-gilt-brass flex-shrink-0 ml-2" />
                    )}
                  </button>
                ))}
                <div className="border-b border-gilt-hairline" />
              </>
            )}

            {/* All / filtered options */}
            {filteredOptions.length > 0 &&
              !searchQuery &&
              recentInOptions.length > 0 && (
                <div className="px-3 py-1.5 font-mono text-[10px] font-semibold text-gilt-faint uppercase tracking-[0.14em] bg-gilt-sunken/60 select-none">
                  All Ledgers
                </div>
              )}
            {filteredOptions.map((opt, i) => {
              const globalIdx = i + createOffset;
              return (
                <button
                  key={opt}
                  data-ss-option
                  onClick={() => handleSelect(opt)}
                  className={`w-full text-left px-3 py-1.5 text-sm flex items-center justify-between transition-colors ${
                    highlightedIndex === globalIdx
                      ? "bg-gilt-sunken text-gilt-brass"
                      : value === opt
                      ? "bg-gilt-sunken text-gilt-brass"
                      : "text-gilt-soft hover:bg-gilt-sunken"
                  }`}
                  role="option"
                  aria-selected={value === opt}
                >
                  <span className="truncate">{opt}</span>
                  {value === opt && (
                    <Check className="h-3.5 w-3.5 text-gilt-brass flex-shrink-0 ml-2" />
                  )}
                </button>
              );
            })}

            {/* No results */}
            {filteredOptions.length === 0 && (
              <div className="py-6 text-center">
                <Search className="h-5 w-5 mx-auto mb-1.5 text-gilt-faint" />
                <p className="text-sm text-gilt-soft">No results found</p>
                <p className="text-xs text-gilt-faint mt-0.5">
                  Try a different search term
                </p>
              </div>
            )}
          </div>
        )}

        {/* Footer with count */}
        {!isLoading && !error && (
          <div className="px-3 py-1.5 border-t border-gilt-hairline font-mono tabular-nums text-xs text-gilt-faint bg-gilt-sunken/60">
            {searchQuery
              ? `${filteredOptions.length} of ${options.length} ledgers`
              : `${options.length} ledgers`}
          </div>
        )}
      </div>,
      document.body
    );

  return (
    <div ref={triggerRef} className={`relative ${className}`}>
      <button
        type="button"
        onClick={toggleOpen}
        disabled={disabled}
        className={`w-full max-w-[200px] flex items-center justify-between gap-1 px-2 py-1 text-sm border rounded text-left transition-colors focus:border-gilt-brass focus:outline-none ${
          !valueExists
            ? "border-gilt-brass/40 bg-gilt-sunken text-gilt-brass"
            : value
            ? "border-gilt-credit/40 bg-gilt-card text-gilt-ink"
            : "border-gilt-hairline bg-gilt-card text-gilt-faint"
        } ${
          disabled
            ? "opacity-50 cursor-not-allowed"
            : "cursor-pointer hover:border-gilt-brass"
        }`}
        title={
          !valueExists
            ? `"${value}" no longer exists in Tally`
            : value || placeholder
        }
        aria-haspopup="listbox"
        aria-expanded={isOpen}
      >
        <span className="truncate flex-1">{value || placeholder}</span>
        <div className="flex items-center gap-0.5 flex-shrink-0">
          {value && !disabled && (
            <span
              onClick={handleClear}
              className="p-0.5 hover:bg-gilt-sunken rounded-full transition-colors"
              role="button"
              aria-label="Clear selection"
              tabIndex={-1}
            >
              <X className="h-3 w-3 text-gilt-faint" />
            </span>
          )}
          <ChevronDown
            className={`h-3.5 w-3.5 text-gilt-faint transition-transform duration-150 ${
              isOpen ? "rotate-180" : ""
            }`}
          />
        </div>
      </button>
      {!valueExists && value && (
        <p className="text-[10px] text-gilt-brass mt-0.5 leading-tight">
          Ledger not found in Tally
        </p>
      )}
      {dropdown}
    </div>
  );
}

export default SearchableSelect;
