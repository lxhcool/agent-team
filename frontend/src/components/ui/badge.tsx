import * as React from "react"
import { cva, type VariantProps } from "class-variance-authority"

import { cn } from "@/lib/utils"

const badgeVariants = cva(
  "inline-flex items-center justify-center rounded-full border px-2.5 py-0.5 text-xs font-medium whitespace-nowrap transition-colors focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none",
  {
    variants: {
      variant: {
        default:
          "border-transparent bg-primary text-primary-foreground",
        secondary:
          "border-transparent bg-secondary text-secondary-foreground",
        outline:
          "border-border bg-background text-foreground",
        success:
          "border-transparent bg-emerald-50 text-emerald-700 dark:border-transparent dark:bg-emerald-500/10 dark:text-emerald-300",
        info:
          "border-transparent bg-indigo-50 text-indigo-700 dark:border-transparent dark:bg-indigo-500/10 dark:text-indigo-300",
        warning:
          "border-transparent bg-amber-50 text-amber-700 dark:border-transparent dark:bg-amber-500/10 dark:text-amber-300",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  }
)

function Badge({
  className,
  variant,
  ...props
}: React.ComponentProps<"span"> & VariantProps<typeof badgeVariants>) {
  return (
    <span
      data-slot="badge"
      className={cn(badgeVariants({ variant }), className)}
      {...props}
    />
  )
}

export { Badge, badgeVariants }
