import * as React from "react";
import { cn } from "../../lib/utils";

export const Skeleton = ({ className, ...props }) => (
  <div
    className={cn(
      "rounded-md bg-gradient-to-r from-gilt-sunken via-gilt-hairline/70 to-gilt-sunken bg-[length:200%_100%] animate-shimmer",
      className
    )}
    {...props}
  />
);

export const SkeletonText = ({ lines = 3, className }) => (
  <div className={cn("space-y-2", className)}>
    {Array.from({ length: lines }).map((_, i) => (
      <Skeleton
        key={i}
        className={cn("h-3", i === lines - 1 ? "w-2/3" : "w-full")}
      />
    ))}
  </div>
);

export const SkeletonCard = ({ className }) => (
  <div className={cn("rounded-xl border border-gilt-hairline bg-gilt-card p-5", className)}>
    <div className="flex items-center gap-3 mb-4">
      <Skeleton className="h-10 w-10 rounded-lg" />
      <div className="flex-1 space-y-2">
        <Skeleton className="h-3 w-1/2" />
        <Skeleton className="h-2.5 w-1/3" />
      </div>
    </div>
    <SkeletonText lines={2} />
  </div>
);

export default Skeleton;
