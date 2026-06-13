import type { Connection } from "@/domain/connection";

export const mockConnections: Connection[] = [
  {
    id: "chat-desktop",
    name: "desktop",
    category: "chat",
    description: "Local desktop chat surface.",
    status: "connected",
    enabled: true,
  },
  {
    id: "chat-telegram",
    name: "telegram",
    category: "chat",
    description: "Telegram chat surface.",
    status: "needs_setup",
    enabled: false,
    reviewRequired: true,
  },
  {
    id: "social-x",
    name: "x",
    category: "social",
    description: "Managed X social browser.",
    status: "available",
    enabled: true,
  },
  {
    id: "custom-react-grab-mcp",
    name: "react-grab-mcp",
    category: "custom",
    description: "Local custom MCP connector.",
    status: "available",
    enabled: true,
    metadata: { toolCount: 1 },
  },
];
