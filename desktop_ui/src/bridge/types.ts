export type VoiceState = "idle" | "listening" | "sending"

export interface ActionResponse {
  ok: boolean
  error?: string
}

export interface ResizeResponse extends ActionResponse {
  height?: number
}

export interface SessionMetadata {
  name: string
}

export interface BootstrapMessage {
  role: "user" | "assistant" | string
  text: string
  images?: string[]
}

export interface BootstrapResponse extends ActionResponse {
  type?: "ready"
  model: string
  session: SessionMetadata
  messages: BootstrapMessage[]
  busy: boolean
  voice_state: VoiceState
}

export interface ApprovalRequest {
  id: string
  operation: string
  target: string
  reason: string
}

export type DesktopEvent =
  | { type: "busy"; busy: boolean }
  | { type: "user_message"; text: string; images?: string[] }
  | { type: "assistant_delta"; text: string }
  | { type: "assistant_message"; text: string; images?: string[] }
  | { type: "tool_event"; name: string; input: string; summary?: string }
  | { type: "reasoning_event"; text: string }
  | ({ type: "approval_request" } & ApprovalRequest)
  | { type: "approval_resolved"; id: string; approved: boolean }
  | { type: "cross_gateway_message"; source: string; text: string; channel_id: string }
  | { type: "error"; message: string }
  | { type: "voice_state"; state: VoiceState }
  | { type: "voice_partial"; text: string }
  | { type: "voice_final"; text: string; send_delay: number }
  | { type: "voice_error"; message: string }

export interface DesktopBridgeApi {
  bootstrap(): Promise<BootstrapResponse>
  poll_events(): Promise<DesktopEvent[]>
  send_message(text: string, images?: string[]): Promise<ActionResponse>
  set_pending_message_images(images?: string[]): Promise<ActionResponse>
  resolve_approval(id: string, approved: boolean): Promise<ActionResponse>
  start_voice_input(): Promise<ActionResponse>
  toggle_voice_input(): Promise<ActionResponse>
  stop_voice_input(): Promise<ActionResponse>
  cancel_voice_input(): Promise<ActionResponse>
  resize_flyout(height: number): Promise<ResizeResponse>
  show_window(): Promise<ActionResponse>
  hide_window(): Promise<ActionResponse>
  quit_app(): Promise<ActionResponse>
  log_client_event(message: string, details?: unknown): Promise<ActionResponse>
}

export interface DesktopBridge {
  kind: "pywebview" | "mock"
  bootstrap(): Promise<BootstrapResponse>
  pollEvents(): Promise<DesktopEvent[]>
  sendMessage(text: string, images?: string[]): Promise<ActionResponse>
  setPendingMessageImages(images?: string[]): Promise<ActionResponse>
  resolveApproval(id: string, approved: boolean): Promise<ActionResponse>
  startVoiceInput(): Promise<ActionResponse>
  toggleVoiceInput(): Promise<ActionResponse>
  stopVoiceInput(): Promise<ActionResponse>
  cancelVoiceInput(): Promise<ActionResponse>
  resizeFlyout(height: number): Promise<ResizeResponse>
  showWindow(): Promise<ActionResponse>
  hideWindow(): Promise<ActionResponse>
  quitApp(): Promise<ActionResponse>
  logClientEvent(message: string, details?: unknown): Promise<ActionResponse>
}

declare global {
  interface Window {
    pywebview?: {
      api?: DesktopBridgeApi
    }
    scheduleResize?: () => void
    __tomoDispatchEvent?: (event: DesktopEvent) => void
  }
}
