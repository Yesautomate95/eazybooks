import * as React from "react";
import { ChevronRight } from "lucide-react";
import { cn } from "../../lib/utils";

/**
 * PageHeader — sticky-friendly page chrome used at the top of every workspace surface.
 *
 * Props:
 *  - eyebrow: small label above title (e.g. "Workspace")
 *  - title:   main heading
 *  - description: optional sub-text
 *  - icon:    lucide icon component
 *  - breadcrumbs: [{ label, onClick? }]
 *  - actions: right-side action node(s)
 *  - children: rendered below the header row (filters, tabs, etc.)
 */
export const PageHeader = ({
  eyebrow,
  title,
  description,
  icon: Icon,
  breadcrumbs,
  actions,
  children,
  className,
}) => (
  <header
    className={cn(
      "border-b border-gilt-hairline",
      className
    )}
  >
    <div className="px-6 lg:px-8 py-5">
      {breadcrumbs && breadcrumbs.length > 0 && (
        <nav className="flex items-center text-xs text-gilt-soft mb-2" aria-label="Breadcrumb">
          {breadcrumbs.map((crumb, i) => (
            <React.Fragment key={i}>
              {i > 0 && <ChevronRight size={12} className="mx-1 text-gilt-faint" />}
              {crumb.onClick ? (
                <button
                  type="button"
                  onClick={crumb.onClick}
                  className="hover:text-gilt-ink transition-colors"
                >
                  {crumb.label}
                </button>
              ) : (
                <span className={cn(i === breadcrumbs.length - 1 && "text-gilt-ink font-medium")}>
                  {crumb.label}
                </span>
              )}
            </React.Fragment>
          ))}
        </nav>
      )}
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0 flex items-start gap-3">
          {Icon && (
            <div className="shrink-0 mt-0.5 inline-flex items-center justify-center h-10 w-10 rounded-xl bg-gilt-sunken border border-gilt-hairline text-gilt-brass">
              <Icon size={20} strokeWidth={1.75} />
            </div>
          )}
          <div className="min-w-0">
            {eyebrow && (
              <p className="gl-eyebrow mb-1">
                {eyebrow}
              </p>
            )}
            <h1 className="gl-display text-2xl text-gilt-ink leading-tight truncate">
              {title}
            </h1>
            {description && (
              <p className="mt-1 text-sm text-gilt-soft max-w-2xl">{description}</p>
            )}
          </div>
        </div>
        {actions && <div className="shrink-0 flex items-center gap-2">{actions}</div>}
      </div>
      {children && <div className="mt-5">{children}</div>}
    </div>
  </header>
);

export default PageHeader;
