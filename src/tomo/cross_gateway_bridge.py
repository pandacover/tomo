from __future__ import annotations

import atexit
import json
import logging
import os
import socket
import socketserver
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from .config import settings
from .tools import now_iso

LOGGER = logging.getLogger("tomo.cross_gateway_bridge")

GATEWAYS_DIRNAME = "gateways"
IPC_HOST = "127.0.0.1"
IPC_TIMEOUT_SECONDS = 5.0
MAX_CONTEXT_MESSAGES = 40
MAX_MESSAGE_CHARS = 8_000


class GatewayTransport(Protocol):
    def deliver_cross_gateway_message(self, channel_id: str, text: str, *, source_gateway: str) -> None: ...

    def get_cross_gateway_context(self, channel_id: str) -> dict[str, Any]: ...

    def list_cross_gateway_channels(self) -> list[str]: ...


@dataclass(frozen=True)
class GatewayRegistration:
    gateway_id: str
    pid: int
    host: str
    port: int
    default_channel_id: str
    channels: tuple[str, ...]
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GatewayRegistration:
        channels = data.get("channels", ())
        if isinstance(channels, list):
            channels = tuple(str(channel) for channel in channels)
        return cls(
            gateway_id=str(data["gateway_id"]),
            pid=int(data["pid"]),
            host=str(data.get("host", IPC_HOST)),
            port=int(data["port"]),
            default_channel_id=str(data["default_channel_id"]),
            channels=channels,
            updated_at=str(data.get("updated_at", "")),
        )


@dataclass
class _LocalGateway:
    gateway_id: str
    transport: GatewayTransport
    default_channel_id: str
    channels: list[str] = field(default_factory=list)


class _GatewayRequestHandler(socketserver.StreamRequestHandler):
    bridge: CrossGatewayBridge | None = None

    def handle(self) -> None:
        bridge = type(self).bridge
        if bridge is None:
            return
        for raw_line in self.rfile:
            line = raw_line.decode("utf-8").strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                self._write({"ok": False, "error": "Invalid JSON request."})
                continue
            response = bridge.handle_request(request)
            self._write(response)
            break

    def _write(self, payload: dict[str, Any]) -> None:
        encoded = (json.dumps(payload, ensure_ascii=True) + "\n").encode("utf-8")
        self.wfile.write(encoded)


