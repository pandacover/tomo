import { type ChangeEvent, type KeyboardEvent, useEffect, useRef, useState } from "react"
import { Loader, Mic, Paperclip, Send, Square, X } from "lucide-react"
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
  messageCount: number
  model: string
  sessionName: string
  voiceState: VoiceState
}

export const Composer = ({
  bridge,
  busy,
  disabled,
  messageCount,
  model,
  sessionName,
  voiceState,
}: ComposerProps) => {
  const fileRef = useRef<HTMLInputElement | null>(null)
  const prevMessageCountRef = useRef(messageCount)
  const voiceSendPendingRef = useRef(false)
  const [text, setText] = useState("")
  const [attachments, setAttachments] = useState<ComposerAttachment[]>([])
  const [activeRef, setActiveRef] = useState<string | null>(null)

  useEffect(() => {
    void bridge.setPendingMessageImages(attachments.map((attachment) => attachment.url))
  }, [attachments, bridge])

  useEffect(() => {
    if (voiceState === "sending") {
      voiceSendPendingRef.current = true
    }

    const messageAdded = messageCount > prevMessageCountRef.current
    prevMessageCountRef.current = messageCount

    if (voiceSendPendingRef.current && messageAdded) {
      setText("")
      setAttachments([])
      setActiveRef(null)
      voiceSendPendingRef.current = false
      return
    }

    if (voiceState === "idle") {
      voiceSendPendingRef.current = false
    }
  }, [messageCount, voiceState])

  const canSubmit = !disabled && (text.trim().length > 0 || attachments.length > 0)
  const isListening = voiceState === "listening"
  const isSending = voiceState === "sending"

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
    const imageUrls = attachments.map((attachment) => attachment.url)
    const result = await bridge.sendMessage(
      sendText,
      imageUrls.length > 0 ? imageUrls : undefined,
    )

    if (result.ok) {
      setText("")
      setAttachments([])
      setActiveRef(null)
      void bridge.setPendingMessageImages([])
    }
  }

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault()
      void submit()
    }
  }

  const toggleVoice = async () => {
    if (isListening || isSending) {
      await bridge.cancelVoiceInput()
      return
    }

    await bridge.toggleVoiceInput()
  }

  return (
    <section className="p-3">
      <div className="flex flex-col gap-2 rounded-[22px] bg-black/85 p-2.5 text-zinc-100">
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
          <div className="absolute bottom-1.5 left-1.5 right-1.5 flex items-center gap-2">
            <div className="flex min-w-0 items-center gap-1">
              <IconButton
                disabled={disabled}
                label="Attach image"
                onClick={() => fileRef.current?.click()}
              >
                <Paperclip className="size-5" />
              </IconButton>
              {[model, sessionName].filter(Boolean).length > 0 ? (
                <span className="truncate text-[10px] text-zinc-400/80">
                  {[model, sessionName].filter(Boolean).join(" · ")}
                </span>
              ) : null}
            </div>
            <div className="flex-1" />
            <div className="flex items-center gap-1">
              <IconButton
                className={cn(
                  (isListening || isSending) &&
                    "h-10 w-auto min-w-10 gap-2 px-3",
                  isListening &&
                    "bg-red-700 text-white hover:bg-red-700",
                  isSending &&
                    "bg-white/10 text-zinc-100 hover:bg-white/10",
                  busy && "text-emerald-300",
                )}
                disabled={busy && voiceState === "idle"}
                label={isListening ? "Stop listening" : isSending ? "Cancel send" : "Voice input"}
                onClick={() => void toggleVoice()}
              >
                {isListening ? (
                  <>
                    <span className="text-xs font-medium leading-none">Listening</span>
                    <Square className="size-4 fill-current" />
                  </>
                ) : isSending ? (
                  <>
                    <span className="text-xs font-medium leading-none">Sending</span>
                    <Loader className="size-4 animate-spin" />
                  </>
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
