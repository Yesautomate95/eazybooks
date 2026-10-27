import * as React from "react";
import { cn } from "../../lib/utils";

export const EmptyState = ({
  icon: Icon,
  title,
  description,
  action,
  className,
  size = "md",
}) => {
  const padding = size === "sm" ? "py-8 px-6" : "py-14 px-6";
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center text-center",
        padding,
        className
      )}
    >
      {Icon && (
        <div className="mb-4 inline-flex items-center justify-center h-12 w-12 rounded-xl bg-gilt-sunken border border-gilt-hairline text-gilt-brass">
          <Icon size={22} strokeWidth={1.75} />
        </div>
      )}
      {title && <h3 className="text-base font-semibold text-gilt-ink">{title}</h3>}
      {description && (
        <p className="mt-1 text-sm text-gilt-soft max-w-sm">{description}</p>
      )}
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
};

export default EmptyState;