class _GatewayTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class CrossGatewayBridge:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._local_gateways: dict[str, _LocalGateway] = {}
        self._server: _GatewayTCPServer | None = None
        self._server_thread: threading.Thread | None = None
        self._port: int | None = None
        self._detached = False

    def attach(
        self,
        gateway_id: str,
        transport: GatewayTransport,
        *,
        default_channel_id: str,
        channels: list[str] | None = None,
    ) -> None:
        with self._lock:
            if self._detached:
                return
            channel_list = list(dict.fromkeys([default_channel_id, *(channels or [])]))
            self._local_gateways[gateway_id] = _LocalGateway(
                gateway_id=gateway_id,
                transport=transport,
                default_channel_id=default_channel_id,
                channels=channel_list,
            )
            self._ensure_server()
            self._write_registration(gateway_id)
            atexit.register(self.detach, gateway_id)

    def detach(self, gateway_id: str) -> None:
        with self._lock:
            self._local_gateways.pop(gateway_id, None)
            self._remove_registration(gateway_id)
            if not self._local_gateways:
                self._stop_server()

    def shutdown(self) -> None:
        with self._lock:
            self._detached = True
            gateway_ids = list(self._local_gateways)
            for gateway_id in gateway_ids:
                self.detach(gateway_id)

    def list_gateways(self) -> list[GatewayRegistration]:
        self._cleanup_stale_registrations()
        registrations: list[GatewayRegistration] = []
        for path in self._gateways_dir().glob("*.json"):
            try:
                registration = GatewayRegistration.from_dict(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError):
                continue
            if process_is_running(registration.pid):
                registrations.append(registration)
        return sorted(registrations, key=lambda item: item.gateway_id)

    def get_context(self, gateway_id: str, channel_id: str | None = None) -> dict[str, Any]:
        registration = self._resolve_registration(gateway_id)
        resolved_channel = channel_id or registration.default_channel_id
        if self._is_local(gateway_id):
            return self._local_get_context(gateway_id, resolved_channel)
        response = self._remote_request(
            registration,
            {
                "method": "get_context",
                "params": {"gateway_id": gateway_id, "channel_id": resolved_channel},
            },
        )
        if not response.get("ok"):
            return response
        context = response.get("context")
        return context if isinstance(context, dict) else response

    def send_message(
        self,
        gateway_id: str,
        message: str,
        *,
        channel_id: str | None = None,
        source_gateway: str | None = None,
    ) -> dict[str, Any]:
        text = message.strip()
        if not text:
            return {"ok": False, "error": "Message cannot be empty."}
        if len(text) > MAX_MESSAGE_CHARS:
            return {"ok": False, "error": f"Message exceeds {MAX_MESSAGE_CHARS} characters."}

        registration = self._resolve_registration(gateway_id)
        resolved_channel = channel_id or registration.default_channel_id
        resolved_source = source_gateway or self._default_source_gateway()
        if not resolved_source:
            return {"ok": False, "error": "No source gateway is registered in this process."}

        if self._is_local(gateway_id):
            self._local_send_message(gateway_id, resolved_channel, text, source_gateway=resolved_source)
            return {"ok": True, "gateway_id": gateway_id, "channel_id": resolved_channel}

        return self._remote_request(
            registration,
            {
                "method": "send_message",
                "params": {
                    "gateway_id": gateway_id,
                    "channel_id": resolved_channel,
                    "message": text,
                    "source_gateway": resolved_source,
                },
            },
        )

    def handle_request(self, request: dict[str, Any]) -> dict[str, Any]:
        method = request.get("method")
        params = request.get("params", {})
        if not isinstance(params, dict):
            return {"ok": False, "error": "Request params must be an object."}

        if method == "ping":
            return {"ok": True, "gateways": [gateway.gateway_id for gateway in self.list_gateways()]}

        if method == "list_gateways":
            return {
                "ok": True,
                "gateways": [registration.to_dict() for registration in self.list_gateways()],
            }

        if method == "get_context":
            gateway_id = str(params.get("gateway_id", "")).strip()
            channel_id = params.get("channel_id")
            if not gateway_id:
                return {"ok": False, "error": "gateway_id is required."}
            if not self._is_local(gateway_id):
                return {"ok": False, "error": f"Gateway '{gateway_id}' is not attached to this process."}
            context = self._local_get_context(gateway_id, str(channel_id or ""))
            return {"ok": True, "context": context}

        if method == "send_message":
            gateway_id = str(params.get("gateway_id", "")).strip()
            channel_id = str(params.get("channel_id", "")).strip()
            message = str(params.get("message", "")).strip()
            source_gateway = str(params.get("source_gateway", "unknown")).strip() or "unknown"
            if not gateway_id or not channel_id:
                return {"ok": False, "error": "gateway_id and channel_id are required."}
            if not self._is_local(gateway_id):
                return {"ok": False, "error": f"Gateway '{gateway_id}' is not attached to this process."}
            self._local_send_message(gateway_id, channel_id, message, source_gateway=source_gateway)
            return {"ok": True}

        return {"ok": False, "error": f"Unknown method: {method!r}"}

    def _ensure_server(self) -> None:
        if self._server is not None:
            return
        server = _GatewayTCPServer((IPC_HOST, 0), _GatewayRequestHandler)
        _GatewayRequestHandler.bridge = self
        self._server = server
        self._port = int(server.server_address[1])
        thread = threading.Thread(target=server.serve_forever, name="cross-gateway-ipc", daemon=True)
        thread.start()
        self._server_thread = thread
        LOGGER.info("ipc.started host=%s port=%s", IPC_HOST, self._port)

    def _stop_server(self) -> None:
        server = self._server
        if server is None:
            return
        server.shutdown()
        server.server_close()
        self._server = None
        self._server_thread = None
        self._port = None
        LOGGER.info("ipc.stopped")

    def _write_registration(self, gateway_id: str) -> None:
        local = self._local_gateways.get(gateway_id)
        if local is None or self._port is None:
            return
        registration = GatewayRegistration(
            gateway_id=gateway_id,
            pid=os.getpid(),
            host=IPC_HOST,
            port=self._port,
            default_channel_id=local.default_channel_id,
            channels=tuple(local.channels),
            updated_at=now_iso(),
        )
        path = self._registration_path(gateway_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(registration.to_dict(), indent=2), encoding="utf-8")

    def _remove_registration(self, gateway_id: str) -> None:
        self._registration_path(gateway_id).unlink(missing_ok=True)

    def _registration_path(self, gateway_id: str) -> Path:
        return self._gateways_dir() / f"{gateway_id}.json"

    def _gateways_dir(self) -> Path:
        return settings.data_dir / GATEWAYS_DIRNAME

    def _resolve_registration(self, gateway_id: str) -> GatewayRegistration:
        for registration in self.list_gateways():
            if registration.gateway_id == gateway_id:
                return registration
        raise ValueError(f"Gateway '{gateway_id}' is not running.")

    def _is_local(self, gateway_id: str) -> bool:
        return gateway_id in self._local_gateways

    def _default_source_gateway(self) -> str | None:
        if not self._local_gateways:
            return None
        return next(iter(self._local_gateways))

    def _local_gateway(self, gateway_id: str) -> _LocalGateway:
        local = self._local_gateways.get(gateway_id)
        if local is None:
            raise ValueError(f"Gateway '{gateway_id}' is not attached to this process.")
        return local

    def _local_get_context(self, gateway_id: str, channel_id: str) -> dict[str, Any]:
        local = self._local_gateway(gateway_id)
        resolved_channel = channel_id or local.default_channel_id
        context = local.transport.get_cross_gateway_context(resolved_channel)
        channels = local.transport.list_cross_gateway_channels() or local.channels
        return {
            "gateway_id": gateway_id,
            "channel_id": resolved_channel,
            "channels": channels,
            **context,
        }

    def _local_send_message(
        self,
        gateway_id: str,
        channel_id: str,
        message: str,
        *,
        source_gateway: str,
    ) -> None:
        local = self._local_gateway(gateway_id)
        local.transport.deliver_cross_gateway_message(channel_id, message, source_gateway=source_gateway)

    def _remote_request(self, registration: GatewayRegistration, request: dict[str, Any]) -> dict[str, Any]:
        payload = json.dumps(request, ensure_ascii=True) + "\n"
        try:
            with socket.create_connection((registration.host, registration.port), timeout=IPC_TIMEOUT_SECONDS) as conn:
                conn.sendall(payload.encode("utf-8"))
                buffer = b""
                while b"\n" not in buffer:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    buffer += chunk
                line = buffer.split(b"\n", 1)[0].decode("utf-8").strip()
                if not line:
                    return {"ok": False, "error": f"Gateway '{registration.gateway_id}' returned an empty response."}
                response = json.loads(line)
        except OSError as exc:
            return {"ok": False, "error": f"Failed to reach gateway '{registration.gateway_id}': {exc}"}
        except json.JSONDecodeError:
            return {"ok": False, "error": f"Gateway '{registration.gateway_id}' returned invalid JSON."}
        if not isinstance(response, dict):
            return {"ok": False, "error": f"Gateway '{registration.gateway_id}' returned an unexpected payload."}
        return response

    def _cleanup_stale_registrations(self) -> None:
        for path in self._gateways_dir().glob("*.json"):
            try:
                registration = GatewayRegistration.from_dict(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError):
                path.unlink(missing_ok=True)
                continue
            if not process_is_running(registration.pid):
                path.unlink(missing_ok=True)


