import * as React from "react";
import { cn } from "../../lib/utils";

export const Card = React.forwardRef(
  ({ className, interactive = false, padded = false, as: Tag = "div", ...props }, ref) => (
    <Tag
      ref={ref}
      className={cn(
        "bg-gilt-card border border-gilt-hairline rounded-xl",
        interactive &&
          "transition-colors duration-150 hover:border-gilt-brass cursor-pointer",
        padded && "p-5",
        className
      )}
      {...props}
    />
  )
);
Card.displayName = "Card";

export const CardHeader = ({ className, title, description, action, children, ...props }) => (
  <div
    className={cn(
      "flex items-start justify-between gap-4 px-5 py-4 border-b border-gilt-hairline",
      className
    )}
    {...props}
  >
    {children ?? (
      <div className="min-w-0">
        {title && <h3 className="text-base font-semibold text-gilt-ink truncate">{title}</h3>}
        {description && <p className="mt-0.5 text-xs text-gilt-soft">{description}</p>}
      </div>
    )}
    {action && <div className="shrink-0">{action}</div>}
  </div>
);

export const CardBody = ({ className, ...props }) => (
  <div className={cn("p-5", className)} {...props} />
);

export const CardFooter = ({ className, ...props }) => (
  <div
    className={cn("flex items-center justify-end gap-2 px-5 py-3 border-t border-gilt-hairline bg-gilt-sunken/50 rounded-b-xl", className)}
    {...props}
  />
);

export default Card;
