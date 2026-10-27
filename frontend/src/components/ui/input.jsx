import * as React from "react";
import { cn } from "../../lib/utils";

const baseField =
  "w-full bg-gilt-card text-gilt-ink placeholder:text-gilt-faint border border-gilt-hairline rounded-lg transition-colors duration-150 " +
  "focus:outline-none focus:border-gilt-brass " +
  "disabled:bg-gilt-sunken disabled:text-gilt-faint disabled:cursor-not-allowed";

const sizeMap = {
  sm: "h-8 px-2.5 text-sm",
  md: "h-9 px-3 text-sm",
  lg: "h-10 px-3.5 text-base",
};

export const Input = React.forwardRef(
  ({ className, size = "md", invalid, leftIcon, rightIcon, ...props }, ref) => {
    const sizing = sizeMap[size] || sizeMap.md;
    if (!leftIcon && !rightIcon) {
      return (
        <input
          ref={ref}
          className={cn(
            baseField,
            sizing,
            invalid && "border-gilt-debit/60 focus:border-gilt-debit",
            className
          )}
          {...props}
        />
      );
    }
    return (
      <div className="relative w-full">
        {leftIcon && (
          <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-gilt-faint [&_svg]:size-[16px]">
            {leftIcon}
          </span>
        )}
        <input
          ref={ref}
          className={cn(
            baseField,
            sizing,
            leftIcon && "pl-9",
            rightIcon && "pr-9",
            invalid && "border-gilt-debit/60 focus:border-gilt-debit",
            className
          )}
          {...props}
        />
        {rightIcon && (
          <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-gilt-faint [&_svg]:size-[16px]">
            {rightIcon}
          </span>
        )}
      </div>
    );
  }
);
Input.displayName = "Input";

export const Textarea = React.forwardRef(({ className, invalid, ...props }, ref) => (
  <textarea
    ref={ref}
    className={cn(
      baseField,
      "min-h-[80px] py-2 px-3 text-sm leading-5 resize-y",
      invalid && "border-gilt-debit/60 focus:border-gilt-debit",
      className
    )}
    {...props}
  />
));
Textarea.displayName = "Textarea";

export const Select = React.forwardRef(({ className, size = "md", invalid, children, ...props }, ref) => {
  const sizing = sizeMap[size] || sizeMap.md;
  return (
    <div className="relative w-full">
      <select
        ref={ref}
        className={cn(
          baseField,
          sizing,
          "appearance-none pr-9 bg-no-repeat",
          invalid && "border-gilt-debit/60 focus:border-gilt-debit",
          className
        )}
        {...props}
      >
        {children}
      </select>
      <svg
        className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-gilt-faint"
        width="14"
        height="14"
        viewBox="0 0 14 14"
        fill="none"
        aria-hidden="true"
      >
        <path d="M3.5 5.25L7 8.75L10.5 5.25" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </div>
  );
});
Select.displayName = "Select";

export const Label = React.forwardRef(({ className, required, children, ...props }, ref) => (
  <label
    ref={ref}
    className={cn("block text-xs font-medium text-gilt-soft mb-1.5", className)}
    {...props}
  >
    {children}
    {required && <span className="text-gilt-debit ml-0.5">*</span>}
  </label>
));
Label.displayName = "Label";

export const HelpText = ({ className, error, children }) => (
  <p className={cn("mt-1.5 text-xs", error ? "text-gilt-debit" : "text-gilt-soft", className)}>
    {children}
  </p>
);

export const Field = ({ label, required, error, hint, children, className }) => (
  <div className={cn("w-full", className)}>
    {label && <Label required={required}>{label}</Label>}
    {children}
    {(error || hint) && <HelpText error={Boolean(error)}>{error || hint}</HelpText>}
  </div>
);
