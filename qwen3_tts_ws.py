# -- coding: utf-8 --
"""Qwen3 TTS Realtime WebSocket 客户端（逻辑对齐 ``qwen3_tts_api_cli.py``，供 GUI 调用）。"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from collections.abc import Callable
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional

from gui.constants import TTS_WEBSOCKET_OPEN_TIMEOUT_SEC, TTS_WEBSOCKET_RECV_TIMEOUT_SEC


class SessionMode(Enum):
    SERVER_COMMIT = "server_commit"
    COMMIT = "commit"


class TTSRealtimeClient:
    """与 TTS Realtime API 交互的客户端（参见官方 Realtime WebSocket 协议）。"""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        voice: str = "Elias",
        mode: SessionMode = SessionMode.COMMIT,
        audio_callback: Optional[Callable[[bytes], None]] = None,
        language_type: str = "Chinese",
        sample_rate: int = 24000,
        response_format: str = "wav",
        log: Optional[Callable[[str], None]] = None,
        instructions: str | None = None,
    ) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.voice = voice
        self.mode = mode
        self.ws = None
        self.audio_callback = audio_callback
        self.language_type = language_type
        self.sample_rate = sample_rate
        self.response_format = str(response_format or "wav").strip().lower()
        self.instructions = (instructions or "").strip() or None
        self._log = log or (lambda _m: None)

        self._session_created = False
        self._session_updated = False
        self._current_response_id = None
        self._current_item_id = None
        self._is_responding = False
        self._response_future: asyncio.Future | None = None
        self._recv_task: asyncio.Task | None = None

    async def _raise_if_recv_failed(self) -> None:
        """若消息接收任务已异常结束，抛出同一异常（避免主协程永久卡在等待事件）。"""
        t = self._recv_task
        if t is None or not t.done():
            return
        exc = t.exception()
        if exc is not None:
            raise exc

    def _fail_pending_response(self, exc: BaseException) -> None:
        """接收循环报错或断开时，解除 ``wait_for_response_done`` 对 Future 的挂起。"""
        fut = self._response_future
        if fut is not None and not fut.done():
            fut.set_exception(exc)

    async def connect(self) -> None:
        import websockets

        headers = {"Authorization": f"Bearer {self.api_key}"}
        ot = float(TTS_WEBSOCKET_OPEN_TIMEOUT_SEC)
        self._log(f"连接 WebSocket（握手超时 {ot:g}s）: {self.base_url[:80]}…")

        self.ws = await websockets.connect(
            self.base_url,
            additional_headers=headers,
            open_timeout=ot,
            close_timeout=10,
        )

        session_config: dict[str, Any] = {
            "mode": self.mode.value,
            "voice": self.voice,
            "language_type": self.language_type,
            "response_format": self.response_format,
            "sample_rate": int(self.sample_rate),
        }
        if self.instructions:
            session_config["instructions"] = self.instructions
            session_config["optimize_instructions"] = True

        await self.update_session(session_config)

    async def send_event(self, event: Dict[str, Any]) -> None:
        import websockets

        event["event_id"] = "event_" + str(int(time.time() * 1000))
        self._log(f"发送事件: type={event.get('type')}")
        await self.ws.send(json.dumps(event))

    async def update_session(self, config: Dict[str, Any]) -> None:
        event = {"type": "session.update", "session": config}
        await self.send_event(event)

    async def append_text(self, text: str) -> None:
        event = {"type": "input_text_buffer.append", "text": text}
        await self.send_event(event)

    async def commit_text_buffer(self) -> None:
        event = {"type": "input_text_buffer.commit"}
        await self.send_event(event)

    async def clear_text_buffer(self) -> None:
        event = {"type": "input_text_buffer.clear"}
        await self.send_event(event)

    async def finish_session(self) -> None:
        event = {"type": "session.finish"}
        await self.send_event(event)

    async def wait_for_session_created(self, timeout: Optional[float] = None) -> None:
        deadline = time.perf_counter() + float(
            timeout if timeout is not None else TTS_WEBSOCKET_OPEN_TIMEOUT_SEC
        )
        while not self._session_created:
            await self._raise_if_recv_failed()
            if time.perf_counter() >= deadline:
                raise TimeoutError(
                    f"等待 session.created 超时（{TTS_WEBSOCKET_OPEN_TIMEOUT_SEC:g}s）"
                )
            await asyncio.sleep(0.05)

    async def wait_for_session_updated(self, timeout: Optional[float] = None) -> None:
        deadline = time.perf_counter() + float(
            timeout if timeout is not None else TTS_WEBSOCKET_OPEN_TIMEOUT_SEC
        )
        while not self._session_updated:
            await self._raise_if_recv_failed()
            if time.perf_counter() >= deadline:
                raise TimeoutError(
                    f"等待 session.updated 超时（{TTS_WEBSOCKET_OPEN_TIMEOUT_SEC:g}s）"
                )
            await asyncio.sleep(0.05)

    async def wait_for_response_done(self, timeout: Optional[float] = None) -> None:
        """等待本轮 response 结束。

        ``timeout`` 为 ``None`` 时不设整段上限（长文本可合成数分钟音频）；仍受
        ``handle_messages`` 中单次 ``recv`` 空闲超时（见 ``TTS_WEBSOCKET_RECV_TIMEOUT_SEC``）约束。
        """
        if timeout is None:
            while self._response_future is None:
                await self._raise_if_recv_failed()
                await asyncio.sleep(0.05)
            await self._response_future
            return
        deadline = time.perf_counter() + float(timeout)
        while self._response_future is None:
            await self._raise_if_recv_failed()
            if time.perf_counter() >= deadline:
                raise TimeoutError("等待 response.created 超时")
            await asyncio.sleep(0.05)
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            raise TimeoutError("等待 response.done 超时")
        await asyncio.wait_for(self._response_future, timeout=remaining)

    async def handle_messages(self) -> None:
        import websockets

        recv_timeout = float(TTS_WEBSOCKET_RECV_TIMEOUT_SEC)
        try:
            while True:
                try:
                    message = await asyncio.wait_for(self.ws.recv(), timeout=recv_timeout)
                except asyncio.TimeoutError:
                    exc = TimeoutError(f"接收 WebSocket 消息超时（{recv_timeout:g}s）")
                    self._log(str(exc))
                    self._fail_pending_response(exc)
                    raise exc from None

                event = json.loads(message)
                event_type = event.get("type")

                if event_type != "response.audio.delta":
                    self._log(f"收到事件: {event_type}")

                if event_type == "error":
                    err = event.get("error", {})
                    self._log(f"服务端错误: {err}")
                    continue
                elif event_type == "session.created":
                    self._session_created = True
                elif event_type == "session.updated":
                    self._session_updated = True
                elif event_type == "input_text_buffer.committed":
                    pass
                elif event_type == "input_text_buffer.cleared":
                    pass
                elif event_type == "response.created":
                    self._current_response_id = event.get("response", {}).get("id")
                    self._is_responding = True
                    self._response_future = asyncio.Future()
                elif event_type == "response.output_item.added":
                    self._current_item_id = event.get("item", {}).get("id")
                elif event_type == "response.audio.delta" and self.audio_callback:
                    audio_bytes = base64.b64decode(event.get("delta", ""))
                    self.audio_callback(audio_bytes)
                elif event_type == "response.audio.done":
                    self._log("音频增量接收完成")
                elif event_type == "response.done":
                    self._is_responding = False
                    self._current_response_id = None
                    self._current_item_id = None
                    if self._response_future and not self._response_future.done():
                        self._response_future.set_result(True)
                elif event_type == "session.finished":
                    self._log("会话已结束")

        except TimeoutError:
            raise
        except websockets.exceptions.ConnectionClosed as e:
            self._log("WebSocket 连接已关闭")
            self._fail_pending_response(e)
            raise
        except Exception as e:
            self._log(f"消息处理异常: {e}")
            self._fail_pending_response(e)
            raise

    async def close(self) -> None:
        if self.ws is None:
            return
        self._log("正在关闭 WebSocket…")
        try:
            await self.ws.close()
        except Exception as e:
            self._log(f"关闭 WebSocket 时异常: {e}")
        finally:
            self.ws = None


async def synthesize_qwen3_realtime_to_file(
    *,
    ws_url: str,
    api_key: str,
    voice: str,
    mode: SessionMode,
    language_type: str,
    text: str,
    output_path: Path,
    sample_rate: int = 24000,
    log: Optional[Callable[[str], None]] = None,
    instructions: str | None = None,
) -> None:
    """连接 Realtime API，提交文本；按保存扩展名将 ``response_format`` 设为 ``wav`` 或 ``mp3``，增量写入完整文件二进制流。"""
    output_path = Path(output_path)
    suf = output_path.suffix.lower()
    if suf == ".wav":
        response_format = "wav"
    elif suf == ".mp3":
        response_format = "mp3"
    else:
        raise ValueError("保存路径扩展名须为 .wav 或 .mp3（用于设置 session.response_format）")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    fout = open(output_path, "wb")
    try:
        if log:
            log(
                f"输出文件「{output_path.name}」→ session.response_format={response_format!r}, "
                f"sample_rate={int(sample_rate)}"
            )

        def audio_callback(audio_bytes: bytes) -> None:
            fout.write(audio_bytes)

        client = TTSRealtimeClient(
            base_url=ws_url,
            api_key=api_key,
            voice=voice,
            mode=mode,
            audio_callback=audio_callback,
            language_type=language_type,
            sample_rate=sample_rate,
            response_format=response_format,
            log=log,
            instructions=instructions,
        )
        recv_task: asyncio.Task | None = None
        try:
            await client.connect()
            recv_task = asyncio.create_task(client.handle_messages())
            client._recv_task = recv_task
            try:
                await client.wait_for_session_created()
                await client.wait_for_session_updated()
                await client.append_text(text)
                await asyncio.sleep(0.1)
                await client.commit_text_buffer()
                _recv_started = time.perf_counter()
                await client.wait_for_response_done()
                _elapsed = time.perf_counter() - _recv_started
                if log:
                    log(
                        f"实时流接收完毕，耗时 {_elapsed:.3f} 秒（自 input_text_buffer.commit 起至 response.done）"
                    )
                await asyncio.sleep(0.1)
            finally:
                await client.close()
                if recv_task is not None:
                    try:
                        await recv_task
                    except TimeoutError:
                        pass
                    except asyncio.CancelledError:
                        pass
                    except Exception:
                        pass
                client._recv_task = None
        finally:
            fout.flush()
    finally:
        fout.close()
