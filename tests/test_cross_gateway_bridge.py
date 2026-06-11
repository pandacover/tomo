from __future__ import annotations

from dataclasses import asdict

from tomo.cross_gateway_bridge import (
    CrossGatewayBridge,
    GatewayRegistration,
    get_cross_gateway_bridge,
    run_cross_gateway_action,
)
from tomo.session_store import create_session, save_session


class RecordingTransport:
    def __init__(self, gateway_id: str, channel_id: str) -> None:
        self.gateway_id = gateway_id
        self.channel_id = channel_id
        self.delivered: list[tuple[str, str, str]] = []
        self.sessions: dict[str, object] = {}

    def seed_message(self, text: str) -> None:
        session = create_session(f"Gateway {self.channel_id}")
        session.messages.extend(
            [
                {"role": "user", "content": text},
                {"role": "assistant", "content": "ack"},
            ]
        )
        save_session(session)
        self.sessions[self.channel_id] = session

    def deliver_cross_gateway_message(self, channel_id: str, text: str, *, source_gateway: str) -> None:
        self.delivered.append((channel_id, text, source_gateway))

    def get_cross_gateway_context(self, channel_id: str) -> dict[str, object]:
        session = self.sessions.get(channel_id)
        if session is None:
            session = create_session(f"Gateway {channel_id}")
            save_session(session)
            self.sessions[channel_id] = session
        return {
            "session": asdict(session.metadata),
            "messages": session.messages,
        }

    def list_cross_gateway_channels(self) -> list[str]:
        return [self.channel_id]


def test_cross_gateway_bridge_lists_local_registration(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    bridge = CrossGatewayBridge()
    transport = RecordingTransport("desktop", "desktop:local")

    bridge.attach("desktop", transport, default_channel_id="desktop:local", channels=["desktop:local"])

    gateways = bridge.list_gateways()
    assert len(gateways) == 1
    assert gateways[0].gateway_id == "desktop"
    assert gateways[0].default_channel_id == "desktop:local"
    bridge.shutdown()


def test_cross_gateway_bridge_delivers_message_locally(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    bridge = CrossGatewayBridge()
    desktop = RecordingTransport("desktop", "desktop:local")
    telegram = RecordingTransport("telegram", "12345")

    bridge.attach("desktop", desktop, default_channel_id="desktop:local")
    bridge.attach("telegram", telegram, default_channel_id="12345", channels=["12345"])

    result = bridge.send_message("telegram", "hello from desktop", source_gateway="desktop")

    assert result["ok"] is True
    assert telegram.delivered == [("12345", "hello from desktop", "desktop")]
    bridge.shutdown()


def test_cross_gateway_bridge_returns_context(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    bridge = CrossGatewayBridge()
    transport = RecordingTransport("desktop", "desktop:local")
    transport.seed_message("seed message")
    bridge.attach("desktop", transport, default_channel_id="desktop:local")

    context = bridge.get_context("desktop")

    assert context["gateway_id"] == "desktop"
    assert context["channel_id"] == "desktop:local"
    assert len(context["messages"]) == 2
    bridge.shutdown()


def test_cross_gateway_bridge_routes_over_ipc(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sender = CrossGatewayBridge()
    receiver = CrossGatewayBridge()
    target = RecordingTransport("telegram", "999")
    receiver.attach("telegram", target, default_channel_id="999", channels=["999"])

    registration = receiver.list_gateways()[0]
    remote_bridge = CrossGatewayBridge()
    result = remote_bridge._remote_request(
        registration,
        {
            "method": "send_message",
            "params": {
                "gateway_id": "telegram",
                "channel_id": "999",
                "message": "relay this",
                "source_gateway": "desktop",
            },
        },
    )

    assert result["ok"] is True
    assert target.delivered == [("999", "relay this", "desktop")]
    sender.shutdown()
    receiver.shutdown()
    remote_bridge.shutdown()


def test_run_cross_gateway_action_list_and_send(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    bridge = get_cross_gateway_bridge()
    bridge.shutdown()
    bridge = CrossGatewayBridge()
    monkeypatch.setattr("tomo.cross_gateway_bridge.get_cross_gateway_bridge", lambda: bridge)

    desktop = RecordingTransport("desktop", "desktop:local")
    telegram = RecordingTransport("telegram", "222")
    bridge.attach("desktop", desktop, default_channel_id="desktop:local")
    bridge.attach("telegram", telegram, default_channel_id="222", channels=["222"])

    listing = run_cross_gateway_action("list")
    assert "desktop" in listing
    assert "telegram" in listing

    send_result = run_cross_gateway_action(
        "send_message",
        gateway_id="telegram",
        channel_id="222",
        message="ping",
    )
    assert "delivered" in send_result
    assert telegram.delivered[-1] == ("222", "ping", "desktop")

    context_result = run_cross_gateway_action("get_context", gateway_id="desktop")
    assert "Gateway: desktop" in context_result
    bridge.shutdown()


def test_gateway_registration_round_trip():
    payload = {
        "gateway_id": "telegram",
        "pid": 42,
        "host": "127.0.0.1",
        "port": 51515,
        "default_channel_id": "123",
        "channels": ["123", "456"],
        "updated_at": "2026-01-01T00:00:00Z",
    }
    registration = GatewayRegistration.from_dict(payload)
    assert registration.gateway_id == payload["gateway_id"]
    assert registration.channels == tuple(payload["channels"])