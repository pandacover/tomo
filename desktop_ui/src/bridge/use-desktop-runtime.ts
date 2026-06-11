import { type Dispatch, useEffect } from "react"
import type { DesktopAction } from "@/state/desktop-reducer"
import type { DesktopBridge } from "@/bridge/types"

const sleep = (ms: number) => new Promise((resolve) => window.setTimeout(resolve, ms))

export const useDesktopRuntime = (bridge: DesktopBridge, dispatch: Dispatch<DesktopAction>) => {
  useEffect(() => {
    let cancelled = false

    bridge.bootstrap().then((data) => {
      if (!cancelled) {
        dispatch({ type: "bootstrapped", data })
      }
    })

    return () => {
      cancelled = true
    }
  }, [bridge, dispatch])

  useEffect(() => {
    let cancelled = false

    const poll = async () => {
      while (!cancelled) {
        try {
          const events = await bridge.pollEvents()

          for (const event of events) {
            dispatch({ type: "desktop_event", event })
          }
        } catch (error) {
          dispatch({
            type: "desktop_event",
            event: {
              type: "error",
              message: error instanceof Error ? error.message : "Unable to poll desktop events.",
            },
          })
        }

        await sleep(180)
      }
    }

    void poll()

    return () => {
      cancelled = true
    }
  }, [bridge, dispatch])
}
