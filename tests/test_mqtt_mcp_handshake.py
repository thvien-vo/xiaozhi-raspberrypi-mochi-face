"""Regression tests for MCP negotiation over MQTT connections."""

import asyncio
import json

import pytest

from src.core.event_bus import EventBus, Events
from src.core.protocol_manager import ProtocolTransport
from src.core.task_manager import TaskManager
from src.mcp.mcp_server import McpServer
from src.mcp.tooling import McpTool, PropertyList


class _PublishedMessage:
    def __init__(self, rc, *, published: bool = True):
        self.rc = rc
        self._published = published
        self.wait_called = False

    def wait_for_publish(self, timeout=None):
        self.wait_called = True

    def is_published(self):
        return self._published


class _FakeMqttClient:
    def __init__(self, result):
        self._result = result
        self.published: list[tuple[str, str, int]] = []

    def publish(self, topic, payload, qos=0):
        self.published.append((topic, payload, qos))
        return self._result

    def loop_stop(self):
        pass

    def disconnect(self):
        pass


class _ConnectedMqttClient:
    def __init__(self, protocol, **kwargs):
        self.protocol = protocol
        self.kwargs = kwargs
        self.loop_stop_called = False
        self.disconnect_called = False
        self.published: list[tuple[str, str, int]] = []
        self.on_connect = None
        self.on_disconnect = None

    def username_pw_set(self, username, password):
        pass

    def tls_set(self, **kwargs):
        pass

    def connect_async(self, host, port, keepalive):
        pass

    def loop_start(self):
        self.on_connect(self, None, None, 0)
        # Simulate duplicate late callback from an old client or background thread.
        self.on_connect(self, None, None, 0)

    def subscribe(self, topic, qos):
        pass

    def publish(self, topic, payload, qos=0):
        self.published.append((topic, payload, qos))
        if json.loads(payload).get("type") == "hello":
            self.protocol._handle_mqtt_message(
                json.dumps(
                    {
                        "type": "hello",
                        "transport": "udp",
                        "session_id": "test-session",
                        "udp": {
                            "server": "127.0.0.1",
                            "port": 9000,
                            "key": "key",
                            "nonce": "nonce",
                        },
                    }
                )
            )
        return _PublishedMessage(0)

    def loop_stop(self):
        self.loop_stop_called = True

    def disconnect(self):
        self.disconnect_called = True

    def is_connected(self):
        return not self.disconnect_called


class _FakeUdp:
    def __init__(self):
        self.server = "127.0.0.1"
        self.port = 9000
        self.start_count = 0
        self.reset_count = 0
        self._ready = False

    def configure(self, **kwargs):
        self.server = kwargs["server"]
        self.port = kwargs["port"]

    def start(self):
        self.start_count += 1
        self._ready = True

    def stop(self):
        self._ready = False

    def reset_session(self):
        self.reset_count += 1
        self._ready = False

    def is_ready(self):
        return self._ready


async def _tool(_arguments):
    return '{"ok": true}'


