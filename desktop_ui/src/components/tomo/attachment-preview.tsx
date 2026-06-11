import { X } from "lucide-react"

interface AttachmentPreviewProps {
  url: string
  onRemove: () => void
}

export const AttachmentPreview = ({ url, onRemove }: AttachmentPreviewProps) => (
  <div className="group relative size-16 shrink-0 overflow-hidden rounded-xl border border-white/15 bg-black/25 shadow-sm">
    <img alt="Attachment preview" className="size-full object-cover" src={url} />
    <button
      aria-label="Remove attachment"
      className="absolute right-1 top-1 flex size-5 items-center justify-center rounded-full bg-black/70 text-white opacity-0 transition-opacity group-hover:opacity-100"
      onClick={onRemove}
      type="button"
    >
      <X className="size-3" />
    </button>
  </div>
)
