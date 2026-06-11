import type { DesktopBridge, DesktopBridgeApi } from "@/bridge/types"

export const createPywebviewBridge = (api: DesktopBridgeApi): DesktopBridge => ({
  kind: "pywebview",
  bootstrap: () => api.bootstrap(),
  pollEvents: () => api.poll_events(),
  sendMessage: (text) => api.send_message(text),
  resolveApproval: (id, approved) => api.resolve_approval(id, approved),
  startVoiceInput: () => api.start_voice_input(),
  toggleVoiceInput: () => api.toggle_voice_input(),
  stopVoiceInput: () => api.stop_voice_input(),
  cancelVoiceInput: () => api.cancel_voice_input(),
  resizeFlyout: (height) => api.resize_flyout(height),
  showWindow: () => api.show_window(),
  hideWindow: () => api.hide_window(),
  quitApp: () => api.quit_app(),
  logClientEvent: (message, details) => api.log_client_event(message, details),
})
