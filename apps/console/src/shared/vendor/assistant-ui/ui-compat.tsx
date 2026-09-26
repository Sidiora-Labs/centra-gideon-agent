"use client";

import { forwardRef, type ComponentProps, type ReactNode } from "react";
import { Tooltip as TooltipPrimitive } from "@base-ui/react/tooltip";
import { Dialog as DialogPrimitive } from "@base-ui/react/dialog";
import { Collapsible as CollapsiblePrimitive } from "@base-ui/react/collapsible";
import { Avatar as AvatarPrimitive } from "@base-ui/react/avatar";
import { cn } from "./lib/utils";

export const TooltipProvider = TooltipPrimitive.Provider;
export const Tooltip = TooltipPrimitive.Root;
export const TooltipTrigger = TooltipPrimitive.Trigger;
export function TooltipContent({ side = "top", sideOffset = 6, className, children, ...props }: ComponentProps<"div"> & {
  side?: "top" | "right" | "bottom" | "left";
  sideOffset?: number;
  className?: string;
  children?: ReactNode;
}) {
  return <TooltipPrimitive.Portal><TooltipPrimitive.Positioner side={side} sideOffset={sideOffset}><TooltipPrimitive.Popup className={className} {...props}>{children}</TooltipPrimitive.Popup></TooltipPrimitive.Positioner></TooltipPrimitive.Portal>;
}

export const Dialog = DialogPrimitive.Root;
export const DialogTrigger = DialogPrimitive.Trigger;
export function DialogContent({ className, children }: { className?: string; children?: ReactNode }) {
  return <DialogPrimitive.Portal><DialogPrimitive.Backdrop className="fixed inset-0 z-40 bg-black/50" /><DialogPrimitive.Popup className={cn("bg-background fixed top-1/2 left-1/2 z-50 w-[min(90vw,40rem)] -translate-x-1/2 -translate-y-1/2 rounded-xl border p-4", className)}>{children}<DialogPrimitive.Close aria-label="Close dialog" className="absolute end-2 top-2">×</DialogPrimitive.Close></DialogPrimitive.Popup></DialogPrimitive.Portal>;
}
export const DialogTitle = DialogPrimitive.Title;
export const DialogDescription = DialogPrimitive.Description;
export function DialogHeader({ children }: { children?: ReactNode }) { return <div className="mb-3 flex flex-col gap-1">{children}</div>; }

export const Avatar = AvatarPrimitive.Root;
export const AvatarImage = AvatarPrimitive.Image;
export const AvatarFallback = AvatarPrimitive.Fallback;
export const Collapsible = CollapsiblePrimitive.Root;
export const CollapsibleTrigger = CollapsiblePrimitive.Trigger;
export const CollapsibleContent = CollapsiblePrimitive.Panel;

export type ButtonProps = ComponentProps<"button"> & { variant?: string; size?: string };
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button({ variant, size, className, type = "button", ...props }, ref) {
  return <button ref={ref} type={type} data-variant={variant} data-size={size} className={cn("inline-flex items-center justify-center rounded-md px-3 py-1.5 text-sm", className)} {...props} />;
});
export function buttonVariants({ variant, size }: { variant?: string; size?: string } = {}) {
  return cn("inline-flex items-center justify-center rounded-md px-3 py-1.5 text-sm", variant === "outline" && "border", size === "sm" && "text-xs");
}
export function Badge({ variant, className, ...props }: ComponentProps<"span"> & { variant?: string }) {
  return <span data-variant={variant} className={cn("inline-flex rounded-full px-2 py-0.5 text-xs", className)} {...props} />;
}
export function Label(props: ComponentProps<"label">) { return <label {...props} />; }
export function Separator({ orientation = "horizontal", className }: { orientation?: "horizontal" | "vertical"; className?: string }) {
  return <span role="separator" aria-orientation={orientation} className={cn(orientation === "horizontal" ? "block h-px w-full" : "block h-full w-px", "bg-border", className)} />;
}
export const Textarea = forwardRef<HTMLTextAreaElement, ComponentProps<"textarea">>(function Textarea(props, ref) {
  return <textarea ref={ref} {...props} />;
});
export function Skeleton(props: ComponentProps<"span">) { return <span aria-hidden {...props} />; }