_bridge: CrossGatewayBridge | None = None
_bridge_lock = threading.Lock()


def get_cross_gateway_bridge() -> CrossGatewayBridge:
    global _bridge
    with _bridge_lock:
        if _bridge is None:
            _bridge = CrossGatewayBridge()
        return _bridge


def process_is_running(pid: int) -> bool:
    if os.name == "nt":
        import ctypes

        process_query_limited_information = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def format_gateway_list(gateways: list[GatewayRegistration]) -> str:
    if not gateways:
        return "No gateways are currently running."
    lines = ["Running gateways:"]
    for gateway in gateways:
        channel_summary = ", ".join(gateway.channels) if gateway.channels else gateway.default_channel_id
        lines.append(
            f"- {gateway.gateway_id}: default_channel={gateway.default_channel_id}; channels=[{channel_summary}]"
        )
    return "\n".join(lines)


def format_gateway_context(context: dict[str, Any]) -> str:
    session = context.get("session")
    messages = context.get("messages", [])
    lines = [
        f"Gateway: {context.get('gateway_id')}",
        f"Channel: {context.get('channel_id')}",
    ]
    if isinstance(session, dict):
        lines.append(f"Session: {session.get('name')} ({session.get('id')})")
    if not isinstance(messages, list) or not messages:
        lines.append("Messages: <empty>")
        return "\n".join(lines)

    lines.append("Recent messages:")
    for message in messages[-MAX_CONTEXT_MESSAGES:]:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role", "unknown"))
        content = message.get("content", "")
        if isinstance(content, list):
            text_parts = [
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str)
            ]
            rendered = " ".join(text_parts).strip() or "<multimodal message>"
        else:
            rendered = str(content).strip()
        if len(rendered) > 500:
            rendered = f"{rendered[:500]}..."
        lines.append(f"- {role}: {rendered or '<empty>'}")
    return "\n".join(lines)


