import { useEffect, useRef } from "react"
import { ScrollArea } from "@/components/ui/scroll-area"
import { MessageBubble } from "@/components/tomo/message-bubble"
import { ToolGroup } from "@/components/tomo/tool-group"
import type { TranscriptItem } from "@/state/desktop-reducer"

interface TranscriptProps {
  messages: TranscriptItem[]
}

export const Transcript = ({ messages }: TranscriptProps) => {
  const bottomRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" })
  }, [messages])

  return (
    <ScrollArea className="min-h-0 flex-1">
      <div className="px-4 pb-3 pt-4">
        {messages.length === 0 ? <div className="hidden">No messages yet.</div> : null}
        {messages.map((item) =>
          item.type === "message" ? (
            <MessageBubble item={item} key={item.id} />
          ) : (
            <ToolGroup item={item} key={item.id} />
          ),
        )}
        <div ref={bottomRef} />
      </div>
    </ScrollArea>
  )
}
