import { memo } from "react"
import type { TranscriptItem } from "@/state/desktop-reducer"
import { cn } from "@/lib/utils"
import { Streamdown } from "streamdown"

type MessageItem = Extract<TranscriptItem, { type: "message" }>

interface MessageBubbleProps {
  item: MessageItem
  isStreaming?: boolean
}

export const MessageBubble = memo(({ item, isStreaming = false }: MessageBubbleProps) => {
  const showStreamingText = item.role === "assistant" && isStreaming

  return (
    <section className="mb-3 flex max-w-[88%] flex-col gap-2">
      {item.images.length > 0 ? (
        <div className="grid gap-2">
          {item.images.map((url) => (
            <img
              alt={item.role === "user" ? "Attached image" : "Generated image"}
              className="max-w-full rounded-md border border-white/15"
              key={url}
              src={url}
            />
          ))}
        </div>
      ) : null}
      {item.text ? (
        <div
          className={cn(
            "w-fit max-w-full rounded-lg border px-3 py-2.5 text-[14px] leading-[1.45] text-white shadow-sm",
            item.role === "user"
              ? "border-emerald-950/20 bg-emerald-950/20"
              : "border-white/15 bg-white/20",
          )}
        >
          {showStreamingText ? (
            <p className="[overflow-wrap:anywhere] whitespace-pre-wrap">{item.text}</p>
          ) : (
            <Streamdown className="[overflow-wrap:anywhere] [&_*:first-child]:mt-0 [&_*:last-child]:mb-0">
              {item.text}
            </Streamdown>
          )}
        </div>
      ) : null}
    </section>
  )
})

MessageBubble.displayName = "MessageBubble"