@pytest.mark.asyncio
async def test_mqtt_mcp_tools_list_before_hello_reaches_server(
    monkeypatch,
):
    """Gateway can fetch the tool list after MQTT CONNACK and before client hello."""
    from src.protocols import mqtt_protocol as mqtt_module

    monkeypatch.setattr(mqtt_module, "get_config", lambda: object())

    task_manager = TaskManager()
    task_manager.initialize()
    transport = ProtocolTransport(EventBus(), task_manager=task_manager)
    protocol = mqtt_module.MqttProtocol(asyncio.get_running_loop())
    result = _PublishedMessage(mqtt_module.mqtt.MQTT_ERR_SUCCESS)
    protocol.mqtt_client = _FakeMqttClient(result)
    protocol.publish_topic = "device-server"
    transport._protocol = protocol
    transport._setup_callbacks()

    server = McpServer()
    server.add_tool(McpTool("test.mqtt", "test", PropertyList(), _tool))
    response_sent = asyncio.Event()

    async def handle_incoming(message):
        if message.get("type") == "mcp":
            await server.parse_message(message["payload"])

    async def send_response(payload):
        delivered = await transport.protocol.send_mcp_message(payload)
        response_sent.set()
        return delivered

    transport._event_bus.on(Events.INCOMING_JSON, handle_incoming)
    server.set_send_callback(send_response)

    try:
        protocol._handle_mqtt_message(
            json.dumps(
                {
                    "type": "mcp",
                    "payload": {
                        "jsonrpc": "2.0",
                        "id": 10001,
                        "method": "tools/list",
                        "params": {},
                    },
                }
            )
        )
        await asyncio.wait_for(response_sent.wait(), timeout=1.0)

        assert result.wait_called
        assert protocol.session_id == ""
        assert len(protocol.mqtt_client.published) == 1
        topic, payload, qos = protocol.mqtt_client.published[0]
        assert topic == "device-server"
        assert qos == 0
        message = json.loads(payload)
        assert message["type"] == "mcp"
        assert message["session_id"] == ""
        assert message["payload"]["id"] == 10001
        assert [tool["name"] for tool in message["payload"]["result"]["tools"]] == [
            "test.mqtt"
        ]
    finally:
        await task_manager.cancel_all()


@pytest.mark.asyncio
async def test_mqtt_send_text_rejects_publish_enqueue_failure(monkeypatch):
    from src.protocols import mqtt_protocol as mqtt_module

    monkeypatch.setattr(mqtt_module, "get_config", lambda: object())

    protocol = mqtt_module.MqttProtocol(asyncio.get_running_loop())
    result = _PublishedMessage(mqtt_module.mqtt.MQTT_ERR_NO_CONN)
    protocol.mqtt_client = _FakeMqttClient(result)
    protocol.publish_topic = "device-server"

    assert await protocol.send_text("{}") is False
    assert result.wait_called is False


