export const glossary = {
  memories: {
    title: "memories",
    subtitle: "Durable context Tomo stores in MEMORY.md and retrieves with read_memory.",
  },
  integrations: {
    title: "integrations",
    subtitle: "Skills, tools, and gateways Tomo can use during agent runs.",
  },
  scheduledTasks: {
    title: "scheduled tasks",
    subtitle: "One-shot reminders and actions stored in .tomo/scheduled_tasks.json.",
  },
  pendingApprovals: {
    title: "pending approvals",
    subtitle:
      "Runtime tool and social actions that need a human decision before Tomo can proceed.",
  },
  importMemory: {
    title: "import memory",
    subtitle: "Append parsed content to MEMORY.md via append_memory.",
  },
} as const;