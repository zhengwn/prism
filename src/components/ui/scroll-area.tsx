import * as React from "react";
import { cn } from "@/lib/utils";

/**
 * Plain scroll area — uses native overflow. We skip @radix-ui/react-scroll-area
 * for v0.1 to keep the dep tree small.
 */
export const ScrollArea = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, children, ...props }, ref) => (
    <div
      ref={ref}
      className={cn("relative overflow-y-auto overflow-x-hidden", className)}
      {...props}
    >
      {children}
    </div>
  ),
);
ScrollArea.displayName = "ScrollArea";
