import { useLayoutEffect, useReducer, useRef } from "react"
import { useDesktopBridge } from "@/bridge/use-desktop-bridge"
import { useDesktopRuntime } from "@/bridge/use-desktop-runtime"
import { ApprovalPanel } from "@/components/tomo/approval-panel"
import { Composer } from "@/components/tomo/composer"
import { Transcript } from "@/components/tomo/transcript"
import { FLYOUT_MAX_HEIGHT, FLYOUT_MIN_HEIGHT } from "@/state/desktop-events"
import {
  desktopReducer,
  initialDesktopState,
} from "@/state/desktop-reducer"

export const App = () => {
  const bridge = useDesktopBridge()
  const [state, dispatch] = useReducer(desktopReducer, initialDesktopState)
  const shellRef = useRef<HTMLDivElement | null>(null)

  useDesktopRuntime(bridge, dispatch)

  useLayoutEffect(() => {
    void bridge.logClientEvent("app.mounted", {
      bridgeKind: bridge.kind,
      userAgent: window.navigator.userAgent,
      viewport: {
        width: window.innerWidth,
        height: window.innerHeight,
      },
    })
  }, [bridge])

  useLayoutEffect(() => {
    const element = shellRef.current

    if (!element) {
      return
    }

    const resize = () => {
      const height = Math.max(
        FLYOUT_MIN_HEIGHT,
        Math.min(FLYOUT_MAX_HEIGHT, Math.ceil(element.scrollHeight)),
      )

      void bridge.logClientEvent("app.resize_requested", {
        scrollHeight: element.scrollHeight,
        clientHeight: element.clientHeight,
        targetHeight: height,
        messageCount: state.messages.length,
        hasApproval: Boolean(state.pendingApproval),
      })
      void bridge.resizeFlyout(height)
    }

    window.scheduleResize = resize
    resize()
    const observer = new ResizeObserver(resize)
    observer.observe(element)

    return () => {
      if (window.scheduleResize === resize) {
        delete window.scheduleResize
      }
      observer.disconnect()
    }
  }, [bridge, state.messages, state.pendingApproval, state.busy, state.voiceState])

  return (
    <div className="dark">
      <div
        className="flex max-h-[700px] min-h-[96px] flex-col overflow-hidden rounded-[22px] border border-white/10 bg-black/80 text-white shadow-2xl shadow-black/30"
        ref={shellRef}
      >
        <Transcript messages={state.messages} />
        <div className="shrink-0">
          <ApprovalPanel approval={state.pendingApproval} bridge={bridge} />
          <Composer
            bridge={bridge}
            busy={state.busy}
            disabled={state.busy || Boolean(state.pendingApproval)}
            model={state.model}
            sessionName={state.sessionName}
            voiceState={state.voiceState}
          />
        </div>
      </div>
    </div>
  )
}
