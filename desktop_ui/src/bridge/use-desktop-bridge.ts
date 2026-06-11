import { useEffect, useState } from "react"
import { createMockBridge } from "@/bridge/adapters/mock-bridge"
import { createPywebviewBridge } from "@/bridge/adapters/pywebview-bridge"
import type { DesktopBridge } from "@/bridge/types"

const createAvailableBridge = (): DesktopBridge => {
    const api = window.pywebview?.api

    if (api) {
      return createPywebviewBridge(api)
    }

    return createMockBridge()
}

export const useDesktopBridge = () => {
  const [bridge, setBridge] = useState<DesktopBridge>(() => createAvailableBridge())

  useEffect(() => {
    const updateBridge = () => {
      setBridge(createAvailableBridge())
    }

    window.addEventListener("pywebviewready", updateBridge)
    updateBridge()

    return () => window.removeEventListener("pywebviewready", updateBridge)
  }, [])

  return bridge
}
