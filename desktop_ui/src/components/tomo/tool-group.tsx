import {
  ChainOfThought,
  ChainOfThoughtContent,
  ChainOfThoughtHeader,
  ChainOfThoughtStep,
} from "@/components/ai-elements/chain-of-thought"
import type { TranscriptItem } from "@/state/desktop-reducer"
import { Wrench } from "lucide-react"

type ToolItem = Extract<TranscriptItem, { type: "tools" }>

interface ToolGroupProps {
  item: ToolItem
}

export const ToolGroup = ({ item }: ToolGroupProps) => (
  <section className="mb-3 w-fit max-w-[88%] overflow-hidden rounded-lg border border-white/15 bg-white/10 px-3 py-2 text-white">
    <ChainOfThought defaultOpen>
      <ChainOfThoughtHeader
        className="text-white/80 hover:text-white"
        icon={Wrench}
      >
        Tools · {item.calls.length} call{item.calls.length === 1 ? "" : "s"}
      </ChainOfThoughtHeader>
      <ChainOfThoughtContent>
        {item.calls.map((call, index) => (
          <ChainOfThoughtStep
            className="text-white/75"
            icon={Wrench}
            key={`${call.name}-${index}`}
            label={call.summary || call.name}
            description={call.input || undefined}
            status="complete"
          />
        ))}
      </ChainOfThoughtContent>
    </ChainOfThought>
  </section>
)
