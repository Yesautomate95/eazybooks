import * as React from "react";
import { cn } from "../../lib/utils";

const VARIANTS = {
  neutral: "bg-gilt-sunken text-gilt-soft border border-gilt-hairline",
  brand: "bg-transparent text-gilt-brass border border-gilt-brass/40",
  success: "bg-transparent text-gilt-credit border border-gilt-credit/40",
  danger: "bg-transparent text-gilt-debit border border-gilt-debit/40",
  warning: "bg-gilt-sunken text-gilt-brass border border-gilt-brass/40",
  info: "bg-transparent text-gilt-soft border border-gilt-hairline",
  outline: "bg-transparent text-gilt-soft border border-gilt-hairline",
};

const SIZES = {
  sm: "text-2xs px-1.5 py-0.5 gap-1",
  md: "text-xs px-2 py-0.5 gap-1.5",
  lg: "text-xs px-2.5 py-1 gap-1.5",
};

export const Badge = React.forwardRef(
  ({ className, variant = "neutral", size = "md", dot = false, children, ...props }, ref) => (
    <span
      ref={ref}
      className={cn(
        "inline-flex items-center font-medium rounded-full whitespace-nowrap",
        VARIANTS[variant] || VARIANTS.neutral,
        SIZES[size] || SIZES.md,
        className
      )}
      {...props}
    >
      {dot && (
        <span
          className={cn(
            "w-1.5 h-1.5 rounded-full",
            variant === "success" && "bg-gilt-credit",
            variant === "danger" && "bg-gilt-debit",
            variant === "warning" && "bg-gilt-brass",
            variant === "info" && "bg-gilt-soft",
            variant === "brand" && "bg-gilt-brass",
            (variant === "neutral" || variant === "outline") && "bg-gilt-faint"
          )}
        />
      )}
      {children}
    </span>
  )
);
Badge.displayName = "Badge";

export default Badge;
