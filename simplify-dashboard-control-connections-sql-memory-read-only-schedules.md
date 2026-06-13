# Simplify Dashboard Control: Connections, SQL Memory, Read-Only Schedules

## Summary

Refactor the dashboard/control-plane model away from low-level agent internals.

Replace the current dashboard concepts:

- `integrations` = tools + skills + gateways
- `pending approvals`
- file-backed `MEMORY.md`

With simpler user-facing concepts:

- `connections`
  - `chats`
  - `apps`
  - `socials`
  - `custom`
- SQL-backed memory with adapter seams
- predefined read-only scheduled tasks

Runtime safety approvals stay inside Tomo’s desktop/Telegram runtime. The dashboard will no longer list or resolve approvals.

Chosen defaults:

- Rename dashboard/control API surface from `integrations` to `connections`.
- Keep `/v1/integrations` as a temporary compatibility alias to `/v1/connections`.
- Remove pending approvals from dashboard UI and overview stats.
- Keep runtime approval prompts in desktop/Telegram.
- Move memory to SQLite locally now.
- Design memory storage through adapters so Supabase or another SQL backend can be added later.
- Auto-migrate existing `MEMORY.md` entries into SQLite once.
- Scheduled tasks are read-only in the dashboard.

## Current State

Relevant current modules:

- `src/tomo/control_plane/models.py`
  - defines `ControlIntegration`, `ControlApproval`, `ControlMemoryEntry`, `ControlScheduledTask`
- `src/tomo/control_plane/plane.py`
  - exposes `list_integrations()`, `list_pending_approvals()`, `resolve_approval()`, memory methods, scheduled-task methods
- `src/tomo/control_plane/integration_adapter.py`
  - flattens tools, skills, desktop gateway, Telegram gateway into one integrations list
- `src/tomo/control_plane/memory_adapter.py`
  - parses and appends `MEMORY.md`
- `src/tomo/control_approval_store.py`
  - file-backed runtime approval coordination
- `dashboard/src/app/page.tsx`
  - shows approvals on the dashboard home page
- `dashboard/src/app/integrations/page.tsx`
  - displays integrations
- `dashboard/src/domain/integration.ts`
  - currently models `skill | tool | gateway`
- `dashboard/src/lib/api/schemas.ts`
  - validates current integration/approval/memory/scheduled-task payloads

## Target Product Model

### Connections

Dashboard connections are user-facing surfaces, not internal agent implementation details.

Connection categories:

- `chat`
  - desktop
  - telegram

- `app`
  - reserved for future app connectors
  - initially may be empty unless concrete app adapters exist

- `social`
  - x

- `custom`
  - external/local MCP-style connectors
  - initial source: `mcps/*`

Do not show:

- internal tools like `terminal`, `read_file`, `web_search`
- internal skills like `skill-installer`, `gen-z-phrasing`
- approval queues

### Memory

Memory becomes SQL-backed.

Local adapter:

- SQLite database at `.tomo/tomo.db`

Future adapters:

- Supabase SQL adapter
- other SQL adapter if needed

Existing `MEMORY.md` is not deleted. It becomes a migration source.

### Scheduled Tasks

Dashboard scheduled tasks become read-only.

No dashboard actions for now:

- no create
- no edit
- no cancel
- no re-enable
- no toggle

The dashboard lists predefined scheduled tasks and their current status only.

## Public Interfaces

### Python Models

Replace `ControlIntegration` with `ControlConnection`.

```python
ConnectionCategory = Literal["chat", "app", "social", "custom"]
ConnectionStatus = Literal["connected", "available", "needs_setup", "disabled", "unknown"]

class ControlConnection(ControlModel):
    id: str
    name: str
    category: ConnectionCategory
    description: str
    status: ConnectionStatus
    enabled: bool
    review_required: bool = Field(default=False, alias="reviewRequired")
    metadata: dict[str, str | int | bool | None] = Field(default_factory=dict)
```

Update overview:

```python
class ControlOverview(ControlModel):
    memory_count: int = Field(alias="memoryCount")
    memories_updated_this_week: int = Field(alias="memoriesUpdatedThisWeek")
    connection_count: int = Field(alias="connectionCount")
    connections_needing_review: int = Field(alias="connectionsNeedingReview")
    scheduled_task_count: int = Field(alias="scheduledTaskCount")
```

Remove from dashboard-facing models:

```python
ControlApproval
ControlApprovalResolution
pending_approval_count
tool_count
skill_count
gateway_count
integrations_needing_review
scheduled_tasks_gated
```

Keep runtime approval storage and responders, but no longer expose them through dashboard routes.

### Memory Models

Keep the dashboard-facing memory shape stable:

