"""
AIOps Agent Platform - WebSocket Endpoints

WebSocket 端点，用于实时推送故障处理进度和 Agent 状态更新。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.config import get_config
from app.utils.logging import get_logger

logger = get_logger(__name__)

websocket_router = APIRouter()

# 连接管理器
class ConnectionManager:
    """
    WebSocket 连接管理器

    管理所有活跃的 WebSocket 连接，支持按房间分组广播。
    """

    def __init__(self) -> None:
        # 所有活跃连接
        self._connections: list[WebSocket] = []
        # 按 incident_id 分组的连接
        self._incident_rooms: dict[str, list[WebSocket]] = {}
        # 按 agent_id 分组的连接
        self._agent_rooms: dict[str, list[WebSocket]] = {}

    @property
    def active_connections(self) -> int:
        """当前活跃连接数"""
        return len(self._connections)

    async def connect(self, websocket: WebSocket) -> None:
        """
        接受并注册新连接

        Args:
            websocket: WebSocket 连接
        """
        await websocket.accept()
        self._connections.append(websocket)
        logger.info(
            "WebSocket connected",
            client=websocket.client.host if websocket.client else "unknown",
            total_connections=self.active_connections,
        )

    def disconnect(self, websocket: WebSocket) -> None:
        """
        断开并移除连接

        Args:
            websocket: WebSocket 连接
        """
        if websocket in self._connections:
            self._connections.remove(websocket)

        # 从所有房间中移除
        for room in self._incident_rooms.values():
            if websocket in room:
                room.remove(websocket)
        for room in self._agent_rooms.values():
            if websocket in room:
                room.remove(websocket)

        logger.info(
            "WebSocket disconnected",
            total_connections=self.active_connections,
        )

    def join_incident_room(self, websocket: WebSocket, incident_id: str) -> None:
        """
        加入故障房间

        Args:
            websocket: WebSocket 连接
            incident_id: 故障ID
        """
        if incident_id not in self._incident_rooms:
            self._incident_rooms[incident_id] = []
        if websocket not in self._incident_rooms[incident_id]:
            self._incident_rooms[incident_id].append(websocket)
            logger.debug("Joined incident room", incident_id=incident_id)

    def leave_incident_room(self, websocket: WebSocket, incident_id: str) -> None:
        """离开故障房间"""
        if incident_id in self._incident_rooms:
            room = self._incident_rooms[incident_id]
            if websocket in room:
                room.remove(websocket)

    async def broadcast(self, message: dict[str, Any]) -> None:
        """
        广播消息给所有连接

        Args:
            message: 消息字典
        """
        text = json.dumps(message, default=str)
        dead_connections: list[WebSocket] = []

        for connection in self._connections:
            try:
                await connection.send_text(text)
            except Exception:
                dead_connections.append(connection)

        # 清理失效连接
        for conn in dead_connections:
            self.disconnect(conn)

    async def broadcast_to_incident(
        self,
        incident_id: str,
        message: dict[str, Any],
    ) -> None:
        """
        向指定故障房间广播

        Args:
            incident_id: 故障ID
            message: 消息字典
        """
        room = self._incident_rooms.get(incident_id, [])
        if not room:
            return

        text = json.dumps(message, default=str)
        dead_connections: list[WebSocket] = []

        for connection in room:
            try:
                await connection.send_text(text)
            except Exception:
                dead_connections.append(connection)

        for conn in dead_connections:
            self.disconnect(conn)

    async def send_to(self, websocket: WebSocket, message: dict[str, Any]) -> None:
        """
        发送消息给指定连接

        Args:
            websocket: 目标 WebSocket
            message: 消息字典
        """
        text = json.dumps(message, default=str)
        await websocket.send_text(text)


# 全局连接管理器实例
manager = ConnectionManager()


@websocket_router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """
    通用 WebSocket 端点

    客户端连接后可订阅不同频道（故障更新、Agent 状态等）。
    """
    await manager.connect(websocket)
    config = get_config()
    heartbeat_interval = config.websocket.heartbeat_interval_seconds

    try:
        while True:
            # 设置超时接收消息
            try:
                data = await asyncio.wait_for(
                    websocket.receive_text(),
                    timeout=heartbeat_interval,
                )

                # 处理客户端消息
                try:
                    message = json.loads(data)
                    await _handle_client_message(websocket, message)
                except json.JSONDecodeError:
                    await manager.send_to(websocket, {
                        "type": "error",
                        "message": "Invalid JSON format",
                    })

            except asyncio.TimeoutError:
                # 发送心跳
                await manager.send_to(websocket, {
                    "type": "heartbeat",
                    "timestamp": json.dumps(None),
                })

    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error("WebSocket error", error=str(e))
        manager.disconnect(websocket)


@websocket_router.websocket("/ws/incidents/{incident_id}")
async def incident_websocket(
    websocket: WebSocket,
    incident_id: str,
) -> None:
    """
    故障专用 WebSocket 端点

    自动订阅指定故障的实时更新。
    """
    await manager.connect(websocket)
    manager.join_incident_room(websocket, incident_id)

    # 发送确认消息
    await manager.send_to(websocket, {
        "type": "subscribed",
        "channel": f"incident:{incident_id}",
    })

    try:
        while True:
            data = await websocket.receive_text()
            logger.debug("Received message from incident WS", incident_id=incident_id, data=data)

    except WebSocketDisconnect:
        manager.leave_incident_room(websocket, incident_id)
        manager.disconnect(websocket)
    except Exception as e:
        logger.error("Incident WebSocket error", incident_id=incident_id, error=str(e))
        manager.leave_incident_room(websocket, incident_id)
        manager.disconnect(websocket)


async def _handle_client_message(
    websocket: WebSocket,
    message: dict[str, Any],
) -> None:
    """
    处理客户端发送的消息

    Args:
        websocket: WebSocket 连接
        message: 消息字典
    """
    msg_type = message.get("type", "")

    if msg_type == "subscribe":
        channel = message.get("channel", "")
        if channel.startswith("incident:"):
            incident_id = channel.split(":", 1)[1]
            manager.join_incident_room(websocket, incident_id)
            await manager.send_to(websocket, {
                "type": "subscribed",
                "channel": channel,
            })

    elif msg_type == "unsubscribe":
        channel = message.get("channel", "")
        if channel.startswith("incident:"):
            incident_id = channel.split(":", 1)[1]
            manager.leave_incident_room(websocket, incident_id)

    elif msg_type == "ping":
        await manager.send_to(websocket, {"type": "pong"})

    else:
        await manager.send_to(websocket, {
            "type": "error",
            "message": f"Unknown message type: {msg_type}",
        })


# 导出广播函数供其他模块使用
__all__ = ["manager", "websocket_endpoint", "incident_websocket"]
