export type ConnectionCategory = "chat" | "app" | "social" | "custom";
export type ConnectionStatus = "connected" | "available" | "needs_setup" | "disabled" | "unknown";

export type Connection = {
  id: string;
  name: string;
  category: ConnectionCategory;
  description: string;
  status: ConnectionStatus;
  enabled: boolean;
  reviewRequired?: boolean;
  metadata?: Record<string, string | number | boolean | null>;
};
