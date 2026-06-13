import type { Integration } from "@/domain/integration";

export const mockIntegrations: Integration[] = [
  {
    id: "int-browser",
    name: "browser",
    kind: "tool",
    description: "Shared browser execution surface for tomo-controlled sessions.",
    scopes: ["browser", "network"],
    enabled: true,
  },
  {
    id: "int-skill-installer",
    name: "skill-installer",
    kind: "skill",
    description: "Installs curated or repo-hosted skills into local skill storage.",
    scopes: ["filesystem"],
    enabled: true,
    reviewRequired: true,
  },
  {
    id: "int-telegram",
    name: "telegram",
    kind: "gateway",
    description: "Routes approvals and alerts through the configured telegram bot.",
    scopes: ["approval_channel"],
    enabled: false,
    reviewRequired: true,
  },
];