```python
class ControlMemoryEntry(ControlModel):
    id: str
    timestamp: str
    text: str
    title: str
    status: Literal["active", "disabled"]
    freshness: Literal["new", "updated", "stale"]
    updated_label: str = Field(alias="updatedLabel")
```

Add internal SQL record fields:

```python
class MemoryRecord:
    id: str
    created_at: str
    text: str
    source: Literal["manual", "import", "migration", "agent"]
    source_ref: str | None
    disabled_at: str | None
```

### Memory Repository Interface

Create:

```text
src/tomo/memory_store/
  __init__.py
  models.py
  repository.py
  sqlite_repository.py
  markdown_migration.py
```

Interface:

```python
class MemoryRepository(Protocol):
    def list(self) -> list[MemoryRecord]: ...
    def append(self, text: str, *, source: str = "manual", source_ref: str | None = None) -> MemoryRecord: ...
    def import_texts(self, texts: list[str], *, source_ref: str | None = None) -> list[MemoryRecord]: ...
    def mark_disabled(self, memory_id: str) -> bool: ...
```

SQLite adapter:

```python
class SQLiteMemoryRepository:
    def __init__(self, db_path: Path | None = None) -> None: ...
```

Default DB path:

```text
.tomo/tomo.db
```

Schema:

```sql
CREATE TABLE IF NOT EXISTS memories (
  id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  text TEXT NOT NULL,
  source TEXT NOT NULL,
  source_ref TEXT,
  disabled_at TEXT
);

CREATE TABLE IF NOT EXISTS memory_migrations (
  id TEXT PRIMARY KEY,
  source_path TEXT NOT NULL,
  source_mtime_ns INTEGER NOT NULL,
  migrated_at TEXT NOT NULL
);
```

Migration behavior:

- On first `SQLiteMemoryRepository` initialization, ensure schema exists.
- If `MEMORY.md` exists and has not been migrated for the current path/mtime marker, parse valid `[timestamp] text` lines.
- Insert each memory with deterministic id based on timestamp + text.
- Record migration in `memory_migrations`.
- Do not delete or rewrite `MEMORY.md`.
- New memory writes go only to SQLite.

### Control Plane Interface

Update `AgentControlPlane`:

```python
class AgentControlPlane:
    def health(self) -> ControlHealth: ...
    def overview(self) -> ControlOverview: ...

    def list_memories(self) -> list[ControlMemoryEntry]: ...
    def append_memory(self, text: str) -> ControlMemoryEntry: ...
    def import_memories(self, files: list[MemoryImportFile]) -> MemoryImportResult: ...

    def list_connections(self) -> list[ControlConnection]: ...

    def list_scheduled_tasks(self) -> list[ControlScheduledTask]: ...
```

Remove dashboard-facing methods:

```python
list_integrations()
list_pending_approvals()
resolve_approval()
cancel_scheduled_task()
```

Keep compatibility where needed:

```python
def list_integrations(self) -> list[ControlConnection]:
    return self.list_connections()
```

Only keep this as a transitional shim.

## HTTP Routes

Keep:

```text
GET  /v1/health
GET  /v1/overview
GET  /v1/memories
POST /v1/memories
POST /v1/memories/import
GET  /v1/scheduled-tasks
```

Add:

```text
GET /v1/connections
```

Temporary compatibility alias:

```text
GET /v1/integrations -> same payload as /v1/connections
```

Remove dashboard usage of:

```text
GET  /v1/approvals
POST /v1/approvals/{approval_id}
PATCH /v1/scheduled-tasks/{task_id}
```

Compatibility choice:

- Route handlers may remain for one release returning `410 Gone` with clear messages.
- Dashboard must stop calling them.

Error messages:

```text
Approvals are handled in the active Tomo gateway, not the dashboard.
Scheduled tasks are read-only in the dashboard.
```

## Connection Adapter

Replace:

```text
src/tomo/control_plane/integration_adapter.py
```

With:

```text
src/tomo/control_plane/connection_adapter.py
```

Responsibilities:

- Build `chat` connections:
  - desktop
  - telegram

- Build `social` connections:
  - x
  - status based on `social_browser`/known session availability where cheap
  - avoid expensive browser startup during listing

- Build `custom` connections:
  - scan `mcps/*`
  - for each directory, expose a custom connection
  - if tool metadata exists under `tools/*.json`, include tool count in metadata

- Build `app` connections:
  - return empty list for now
  - keep code path explicit for future adapters

Initial connection examples:

```python
ControlConnection(
    id="chat-desktop",
    name="desktop",
    category="chat",
    description="Local desktop chat surface.",
    status="connected" if desktop_running else "available",
    enabled=desktop_running,
)
```

```python
ControlConnection(
    id="chat-telegram",
    name="telegram",
    category="chat",
    description="Telegram chat surface.",
    status="connected" if telegram_running else "needs_setup",
    enabled=telegram_running and telegram_config is not None,
    review_required=telegram_config is None,
)
```