@pytest.mark.asyncio
async def test_mqtt_connection_uses_controlled_reconnect_and_disconnects_cleanly(
    monkeypatch,
):
    """MQTT disconnects are handled by Protocol; Paho must not auto-reconnect in the background."""
    from src.protocols import mqtt_protocol as mqtt_module

    mqtt_info = {
        "endpoint": "mqtt.example.test:1883",
        "client_id": "test-client",
        "username": "test-user",
        "password": "test-password",
        "publish_topic": "device-server",
        "subscribe_topic": "server-device",
    }

    class _Config:
        def get_config(self, key):
            assert key == "SYSTEM_OPTIONS.NETWORK.MQTT_INFO"
            return mqtt_info

    monkeypatch.setattr(mqtt_module, "get_config", lambda: _Config())

    protocol = mqtt_module.MqttProtocol(asyncio.get_running_loop())
    protocol._udp = _FakeUdp()
    clients = []

    def create_client(*args, **kwargs):
        client = _ConnectedMqttClient(protocol, **kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(mqtt_module.mqtt, "Client", create_client)

    closed = asyncio.Event()
    errors = []

    async def on_closed():
        closed.set()

    async def on_error(reason):
        errors.append(reason)

    protocol.on_audio_channel_closed(on_closed)
    protocol.on_network_error(on_error)

    assert await protocol.connect() is True
    assert clients[0].kwargs["reconnect_on_failure"] is False

    # Duplicate success callbacks settle the connect() Future only once without raising InvalidStateError.
    clients[0].on_disconnect(clients[0], None, 7)
    await asyncio.wait_for(closed.wait(), timeout=1.0)

    assert protocol.connected is False
    assert clients[0].loop_stop_called
    assert errors == ["Connection lost: MQTT disconnected: 7"]


@pytest.mark.asyncio
async def test_mqtt_closes_only_udp_session_and_reuses_broker(monkeypatch):
    """Normal goodbye releases UDP, and the next hello must reuse the connected broker."""
    from src.protocols import mqtt_protocol as mqtt_module

    mqtt_info = {
        "endpoint": "mqtt.example.test:1883",
        "client_id": "test-client",
        "username": "test-user",
        "password": "test-password",
        "publish_topic": "device-server",
        "subscribe_topic": "server-device",
    }

    class _Config:
        def get_config(self, key):
            assert key == "SYSTEM_OPTIONS.NETWORK.MQTT_INFO"
            return mqtt_info

    monkeypatch.setattr(mqtt_module, "get_config", lambda: _Config())

    protocol = mqtt_module.MqttProtocol(asyncio.get_running_loop())
    udp = _FakeUdp()
    protocol._udp = udp
    clients = []

    def create_client(*args, **kwargs):
        client = _ConnectedMqttClient(protocol, **kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(mqtt_module.mqtt, "Client", create_client)

    closed = asyncio.Event()

    async def on_closed():
        closed.set()

    protocol.on_audio_channel_closed(on_closed)

    assert await protocol.connect() is True
    client = clients[0]
    assert protocol.is_audio_channel_opened()
    assert udp.start_count == 1

    await protocol.close_audio_channel()

    assert await asyncio.wait_for(closed.wait(), timeout=1.0) is True
    assert protocol.mqtt_client is client
    assert client.loop_stop_called is False
    assert client.disconnect_called is False
    assert protocol.is_audio_channel_opened() is False
    assert json.loads(client.published[-1][1]) == {
        "type": "goodbye",
        "session_id": "test-session",
    }

    assert await protocol.open_audio_channel() is True
    assert len(clients) == 1
    assert udp.start_count == 2
    assert protocol.is_audio_channel_opened()

    await protocol.shutdown()
    assert client.loop_stop_called
    assert client.disconnect_called
    assert protocol.mqtt_client is None


@pytest.mark.asyncio
async def test_mqtt_start_opens_only_control_connection_until_audio_is_requested(
    monkeypatch,
):
    """On startup, MQTT can receive notify/MCP but must not send hello or open UDP early."""
    from src.protocols import mqtt_protocol as mqtt_module

    mqtt_info = {
        "endpoint": "mqtt.example.test:1883",
        "client_id": "test-client",
        "username": "test-user",
        "password": "test-password",
        "publish_topic": "device-server",
        "subscribe_topic": "server-device",
    }

    class _Config:
        def get_config(self, key):
            assert key == "SYSTEM_OPTIONS.NETWORK.MQTT_INFO"
            return mqtt_info

    monkeypatch.setattr(mqtt_module, "get_config", lambda: _Config())
    protocol = mqtt_module.MqttProtocol(asyncio.get_running_loop())
    udp = _FakeUdp()
    protocol._udp = udp
    clients = []

    def create_client(*args, **kwargs):
        client = _ConnectedMqttClient(protocol, **kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(mqtt_module.mqtt, "Client", create_client)

    assert await protocol.start() is True
    assert len(clients) == 1
    assert clients[0].published == []
    assert udp.start_count == 0
    assert protocol.is_audio_channel_opened() is False

    assert await protocol.open_audio_channel() is True
    assert json.loads(clients[0].published[0][1])["type"] == "hello"
    assert udp.start_count == 1


@pytest.mark.asyncio
async def test_mqtt_control_reconnect_waits_for_idle_and_never_opens_udp(
    monkeypatch,
):
    from src.protocols import mqtt_protocol as mqtt_module

    monkeypatch.setattr(mqtt_module, "get_config", lambda: object())
    monkeypatch.setattr(mqtt_module, "_CONTROL_RECONNECT_DELAY", 0)

    protocol = mqtt_module.MqttProtocol(asyncio.get_running_loop())
    idle = {"value": False}
    protocol.set_idle_checker(lambda: idle["value"])
    attempts = []
    connected = asyncio.Event()

    async def connect_control():
        attempts.append("control")
        connected.set()
        return True

    protocol._connect_control_channel = connect_control
    protocol._schedule_control_reconnect()
    await asyncio.sleep(0)
    assert attempts == []

    idle["value"] = True
    await asyncio.wait_for(connected.wait(), timeout=1)
    assert attempts == ["control"]
    assert protocol.udp_running is False
