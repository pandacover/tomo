import { ShieldAlert } from "lucide-react"
import { Button } from "@/components/ui/button"
import type { ApprovalRequest, DesktopBridge } from "@/bridge/types"

interface ApprovalPanelProps {
  approval: ApprovalRequest | null
  bridge: DesktopBridge
}

export const ApprovalPanel = ({ approval, bridge }: ApprovalPanelProps) => {
  if (!approval) {
    return null
  }

  return (
    <section className="border-t border-amber-950/10 bg-amber-50 px-3.5 py-3 text-zinc-950">
      <div className="flex items-center gap-2 text-sm font-semibold">
        <ShieldAlert className="size-4 text-amber-700" />
        Approval required
      </div>
      <div className="mt-1 whitespace-pre-wrap text-sm text-amber-950/80 [overflow-wrap:anywhere]">
        {approval.operation} {approval.target}
        {"\n\n"}
        {approval.reason}
      </div>
      <div className="mt-2 flex gap-2">
        <Button
          className="h-8"
          onClick={() => void bridge.resolveApproval(approval.id, true)}
          type="button"
        >
          Approve
        </Button>
        <Button
          className="h-8"
          onClick={() => void bridge.resolveApproval(approval.id, false)}
          type="button"
          variant="destructive"
        >
          Deny
        </Button>
      </div>
    </section>
  )
}
