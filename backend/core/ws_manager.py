"""
core/ws_manager.py — WebSocket 연결 관리자 + 대시보드 브로드캐스트
"""
import asyncio
import os

from fastapi import WebSocket
from core.state import bot_global_state


class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast_json(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                pass


manager = ConnectionManager()


async def broadcast_dashboard_state():
    """1초마다 전역 상태를 연결된 모든 웹소켓 클라이언트에게 브로드캐스트"""
    from logger import get_logger
    _logger = get_logger(__name__)
    while True:
        try:
            if manager.active_connections:
                state_to_send = {
                    "is_running": bot_global_state["is_running"],
                    "balance": bot_global_state["balance"],
                    "symbols": bot_global_state["symbols"],
                    "logs": list(bot_global_state["logs"][-10:]),
                    "is_demo": os.getenv("OKX_DEMO", "false").strip().lower() in ("true", "1", "yes"),
                }
                await manager.broadcast_json(state_to_send)
        except Exception as e:
            _logger.error(f"WebSocket Broadcast 오류: {e}")
        await asyncio.sleep(1)
