import { Wrench } from "lucide-react"
import type { TranscriptItem } from "@/state/desktop-reducer"

type ToolItem = Extract<TranscriptItem, { type: "tools" }>

interface ToolGroupProps {
  item: ToolItem
}

export const ToolGroup = ({ item }: ToolGroupProps) => (
  <section className="mb-3 w-fit max-w-[88%] overflow-hidden rounded-lg border border-white/15 bg-white/20 text-white backdrop-blur-xl">
    <details>
      <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2 text-xs text-white/90 [&::-webkit-details-marker]:hidden">
        <Wrench className="size-3.5 text-emerald-300" />
        tools {item.calls.length} tool call{item.calls.length === 1 ? "" : "s"}
      </summary>
      <div className="grid gap-1 px-3 pb-2 font-mono text-xs text-white/80">
        {item.calls.map((call, index) => (
          <div className="[overflow-wrap:anywhere]" key={`${call.name}-${index}`}>
            {call.name}: "{call.input || ""}"
          </div>
        ))}
      </div>
    </details>
  </section>
)