CrossGatewayAction = Literal["list", "get_context", "send_message"]


def run_cross_gateway_action(
    action: CrossGatewayAction,
    *,
    gateway_id: str = "",
    channel_id: str = "",
    message: str = "",
    source_gateway: str | None = None,
) -> str:
    bridge = get_cross_gateway_bridge()
    if action == "list":
        return format_gateway_list(bridge.list_gateways())

    if action == "get_context":
        if not gateway_id.strip():
            return "Error: gateway_id is required for get_context."
        try:
            context = bridge.get_context(gateway_id.strip(), channel_id.strip() or None)
        except ValueError as exc:
            return f"Error: {exc}"
        if context.get("ok") is False:
            return f"Error: {context.get('error', 'Failed to fetch context.')}"
        if "context" in context:
            return format_gateway_context(context["context"])
        return format_gateway_context(context)

    if action == "send_message":
        if not gateway_id.strip():
            return "Error: gateway_id is required for send_message."
        if not message.strip():
            return "Error: message is required for send_message."
        result = bridge.send_message(
            gateway_id.strip(),
            message,
            channel_id=channel_id.strip() or None,
            source_gateway=source_gateway,
        )
        if not result.get("ok"):
            return f"Error: {result.get('error', 'Failed to send message.')}"
        target_channel = result.get("channel_id", channel_id or "default")
        return f"Message delivered to gateway '{gateway_id}' on channel '{target_channel}'."

    return f"Error: Unknown action '{action}'."


def make_cross_gateway_tool():
    from langchain_core.tools import tool

    @tool("cross_gateway")
    def cross_gateway(
        action: CrossGatewayAction,
        gateway_id: str = "",
        channel_id: str = "",
        message: str = "",
    ) -> str:
        """Interact with other running Tomo gateways for cross-channel context and messaging.

        Use action=list to discover running gateways and their channel ids.
        Use action=get_context with gateway_id (and optional channel_id) to read recent messages from another gateway.
        Use action=send_message with gateway_id, message, and optional channel_id to deliver a message to another gateway.
        """
        return run_cross_gateway_action(
            action,
            gateway_id=gateway_id,
            channel_id=channel_id,
            message=message,
        )

    return cross_gateway