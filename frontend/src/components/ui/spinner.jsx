import * as React from "react";
import { Loader2 } from "lucide-react";
import { cn } from "../../lib/utils";

export const Spinner = ({ size = 16, className, tone = "default" }) => (
  <Loader2
    className={cn(
      "animate-spin",
      tone === "brand" && "text-gilt-brass",
      tone === "muted" && "text-gilt-faint",
      tone === "default" && "text-gilt-brass",
      className
    )}
    size={size}
    strokeWidth={2.25}
  />
);

export const FullPageSpinner = ({ label = "Loading", className }) => (
  <div
    className={cn(
      "flex flex-col items-center justify-center gap-3 py-16",
      className
    )}
  >
    <Spinner size={22} tone="brand" />
    <p className="text-sm text-gilt-soft">{label}</p>
  </div>
);

export default Spinner;
