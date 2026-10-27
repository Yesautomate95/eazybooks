import React from "react";
import { cn } from "../../../../../lib/utils";

/**
 * Shimmer block — paints instantly so layout doesn't jump while data loads.
 * Composable: `<Skeleton className="h-4 w-24" />`.
 */
export function Skeleton({ className, style }) {
  return (
    <div
      style={style}
      className={cn(
        "relative overflow-hidden rounded-md bg-gilt-sunken",
        "before:absolute before:inset-0 before:-translate-x-full",
        "before:animate-[shimmer_1.6s_linear_infinite]",
        "before:bg-gradient-to-r before:from-gilt-sunken before:via-gilt-hairline/70 before:to-gilt-sunken",
        className
      )}
    />
  );
}

export function StatCardSkeleton() {
  return (
    <div className="rounded-xl border border-gilt-hairline bg-gilt-card p-5">
      <Skeleton className="h-3 w-24" />
      <Skeleton className="mt-4 h-8 w-36" />
      <div className="mt-4 flex items-center gap-2">
        <Skeleton className="h-3 w-14" />
        <Skeleton className="h-8 flex-1" />
      </div>
    </div>
  );
}

export function ChartCardSkeleton({ height = 280 }) {
  return (
    <div className="rounded-xl border border-gilt-hairline bg-gilt-card p-5">
      <div className="flex items-center justify-between">
        <Skeleton className="h-4 w-44" />
        <Skeleton className="h-7 w-24" />
      </div>
      <Skeleton className="mt-5 w-full" style={{ height }} />
    </div>
  );
}

export function ListSkeleton({ rows = 6 }) {
  return (
    <div className="rounded-xl border border-gilt-hairline bg-gilt-card p-5">
      <Skeleton className="h-4 w-40" />
      <div className="mt-4 space-y-3">
        {Array.from({ length: rows }).map((_, i) => (
          <div key={i} className="flex items-center gap-3">
            <Skeleton className="h-9 w-9 rounded-full" />
            <div className="flex-1 space-y-1.5">
              <Skeleton className="h-3 w-2/3" />
              <Skeleton className="h-2 w-1/3" />
            </div>
            <Skeleton className="h-3 w-16" />
          </div>
        ))}
      </div>
    </div>
  );
}