```python
ControlConnection(
    id="social-x",
    name="x",
    category="social",
    description="Managed X social browser.",
    status="available",
    enabled=True,
)
```

```python
ControlConnection(
    id="custom-react-grab-mcp",
    name="react-grab-mcp",
    category="custom",
    description="Local custom MCP connector.",
    status="available",
    enabled=True,
    metadata={"toolCount": 1},
)
```

## Dashboard Changes

Rename domain files:

```text
dashboard/src/domain/integration.ts -> dashboard/src/domain/connection.ts
dashboard/src/components/integrations/IntegrationList.tsx -> dashboard/src/components/connections/ConnectionList.tsx
dashboard/src/app/integrations/page.tsx -> dashboard/src/app/connections/page.tsx
dashboard/src/lib/mock/integrations.ts -> dashboard/src/lib/mock/connections.ts
```

Dashboard navigation/content:

- Home stat card:
  - label: `Connections`
  - href: `/connections`
  - value: `stats.connectionCount`
  - meta: `${stats.connectionsNeedingReview} need setup`

- Remove pending approvals section from home page.

- Connection page:
  - group by category
  - order: chats, apps, socials, custom
  - no toggle switch unless the action is backed by a real mutation
  - show status badge instead of fake local toggles

Connection UI states:

- `connected`
- `available`
- `needs setup`
- `disabled`
- `unknown`

Update glossary:

```ts
connections: {
  title: "connections",
  subtitle: "Chat surfaces, socials, apps, and custom MCP connectors Tomo can use."
}
```

Memory copy:

```ts
memories: {
  title: "memories",
  subtitle: "Durable context Tomo stores in local SQL and retrieves with read_memory."
}
```

Scheduled copy:

```ts
scheduledTasks: {
  title: "scheduled tasks",
  subtitle: "Predefined scheduled routines Tomo can run or report on."
}
```

## Dashboard Schemas

Replace:

```ts
integrationSchema
integrationsResponseSchema
```

With:

```ts
connectionSchema
connectionsResponseSchema
```

Schema:

```ts
export const connectionSchema = z.object({
  id: z.string(),
  name: z.string(),
  category: z.enum(["chat", "app", "social", "custom"]),
  description: z.string(),
  status: z.enum(["connected", "available", "needs_setup", "disabled", "unknown"]),
  enabled: z.boolean(),
  reviewRequired: z.boolean().optional(),
  metadata: z.record(z.string(), z.union([z.string(), z.number(), z.boolean(), z.null()])).optional(),
});
```

Overview schema:

```ts
export const overviewStatsSchema = z.object({
  memoryCount: z.number(),
  memoriesUpdatedThisWeek: z.number(),
  connectionCount: z.number(),
  connectionsNeedingReview: z.number(),
  scheduledTaskCount: z.number(),
});
```

Remove approval schema from dashboard fetch seam.

## Agent Tool Memory Integration

Current tools:

```python
append_memory
read_memory
```

must switch from `MEMORY.md` to the memory repository.

Implementation:

- `append_memory(text)` calls `get_memory_repository().append(text, source="agent")`
- `read_memory(query, k)` pulls active SQL memories and ranks them with existing BM25 logic
- Keep `MEMORY_FILE = Path("MEMORY.md")` only as migration source and compatibility constant

Add:

```text
src/tomo/memory_store/factory.py
```

```python
def get_memory_repository() -> MemoryRepository:
    return SQLiteMemoryRepository(settings.data_dir / "tomo.db")
```

Future Supabase seam:

```python
class SupabaseMemoryRepository:
    ...
```

Do not implement Supabase now.

## Scheduled Tasks

Control plane:

```python
def list_scheduled_tasks(self) -> list[ControlScheduledTask]:
    return self.scheduler.list()
```

Remove dashboard mutation route usage.

HTTP:

- `GET /v1/scheduled-tasks` remains.
- `PATCH /v1/scheduled-tasks/{task_id}` returns `410 Gone` or is kept unused. Recommended: return `410 Gone`.

Dashboard:

- Remove `Switch` from `ScheduledTaskList`.
- Remove `patchScheduledTaskAction`.
- Render only label, description, status, schedule label, and approval requirement if present.

## Approvals

Dashboard:

- Remove `ApprovalQueue` from `dashboard/src/app/page.tsx`.
- Remove approval fetch from `dashboard/src/lib/data/index.ts`.
- Remove approval actions from `dashboard/src/lib/api/actions.ts`.
- Remove approval schema from dashboard API schemas.
- Remove mock approvals.

Control plane:

- Remove approval methods from `AgentControlPlane`.
- Remove approval routes from active dashboard usage.
- Keep `control_approval_store.py` because desktop/Telegram runtime still use it.
- Keep `DesktopApprovalResponder` and `TelegramGateway.request_approval()` behavior.

