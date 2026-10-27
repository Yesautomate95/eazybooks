import * as React from "react";
import { Loader2 } from "lucide-react";
import { cn } from "../../lib/utils";

// Gilt Ledger button semantics:
//   primary  → BRASS, the affirmative metal (Connect, Save, Push)
//   danger   → IRON-INK, weighty but NOT financial. Debit-red is reserved for
//              debit/error meaning only, so destructive actions never use it.
//   For high-consequence confirmation, prefer the PressTheSeal control.
const VARIANTS = {
  primary:
    "bg-gilt-brass text-gilt-paper hover:bg-gilt-brass/90 active:bg-gilt-brass/80 border border-transparent",
  secondary:
    "bg-gilt-card text-gilt-soft border border-gilt-hairline hover:border-gilt-brass hover:text-gilt-brass active:bg-gilt-sunken",
  tertiary:
    "bg-transparent text-gilt-soft hover:bg-gilt-sunken active:bg-gilt-sunken border border-transparent",
  ghost:
    "bg-transparent text-gilt-soft hover:bg-gilt-sunken hover:text-gilt-ink border border-transparent",
  danger:
    "bg-gilt-ink text-gilt-paper hover:bg-gilt-ink/90 active:bg-gilt-ink/80 border border-transparent",
  "danger-outline":
    "bg-transparent text-gilt-ink border border-gilt-ink/30 hover:border-gilt-ink hover:bg-gilt-sunken",
  link:
    "bg-transparent text-gilt-brass hover:text-gilt-brass/80 hover:underline px-0 h-auto border-transparent shadow-none",
  // legacy aliases
  default: "bg-gilt-brass text-gilt-paper hover:bg-gilt-brass/90 border border-transparent",
  destructive: "bg-gilt-ink text-gilt-paper hover:bg-gilt-ink/90 border border-transparent",
  outline: "bg-gilt-card text-gilt-soft border border-gilt-hairline hover:border-gilt-brass hover:text-gilt-brass",
};

const SIZES = {
  xs: "h-7 px-2.5 text-xs gap-1.5 rounded-md",
  sm: "h-8 px-3 text-sm gap-1.5 rounded-md",
  md: "h-9 px-3.5 text-sm gap-2 rounded-lg",
  default: "h-9 px-3.5 text-sm gap-2 rounded-lg",
  lg: "h-10 px-4 text-sm gap-2 rounded-lg",
  xl: "h-11 px-5 text-base gap-2 rounded-lg",
  icon: "h-9 w-9 rounded-lg",
  "icon-sm": "h-8 w-8 rounded-md",
};

const Button = React.forwardRef(
  (
    {
      className,
      variant = "primary",
      size = "md",
      children,
      loading = false,
      leftIcon,
      rightIcon,
      disabled,
      type = "button",
      ...props
    },
    ref
  ) => {
    const isDisabled = disabled || loading;
    return (
      <button
        ref={ref}
        type={type}
        disabled={isDisabled}
        className={cn(
          "inline-flex items-center justify-center font-medium transition-colors duration-150 select-none",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-gilt-brass focus-visible:ring-offset-2 focus-visible:ring-offset-gilt-paper",
          "disabled:opacity-50 disabled:cursor-not-allowed disabled:pointer-events-none",
          "whitespace-nowrap",
          VARIANTS[variant] || VARIANTS.primary,
          SIZES[size] || SIZES.md,
          className
        )}
        {...props}
      >
        {loading ? (
          <Loader2 className="animate-spin" size={size === "xs" || size === "sm" ? 14 : 16} />
        ) : leftIcon ? (
          <span className="inline-flex shrink-0 [&_svg]:size-[16px]">{leftIcon}</span>
        ) : null}
        {children}
        {!loading && rightIcon ? (
          <span className="inline-flex shrink-0 [&_svg]:size-[16px]">{rightIcon}</span>
        ) : null}
      </button>
    );
  }
);
Button.displayName = "Button";

export { Button };
export default Button;
