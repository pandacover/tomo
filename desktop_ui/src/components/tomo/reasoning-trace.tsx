import {
  ChainOfThought,
  ChainOfThoughtContent,
  ChainOfThoughtHeader,
  ChainOfThoughtStep,
} from "@/components/ai-elements/chain-of-thought"
import type { TranscriptItem } from "@/state/desktop-reducer"
import { BrainIcon } from "lucide-react"

type ReasoningItem = Extract<TranscriptItem, { type: "reasoning" }>

interface ReasoningTraceProps {
  item: ReasoningItem
}

export const ReasoningTrace = ({ item }: ReasoningTraceProps) => {
  const lines = item.text.split("\n").filter((line) => line.trim().length > 0)

  return (
    <section className="mb-3 w-fit max-w-[88%] overflow-hidden rounded-lg border border-white/15 bg-white/10 px-3 py-2 text-white">
      <ChainOfThought defaultOpen={false}>
        <ChainOfThoughtHeader
          className="text-white/80 hover:text-white"
          icon={BrainIcon}
        >
          Reasoning
        </ChainOfThoughtHeader>
        <ChainOfThoughtContent>
          {lines.map((line, index) => (
            <ChainOfThoughtStep
              className="text-white/75"
              key={`${index}-${line.slice(0, 24)}`}
              label={line}
              status="complete"
            />
          ))}
        </ChainOfThoughtContent>
      </ChainOfThought>
    </section>
  )
}