import { type ChangeEvent, type KeyboardEvent, useRef, useState } from "react"
import { Mic, Paperclip, Send, Square, X } from "lucide-react"
import { AttachmentPreview } from "@/components/tomo/attachment-preview"
import { IconButton } from "@/components/tomo/icon-button"
import { Badge } from "@/components/ui/badge"
import type { DesktopBridge, VoiceState } from "@/bridge/types"
import { cn } from "@/lib/utils"

interface ComposerAttachment {
  id: string
  url: string
}

interface ComposerProps {
  bridge: DesktopBridge
  busy: boolean
  disabled: boolean
  model: string
  sessionName: string
  voiceState: VoiceState
}

export const Composer = ({
  bridge,
  busy,
  disabled,
  model,
  sessionName,
  voiceState,
}: ComposerProps) => {
  const fileRef = useRef<HTMLInputElement | null>(null)
  const [text, setText] = useState("")
  const [attachments, setAttachments] = useState<ComposerAttachment[]>([])
  const [activeRef, setActiveRef] = useState<string | null>(null)

  const canSubmit = !disabled && (text.trim().length > 0 || attachments.length > 0)

  const handleFiles = (event: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files || [])

    for (const file of files) {
      if (!file.type.startsWith("image/")) {
        continue
      }

      const reader = new FileReader()
      reader.onload = (readerEvent) => {
        const url = String(readerEvent.target?.result || "")

        if (url) {
          setAttachments((current) => [
            ...current,
            {
              id: `att-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`,
              url,
            },
          ])
        }
      }
      reader.readAsDataURL(file)
    }

    event.target.value = ""
  }

  const submit = async () => {
    if (!canSubmit) {
      return
    }

    const trimmed = text.trim()
    const sendText = activeRef ? `[context: ${activeRef}] ${trimmed}` : trimmed
    const result = await bridge.sendMessage(sendText || "attached images")

    if (result.ok) {
      setText("")
      setAttachments([])
      setActiveRef(null)
    }
  }

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault()
      void submit()
    }
  }

  const toggleVoice = async () => {
    if (voiceState === "listening" || voiceState === "sending") {
      await bridge.cancelVoiceInput()
      return
    }

    await bridge.toggleVoiceInput()
  }

  return (
    <section className="p-3">
      <div className="flex flex-col gap-2 rounded-[22px] bg-black/85 p-2.5 text-zinc-100 backdrop-blur-xl">
        <div className="px-1 text-[10px] text-zinc-400/80">
          {[model, sessionName].filter(Boolean).join(" · ")}
        </div>

        {attachments.length > 0 ? (
          <div className="flex gap-2 px-1">
            {attachments.map((attachment) => (
              <AttachmentPreview
                key={attachment.id}
                onRemove={() =>
                  setAttachments((current) => current.filter((item) => item.id !== attachment.id))
                }
                url={attachment.url}
              />
            ))}
          </div>
        ) : null}

        {activeRef ? (
          <div className="px-1">
            <Badge className="gap-1 border-white/10 bg-white/10 text-zinc-200" variant="outline">
              {activeRef}
              <button aria-label="Clear reference" onClick={() => setActiveRef(null)} type="button">
                <X className="size-3" />
              </button>
            </Badge>
          </div>
        ) : null}

        <div className="relative min-h-16 rounded-[22px] bg-black/60">
          <textarea
            className="min-h-[64px] w-full resize-none bg-transparent px-3 py-2.5 pb-12 text-[14.5px] leading-[1.45] text-zinc-100 outline-none placeholder:text-zinc-500"
            disabled={disabled}
            onChange={(event) => setText(event.target.value)}
            onKeyDown={onKeyDown}
            placeholder="type something"
            rows={1}
            value={text}
          />
          <div className="absolute bottom-1.5 left-1.5 right-1.5 flex items-center">
            <IconButton
              disabled={disabled}
              label="Attach image"
              onClick={() => fileRef.current?.click()}
            >
              <Paperclip className="size-5" />
            </IconButton>
            <div className="flex-1" />
            <div className="flex items-center gap-1">
              <IconButton
                className={cn(
                  (voiceState === "listening" || voiceState === "sending") &&
                    "bg-red-700 text-white hover:bg-red-700",
                  busy && "text-emerald-300",
                )}
                disabled={busy && voiceState === "idle"}
                label={voiceState === "listening" ? "Cancel listening" : voiceState === "sending" ? "Stop sending" : "Voice input"}
                onClick={() => void toggleVoice()}
              >
                {voiceState === "listening" || voiceState === "sending" ? (
                  <Square className="size-4 fill-current" />
                ) : (
                  <Mic className="size-5" />
                )}
              </IconButton>
              <IconButton
                className="bg-emerald-700 text-white hover:bg-emerald-800 disabled:bg-emerald-700"
                disabled={!canSubmit}
                label="Send"
                onClick={() => void submit()}
              >
                <Send className="size-5" />
              </IconButton>
            </div>
          </div>
        </div>
      </div>
      <input
        accept="image/*"
        className="hidden"
        multiple
        onChange={handleFiles}
        ref={fileRef}
        type="file"
      />
    </section>
  )
}
