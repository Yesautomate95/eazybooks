import React, { useState, useRef, useEffect } from 'react';
import { createPortal } from 'react-dom';
import { Search, ChevronDown, Plus } from 'lucide-react';

function SearchableDropdown({ options, value, onChange, onCreateNew, placeholder = "Select ledger...", createLabel = "Create New Ledger" }) {
  const [isOpen, setIsOpen] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');
  const [dropdownStyle, setDropdownStyle] = useState({});
  const triggerRef = useRef(null);
  const popupRef = useRef(null);
  const inputRef = useRef(null);

  const filteredOptions = options.filter(option =>
    option.toLowerCase().includes(searchTerm.toLowerCase())
  );

  // Close on outside click — the popup is portaled out of the trigger subtree
  // so we have to check both the trigger and the portaled popup explicitly.
  useEffect(() => {
    function handleClickOutside(event) {
      if (triggerRef.current?.contains(event.target)) return;
      if (popupRef.current?.contains(event.target)) return;
      setIsOpen(false);
      setSearchTerm('');
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  // Focus search input when opened
  useEffect(() => {
    if (isOpen && inputRef.current) {
      inputRef.current.focus();
    }
  }, [isOpen]);

  // Compute the portal'd popup's position from the trigger's bounding rect.
  // Flips above when there isn't enough room below, clamps to viewport so a
  // near-edge field doesn't render off-screen. Re-runs on scroll/resize so the
  // popup tracks the trigger when its container scrolls.
  useEffect(() => {
    if (!isOpen || !triggerRef.current) return;
    const updatePosition = () => {
      const rect = triggerRef.current.getBoundingClientRect();
      const spaceBelow = window.innerHeight - rect.bottom;
      const spaceAbove = rect.top;
      const desired = 320;
      const style = {
        position: 'fixed',
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
    window.addEventListener('scroll', updatePosition, true);
    window.addEventListener('resize', updatePosition);
    return () => {
      window.removeEventListener('scroll', updatePosition, true);
      window.removeEventListener('resize', updatePosition);
    };
  }, [isOpen]);

  return (
    <div className="relative w-full max-w-[200px]">
      {/* Selected Value Display */}
      <div
        ref={triggerRef}
        className={`flex items-center justify-between px-2 py-1 text-sm border rounded cursor-pointer hover:border-gilt-brass transition-colors ${
          value ? "border-gilt-credit/40 bg-gilt-card text-gilt-ink" : "border-gilt-hairline bg-gilt-card text-gilt-faint"
        }`}
        onClick={() => setIsOpen(!isOpen)}
      >
        <span className={`truncate ${value ? "text-gilt-ink" : "text-gilt-faint"}`}>{value || placeholder}</span>
        <ChevronDown className={`h-3 w-3 ml-1 flex-shrink-0 transition-transform ${isOpen ? 'rotate-180' : ''}`} />
      </div>

      {/* Popup — portaled out of the table/panel's overflow-clipped container
          (otherwise the popup is cut off the moment it extends past the row).
          Portal into .gl-root rather than <body> so the gilt CSS variables —
          which are scoped to .gl-root — still cascade in; portaling to <body>
          escapes that scope and leaves the popup un-themed. The popup is
          position:fixed, so the container swap doesn't change its placement. */}
      {isOpen && createPortal(
        <div
          ref={popupRef}
          style={dropdownStyle}
          className="bg-gilt-card border border-gilt-hairline rounded-lg shadow-sm overflow-hidden flex flex-col"
        >
          {/* Search Input */}
          <div className="p-2 border-b border-gilt-hairline shrink-0 bg-gilt-card">
            <div className="relative">
              <Search className="absolute left-2 top-1/2 transform -translate-y-1/2 h-3.5 w-3.5 text-gilt-faint" />
              <input
                ref={inputRef}
                type="text"
                placeholder="Search…"
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                className="w-full pl-7 pr-2 py-1.5 text-sm border border-gilt-hairline rounded text-gilt-ink bg-gilt-paper focus:outline-none focus:border-gilt-brass placeholder:text-gilt-faint transition-colors"
                onClick={(e) => e.stopPropagation()}
              />
            </div>
          </div>

          {/* Create New Option — pinned at the top of the list so the user can
              add a new ledger inline (matches Tally's pattern). */}
          {onCreateNew && (
            <div
              className="px-3 py-2 cursor-pointer hover:bg-gilt-sunken text-gilt-brass text-sm font-medium flex items-center gap-1.5 border-b border-gilt-hairline shrink-0 transition-colors"
              onClick={() => {
                onCreateNew(searchTerm || undefined);
                setIsOpen(false);
                setSearchTerm('');
              }}
            >
              <Plus className="h-3.5 w-3.5" />
              {createLabel}
              {searchTerm && (
                <span className="ml-1 truncate text-gilt-brass/80">"{searchTerm}"</span>
              )}
            </div>
          )}

          {/* Options List */}
          <div className="overflow-y-auto flex-1">
            {filteredOptions.length > 0 ? (
              filteredOptions.map((option) => (
                <div
                  key={option}
                  className={`px-3 py-1.5 cursor-pointer text-sm transition-colors ${
                    option === value
                      ? 'bg-gilt-sunken text-gilt-brass font-medium'
                      : 'text-gilt-ink hover:bg-gilt-sunken'
                  }`}
                  onClick={() => {
                    onChange(option);
                    setIsOpen(false);
                    setSearchTerm('');
                  }}
                >
                  {option}
                </div>
              ))
            ) : (
              <div className="px-3 py-3 text-gilt-faint text-sm text-center">
                No matches for "{searchTerm}"
              </div>
            )}
          </div>
        </div>,
        (typeof document !== 'undefined' && document.querySelector('.gl-root')) || document.body,
      )}
    </div>
  );
}

export default SearchableDropdown;
