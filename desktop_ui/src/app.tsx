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
  type DesktopState,
} from "@/state/desktop-reducer"

export const App = () => {
  const bridge = useDesktopBridge()
  const [state, dispatch] = useReducer(desktopReducer, initialDesktopState)
  const shellRef = useRef<HTMLDivElement | null>(null)
  const stateRef = useRef<DesktopState>(state)
  const lastAppliedHeightRef = useRef(0)
  const resizeTimerRef = useRef<number | null>(null)

  stateRef.current = state

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

    const applyResize = () => {
      const height = Math.max(
        FLYOUT_MIN_HEIGHT,
        Math.min(FLYOUT_MAX_HEIGHT, Math.ceil(element.scrollHeight)),
      )

      if (height === lastAppliedHeightRef.current) {
        return
      }

      lastAppliedHeightRef.current = height
      const currentState = stateRef.current

      void bridge.logClientEvent("app.resize_requested", {
        scrollHeight: element.scrollHeight,
        clientHeight: element.clientHeight,
        targetHeight: height,
        messageCount: currentState.messages.length,
        hasApproval: Boolean(currentState.pendingApproval),
      })
      void bridge.resizeFlyout(height)
    }

    const scheduleResize = () => {
      if (resizeTimerRef.current !== null) {
        window.clearTimeout(resizeTimerRef.current)
      }

      resizeTimerRef.current = window.setTimeout(() => {
        resizeTimerRef.current = null
        applyResize()
      }, 0)
    }

    window.scheduleResize = scheduleResize
    scheduleResize()
    const observer = new ResizeObserver(scheduleResize)
    observer.observe(element)

    return () => {
      if (resizeTimerRef.current !== null) {
        window.clearTimeout(resizeTimerRef.current)
      }

      if (window.scheduleResize === scheduleResize) {
        delete window.scheduleResize
      }

      observer.disconnect()
    }
  }, [bridge])

  return (
    <div className="dark">
      <div
        className="flex max-h-175 min-h-24 flex-col overflow-hidden rounded-[22px] border border-white/10 bg-black/80 text-white shadow-2xl shadow-black/30"
        ref={shellRef}
      >
        <Transcript
          messages={state.messages}
          streamingAssistantId={state.streamingAssistantId}
        />
        <div className="shrink-0">
          <ApprovalPanel approval={state.pendingApproval} bridge={bridge} />
          <Composer
            bridge={bridge}
            busy={state.busy}
            disabled={state.busy || Boolean(state.pendingApproval)}
            messageCount={state.messages.length}
            model={state.model}
            sessionName={state.sessionName}
            voiceState={state.voiceState}
          />
        </div>
      </div>
    </div>
  )
}