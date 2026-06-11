import type {
  ApprovalRequest,
  BootstrapResponse,
  DesktopEvent,
  VoiceState,
} from "@/bridge/types"

export type TranscriptItem =
  | {
      id: string
      type: "message"
      role: "user" | "assistant"
      text: string
      images: string[]
    }
  | {
      id: string
      type: "tools"
      calls: Array<{ name: string; input: string }>
    }

export interface DesktopState {
  model: string
  sessionName: string
  messages: TranscriptItem[]
  busy: boolean
  voiceState: VoiceState
  pendingApproval: ApprovalRequest | null
  streamingAssistantId: string | null
  currentToolGroupId: string | null
}

export type DesktopAction =
  | { type: "bootstrapped"; data: BootstrapResponse }
  | { type: "desktop_event"; event: DesktopEvent }

export const initialDesktopState: DesktopState = {
  model: "",
  sessionName: "",
  messages: [],
  busy: false,
  voiceState: "idle",
  pendingApproval: null,
  streamingAssistantId: null,
  currentToolGroupId: null,
}

const makeId = (prefix: string) =>
  `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`

const addMessage = (
  state: DesktopState,
  role: "user" | "assistant",
  text: string,
  images: string[] = [],
): DesktopState => ({
  ...state,
  currentToolGroupId: role === "user" ? null : state.currentToolGroupId,
  streamingAssistantId: null,
  messages: [
    ...state.messages,
    {
      id: makeId(role),
      type: "message",
      role,
      text,
      images,
    },
  ],
})

const applyAssistantDelta = (state: DesktopState, text: string): DesktopState => {
  const existingId = state.streamingAssistantId

  if (existingId) {
    return {
      ...state,
      messages: state.messages.map((item) =>
        item.id === existingId && item.type === "message"
          ? { ...item, text: item.text + text }
          : item,
      ),
    }
  }

  const id = makeId("assistant")

  return {
    ...state,
    streamingAssistantId: id,
    currentToolGroupId: null,
    messages: [
      ...state.messages,
      {
        id,
        type: "message",
        role: "assistant",
        text,
        images: [],
      },
    ],
  }
}

const finalizeAssistantMessage = (
  state: DesktopState,
  text: string,
  images: string[] = [],
): DesktopState => {
  if (!state.streamingAssistantId) {
    return addMessage(state, "assistant", text, images)
  }

  return {
    ...state,
    streamingAssistantId: null,
    currentToolGroupId: null,
    messages: state.messages.map((item) =>
      item.id === state.streamingAssistantId && item.type === "message"
        ? {
            ...item,
            text: item.text || text,
            images,
          }
        : item,
    ),
  }
}

const addToolCall = (state: DesktopState, name: string, input: string): DesktopState => {
  const groupId = state.currentToolGroupId

  if (groupId) {
    return {
      ...state,
      messages: state.messages.map((item) =>
        item.id === groupId && item.type === "tools"
          ? { ...item, calls: [...item.calls, { name, input }] }
          : item,
      ),
    }
  }

  const id = makeId("tools")

  return {
    ...state,
    currentToolGroupId: id,
    messages: [
      ...state.messages,
      {
        id,
        type: "tools",
        calls: [{ name, input }],
      },
    ],
  }
}

export const desktopReducer = (
  state: DesktopState,
  action: DesktopAction,
): DesktopState => {
  if (action.type === "bootstrapped") {
    const { data } = action

    return {
      ...state,
      model: data.model,
      sessionName: data.session.name,
      busy: data.busy,
      voiceState: data.voice_state || "idle",
      messages: data.messages.map((message) => ({
        id: makeId(String(message.role)),
        type: "message",
        role: message.role === "user" ? "user" : "assistant",
        text: message.text,
        images: message.images || [],
      })),
    }
  }

  const { event } = action

  switch (event.type) {
    case "busy":
      return { ...state, busy: event.busy }
    case "user_message":
      return addMessage(state, "user", event.text)
    case "assistant_delta":
      return applyAssistantDelta(state, event.text)
    case "assistant_message":
      return finalizeAssistantMessage(state, event.text, event.images || [])
    case "tool_event":
      return addToolCall(state, event.name, event.input)
    case "approval_request":
      return {
        ...state,
        pendingApproval: {
          id: event.id,
          operation: event.operation,
          target: event.target,
          reason: event.reason,
        },
      }
    case "approval_resolved":
      return { ...state, pendingApproval: null }
    case "error":
      return addMessage(state, "assistant", `Error: ${event.message}`)
    case "voice_state":
      return { ...state, voiceState: event.state }
    case "voice_final":
      return { ...state, voiceState: "sending" }
    case "voice_error":
      return addMessage({ ...state, voiceState: "idle" }, "assistant", `Voice input error: ${event.message}`)
    case "voice_partial":
      return state
    default:
      return state
  }
}
