import { memo, useEffect, useRef } from "react"
import { MessageBubble } from "@/components/tomo/message-bubble"
import { ReasoningTrace } from "@/components/tomo/reasoning-trace"
import { ToolGroup } from "@/components/tomo/tool-group"
import type { TranscriptItem } from "@/state/desktop-reducer"

const SCROLL_PIN_THRESHOLD_PX = 48

interface TranscriptProps {
  messages: TranscriptItem[]
  streamingAssistantId: string | null
}

export const Transcript = memo(({ messages, streamingAssistantId }: TranscriptProps) => {
  const scrollRef = useRef<HTMLDivElement | null>(null)
  const pinnedToBottomRef = useRef(true)

  const handleScroll = () => {
    const element = scrollRef.current

    if (!element) {
      return
    }

    const distanceFromBottom =
      element.scrollHeight - element.scrollTop - element.clientHeight
    pinnedToBottomRef.current = distanceFromBottom <= SCROLL_PIN_THRESHOLD_PX
  }

  useEffect(() => {
    if (!pinnedToBottomRef.current) {
      return
    }

    const element = scrollRef.current

    if (!element) {
      return
    }

    element.scrollTop = element.scrollHeight
  }, [messages])

  return (
    <div
      className="min-h-0 flex-1 overflow-y-auto"
      onScroll={handleScroll}
      ref={scrollRef}
    >
      <div className="h-full px-4 pb-3 pt-4">
        {messages.length === 0 ? <div className="hidden">No messages yet.</div> : null}
        {messages.map((item) => {
          if (item.type === "message") {
            return (
              <MessageBubble
                isStreaming={item.id === streamingAssistantId}
                item={item}
                key={item.id}
              />
            )
          }

          if (item.type === "tools") {
            return <ToolGroup item={item} key={item.id} />
          }

          return <ReasoningTrace item={item} key={item.id} />
        })}
      </div>
    </div>
  )
})

Transcript.displayName = "Transcript"