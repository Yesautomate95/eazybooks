"use client";

import * as DialogPrimitive from "@radix-ui/react-dialog";
import * as React from "react";
import { X } from "lucide-react";
import { cn } from "../../lib/utils";

const Dialog = DialogPrimitive.Root;
const DialogTrigger = DialogPrimitive.Trigger;
const DialogPortal = DialogPrimitive.Portal;
const DialogClose = DialogPrimitive.Close;

const DialogOverlay = React.forwardRef(({ className, ...props }, ref) => (
  <DialogPrimitive.Overlay
    ref={ref}
    className={cn(
      "fixed inset-0 z-[100] bg-gilt-ink/40 backdrop-blur-[2px]",
      "data-[state=open]:animate-in data-[state=closed]:animate-out",
      "data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0",
      className
    )}
    {...props}
  />
));
DialogOverlay.displayName = DialogPrimitive.Overlay.displayName;

const DialogContent = React.forwardRef(
  ({ className, children, size = "md", showClose = true, ...props }, ref) => {
    const sizeClass = {
      sm: "sm:max-w-[420px]",
      md: "sm:max-w-[520px]",
      lg: "sm:max-w-[640px]",
      xl: "sm:max-w-[820px]",
    }[size];
    // Radix portals to <body> by default, which escapes the .gl-root scope
    // where the gilt CSS variables live — leaving the dialog un-themed. Portal
    // into the gl-root element instead so the theme (incl. dark) cascades in.
    const glRoot =
      typeof document !== "undefined" ? document.querySelector(".gl-root") : null;
    return (
      <DialogPortal container={glRoot || undefined}>
        <DialogOverlay />
        <DialogPrimitive.Content
          ref={ref}
          className={cn(
            "fixed left-1/2 top-1/2 z-[101] w-[calc(100%-2rem)] -translate-x-1/2 -translate-y-1/2",
            "bg-gilt-card border border-gilt-hairline rounded-2xl",
            "max-h-[calc(100%-4rem)] overflow-y-auto",
            "duration-200",
            "data-[state=open]:animate-in data-[state=closed]:animate-out",
            "data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0",
            "data-[state=closed]:zoom-out-95 data-[state=open]:zoom-in-95",
            sizeClass,
            className
          )}
          {...props}
        >
          {children}
          {showClose && (
            <DialogPrimitive.Close
              className="absolute right-4 top-4 inline-flex h-7 w-7 items-center justify-center rounded-md text-gilt-faint hover:bg-gilt-sunken hover:text-gilt-ink transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-gilt-brass"
              aria-label="Close"
            >
              <X size={16} strokeWidth={2} />
            </DialogPrimitive.Close>
          )}
        </DialogPrimitive.Content>
      </DialogPortal>
    );
  }
);
DialogContent.displayName = DialogPrimitive.Content.displayName;

const DialogHeader = ({ className, ...props }) => (
  <div className={cn("px-6 pt-6 pb-2", className)} {...props} />
);
DialogHeader.displayName = "DialogHeader";

const DialogBody = ({ className, ...props }) => (
  <div className={cn("px-6 py-3 text-sm text-gilt-soft", className)} {...props} />
);
DialogBody.displayName = "DialogBody";

const DialogFooter = ({ className, ...props }) => (
  <div
    className={cn(
      "flex flex-col-reverse gap-2 sm:flex-row sm:justify-end sm:gap-2 px-6 py-4 border-t border-gilt-hairline bg-gilt-sunken/40 rounded-b-2xl",
      className
    )}
    {...props}
  />
);
DialogFooter.displayName = "DialogFooter";

const DialogTitle = React.forwardRef(({ className, ...props }, ref) => (
  <DialogPrimitive.Title
    ref={ref}
    className={cn("text-lg font-semibold text-gilt-ink tracking-tight", className)}
    {...props}
  />
));
DialogTitle.displayName = DialogPrimitive.Title.displayName;

const DialogDescription = React.forwardRef(({ className, ...props }, ref) => (
  <DialogPrimitive.Description
    ref={ref}
    className={cn("mt-1 text-sm text-gilt-soft", className)}
    {...props}
  />
));
DialogDescription.displayName = DialogPrimitive.Description.displayName;

export {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogBody,
  DialogOverlay,
  DialogPortal,
  DialogTitle,
  DialogTrigger,
};
