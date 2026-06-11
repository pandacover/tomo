import type { DesktopBridge, DesktopBridgeApi } from "@/bridge/types"

export const createPywebviewBridge = (api: DesktopBridgeApi): DesktopBridge => ({
  kind: "pywebview",
  bootstrap: () => api.bootstrap(),
  pollEvents: () => api.poll_events(),
  sendMessage: (text, images) => api.send_message(text, images),
  setPendingMessageImages: (images) => api.set_pending_message_images(images),
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
