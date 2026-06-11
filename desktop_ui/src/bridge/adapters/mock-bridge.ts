import type {
  ActionResponse,
  BootstrapResponse,
  DesktopBridge,
  DesktopEvent,
  VoiceState,
} from "@/bridge/types"

const ok = (): ActionResponse => ({ ok: true })

export const createMockBridge = (): DesktopBridge => {
  const events: DesktopEvent[] = []
  let voiceState: VoiceState = "idle"

  const enqueue = (...nextEvents: DesktopEvent[]) => {
    events.push(...nextEvents)
  }

  return {
    kind: "mock",
    bootstrap: async (): Promise<BootstrapResponse> => ({
      ok: true,
      type: "ready",
      model: "mock-grok",
      session: { name: "Desktop" },
      busy: false,
      voice_state: "idle",
      messages: [
        {
          role: "assistant",
          text: "Mock desktop bridge ready. Send a message to preview streaming, tools, approvals, and voice states.",
          images: [],
        },
      ],
    }),
    pollEvents: async () => events.splice(0, events.length),
    sendMessage: async (text) => {
      const trimmed = text.trim()
      if (!trimmed) {
        return { ok: false, error: "Message cannot be empty." }
      }

      enqueue({ type: "busy", busy: true }, { type: "user_message", text: trimmed })

      window.setTimeout(() => {
        enqueue({ type: "tool_event", name: "mock_search", input: `{"query":"${trimmed}"}` })
      }, 160)
      window.setTimeout(() => enqueue({ type: "assistant_delta", text: "Here is a " }), 320)
      window.setTimeout(() => enqueue({ type: "assistant_delta", text: "mock streamed response." }), 520)
      window.setTimeout(() => {
        if (trimmed.toLowerCase().includes("approve")) {
          enqueue({
            type: "approval_request",
            id: "mock-approval",
            operation: "write",
            target: "demo.txt",
            reason: "This mock request demonstrates the approval panel.",
          })
        }
        enqueue({ type: "assistant_message", text: "Here is a mock streamed response.", images: [] })
        enqueue({ type: "busy", busy: false })
      }, 760)

      return ok()
    },
    resolveApproval: async (id, approved) => {
      enqueue({ type: "approval_resolved", id, approved })
      return ok()
    },
    startVoiceInput: async () => {
      voiceState = "listening"
      enqueue({ type: "voice_state", state: voiceState })
      return ok()
    },
    toggleVoiceInput: async () => {
      voiceState = voiceState === "listening" ? "idle" : "listening"
      enqueue({ type: "voice_state", state: voiceState })
      return ok()
    },
    stopVoiceInput: async () => {
      voiceState = "idle"
      enqueue({ type: "voice_state", state: voiceState })
      return ok()
    },
    cancelVoiceInput: async () => {
      voiceState = "idle"
      enqueue({ type: "voice_state", state: voiceState })
      return ok()
    },
    resizeFlyout: async (height) => ({ ok: true, height }),
    showWindow: async () => ok(),
    hideWindow: async () => ok(),
    quitApp: async () => ok(),
    logClientEvent: async (message, details) => {
      console.info(`[tomo.desktop] ${message}`, details)
      return ok()
    },
  }
}