Runtime:

- No behavior change to approval prompts in desktop/Telegram.
- Social publishing still requires runtime approval.
- Terminal/write/edit approval behavior remains unchanged.

## Migration Steps

1. Add memory repository package
   - `memory_store/models.py`
   - `memory_store/repository.py`
   - `memory_store/sqlite_repository.py`
   - `memory_store/markdown_migration.py`
   - `memory_store/factory.py`

2. Update `tools.py`
   - `append_memory` writes SQL
   - `read_memory` reads SQL
   - `MEMORY.md` only remains as migration source

3. Update control memory adapter
   - Replace file parser implementation with memory repository calls
   - Keep import file support
   - Auto migration runs before first list/import/append

4. Replace integration model with connection model
   - Add `ControlConnection`
   - Remove `ControlIntegration` from primary control plane
   - Add `ConnectionAdapter`

5. Update control plane
   - Add `list_connections`
   - Update overview fields
   - Remove dashboard approval methods
   - Make scheduled tasks read-only

6. Update HTTP adapter
   - Add `/v1/connections`
   - Alias `/v1/integrations`
   - Remove dashboard dependency on `/v1/approvals`
   - Make scheduled-task PATCH return `410 Gone`

7. Update dashboard domain/API
   - Rename integration files to connection files
   - Update routes to `/connections`
   - Update zod schemas
   - Remove approval data/actions/UI
   - Remove scheduled task mutation UI

8. Update tests
   - Add memory repository tests
   - Update control-plane tests
   - Update HTTP tests
   - Update dashboard build/schema expectations
   - Keep runtime approval tests

## Test Plan

### Python Memory Tests

Add `tests/test_memory_store.py`:

- creates `.tomo/tomo.db`
- initializes schema
- appends memory
- lists active memories newest first
- imports paragraph-separated text
- disables memory
- migrates valid `MEMORY.md` lines
- ignores invalid `MEMORY.md` lines
- migration is idempotent
- migration does not delete or modify `MEMORY.md`

### Python Control Plane Tests

Update `tests/test_control_plane.py`:

- `overview()` returns `connection_count`, no approval count
- `list_connections()` includes desktop, telegram, x, custom MCP
- `list_connections()` does not include internal tools
- `list_connections()` does not include skills
- `list_scheduled_tasks()` is read-only
- `append_memory()` writes SQLite
- `import_memories()` writes SQLite

### HTTP Tests

Update `tests/test_control_http_adapter.py`:

- `GET /v1/connections` returns connections
- `GET /v1/integrations` aliases connections during compatibility period
- `GET /v1/approvals` returns `410 Gone` or is removed from test coverage if route is deleted
- `POST /v1/approvals/{id}` returns `410 Gone`
- `PATCH /v1/scheduled-tasks/{id}` returns `410 Gone`
- memory import still works
- API key behavior still works

### Runtime Approval Tests

Keep and update only if needed:

- `tests/test_desktop.py`
- `tests/test_telegram.py`
- `tests/test_gateway.py`
- `tests/test_langgraph_agent.py`

Expected behavior:

- desktop approval prompt still works
- Telegram approval prompt still works
- social publish still requires runtime approval
- dashboard no longer participates in approval resolution

### Dashboard Build

Run:

```powershell
cd dashboard
bun run build
```

Expected:

- no references to approval UI/actions
- `/connections` builds
- `/integrations` dashboard route removed or redirected depending implementation choice
- scheduled-task list has no mutable switch

### Full Verification

```powershell
uv run pytest
cd dashboard
bun run build
```

## Acceptance Criteria

- Dashboard no longer shows pending approvals.
- Runtime approvals still work in Telegram and desktop.
- Dashboard no longer exposes internal tools or skills as integrations.
- Dashboard shows `connections` grouped into chats, apps, socials, custom.
- `/v1/connections` is the primary control route.
- `/v1/integrations` remains as a temporary compatibility alias.
- Memory reads/writes use SQLite by default.
- Existing `MEMORY.md` entries auto-migrate once into SQLite.
- `MEMORY.md` is not deleted or rewritten.
- Memory storage has a repository seam suitable for future Supabase adapter.
- Scheduled tasks are read-only in the dashboard.
- Full Python tests and dashboard build pass.

## Explicit Assumptions

- `apps` starts as an empty category unless a concrete app registry appears.
- `custom` means local/external MCP-style connectors discovered from `mcps/*`.
- SQLite uses Python stdlib `sqlite3`; no new Python dependency is needed.
- Supabase is not implemented now, only made easy through the memory repository interface.
- Approval removal is dashboard-only, not runtime safety removal.
- Scheduled tasks remain visible, but cannot be modified from the dashboard.
