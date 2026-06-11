import type { ButtonHTMLAttributes, ReactNode } from "react"
import { Button } from "@/components/ui/button"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"

interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  label: string
  children: ReactNode
}

export const IconButton = ({ label, children, className, ...props }: IconButtonProps) => (
  <TooltipProvider delayDuration={250}>
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          aria-label={label}
          className={cn("size-10 rounded-lg border-0 bg-transparent text-zinc-300 hover:bg-white/10", className)}
          size="icon"
          title={label}
          type="button"
          variant="ghost"
          {...props}
        >
          {children}
        </Button>
      </TooltipTrigger>
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  </TooltipProvider>
)
