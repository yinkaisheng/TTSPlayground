# -- coding: utf-8 --
"""合成/WebSocket 等日志行带时间戳前缀（多 Tab 共用）。"""

from __future__ import annotations

from datetime import datetime


def log_timestamp_ms() -> str:
    """当前时间，精确到毫秒。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")


def format_log_line(message: str) -> str:
    return f"[{log_timestamp_ms()}] {message}"
