import React from "react";
import { Building2, ChevronDown } from "lucide-react";
import { cn } from "../../../../../lib/utils";

/**
 * Company picker for Tally analytics. A user may own several companies; the
 * selection drives the (user, company) scope of every figure on the page.
 */
export function CompanySelect({ companies = [], value, onChange }) {
  return (
    <label className="relative inline-flex items-center">
      <Building2
        size={14}
        className="pointer-events-none absolute left-3 text-gilt-brass"
        aria-hidden
      />
      <select
        value={value || ""}
        onChange={(e) => onChange(e.target.value)}
        className={cn(
          "appearance-none rounded-lg border border-gilt-hairline bg-gilt-card",
          "py-2 pl-8 pr-8 text-xs font-medium text-gilt-ink",
          "focus:border-gilt-brass focus:outline-none"
        )}
        aria-label="Tally company"
      >
        {companies.length === 0 && <option value="">No companies synced</option>}
        {companies.map((c) => (
          <option key={c.company} value={c.company}>
            {c.company}
          </option>
        ))}
      </select>
      <ChevronDown
        size={14}
        className="pointer-events-none absolute right-2.5 text-gilt-soft"
        aria-hidden
      />
    </label>
  );
}
