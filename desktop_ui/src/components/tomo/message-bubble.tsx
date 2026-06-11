import type { TranscriptItem } from "@/state/desktop-reducer"
import { cn } from "@/lib/utils"

type MessageItem = Extract<TranscriptItem, { type: "message" }>

interface MessageBubbleProps {
  item: MessageItem
}

export const MessageBubble = ({ item }: MessageBubbleProps) => (
  <section className={cn("mb-3 max-w-[88%]", item.role === "user" ? "ml-0" : "ml-0")}>
    <div className="mb-1 ml-0.5 text-[11px] text-white/55">
      {item.role === "user" ? "You" : "Tomo"}
    </div>
    <div
      className={cn(
        "w-fit whitespace-pre-wrap rounded-lg border px-3 py-2.5 text-[14px] leading-[1.45] text-white shadow-sm backdrop-blur-xl [overflow-wrap:anywhere]",
        item.role === "user"
          ? "border-emerald-950/20 bg-emerald-950/20"
          : "border-white/15 bg-white/20",
      )}
    >
      {item.text}
    </div>
    {item.images.length > 0 ? (
      <div className="mt-2 grid gap-2">
        {item.images.map((url) => (
          <img
            alt="Generated"
            className="max-w-full rounded-md border border-white/15"
            key={url}
            src={url}
          />
        ))}
      </div>
    ) : null}
  </section>
)
