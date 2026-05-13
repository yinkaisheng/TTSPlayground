# -- coding: utf-8 --
"""Sambert TTS WebSocket 合成（与 sambert_tts_cli 逻辑一致，支持参数化）。"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Callable
from pathlib import Path

import websockets

from gui.constants import TTS_WEBSOCKET_OPEN_TIMEOUT_SEC, TTS_WEBSOCKET_RECV_TIMEOUT_SEC
from gui.format_log import format_log_line
from wav_repair import repair_wav_chunk_sizes


def build_run_task(
    model: str,
    text: str,
    *,
    audio_format: str,
    sample_rate: int,
    volume: int,
    rate: float,
    pitch: float,
    word_timestamp_enabled: bool,
    phoneme_timestamp_enabled: bool,
) -> dict:
    task_id = str(uuid.uuid4())
    fmt = audio_format.lower().strip()
    if fmt not in ("mp3", "wav", "pcm"):
        fmt = "mp3"
    return {
        "header": {
            "action": "run-task",
            "task_id": task_id,
            "streaming": "out",
        },
        "payload": {
            "model": model,
            "task_group": "audio",
            "task": "tts",
            "function": "SpeechSynthesizer",
            "input": {"text": text},
            "parameters": {
                "text_type": "PlainText",
                "format": fmt,
                "sample_rate": int(sample_rate),
                "volume": int(volume),
                "rate": float(rate),
                "pitch": float(pitch),
                "word_timestamp_enabled": bool(word_timestamp_enabled),
                "phoneme_timestamp_enabled": bool(phoneme_timestamp_enabled),
            },
        },
    }


async def synthesize_sambert_tts(
    ws_url: str,
    api_key: str,
    model: str,
    text: str,
    output_path: Path,
    *,
    audio_format: str,
    sample_rate: int,
    volume: int,
    rate: float,
    pitch: float,
    word_timestamp_enabled: bool,
    phoneme_timestamp_enabled: bool,
    log: Callable[[str], None] | None = None,
) -> None:
    def lg(msg: str) -> None:
        if log:
            log(format_log_line(msg))

    output_path = Path(output_path)
    if output_path.exists():
        output_path.write_bytes(b"")
        lg(f"已清空输出文件: {output_path}")
    else:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.touch()
        lg(f"已创建输出文件: {output_path}")

    headers = {
        "Authorization": f"bearer {api_key}",
        "X-DashScope-DataInspection": "enable",
    }
    lg('请求头: Authorization=bearer ***（已隐藏）, X-DashScope-DataInspection=enable')

    payload = build_run_task(
        model,
        text,
        audio_format=audio_format,
        sample_rate=sample_rate,
        volume=volume,
        rate=rate,
        pitch=pitch,
        word_timestamp_enabled=word_timestamp_enabled,
        phoneme_timestamp_enabled=phoneme_timestamp_enabled,
    )

    send_body = json.dumps(payload, ensure_ascii=False)
    ot = float(TTS_WEBSOCKET_OPEN_TIMEOUT_SEC)
    rt = float(TTS_WEBSOCKET_RECV_TIMEOUT_SEC)
    lg(f"正在连接 WebSocket（握手超时 {ot:g}s）: {ws_url}")
    async with websockets.connect(
        ws_url,
        additional_headers=headers,
        open_timeout=ot,
        close_timeout=10,
    ) as ws:
        lg("WebSocket 已连接")
        lg(f"发送 run-task JSON（长度 {len(send_body)} 字符）: {send_body}")
        await ws.send(send_body)

        binary_count = 0
        binary_total = 0
        task_started_mono: float | None = None
        task_receive_seconds: float | None = None

        with output_path.open("ab") as fout:
            while True:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=rt)
                except asyncio.TimeoutError:
                    lg(f"接收 WebSocket 消息超时（{rt:g}s）")
                    await ws.close()
                    raise RuntimeError(
                        f"接收 WebSocket 消息超时（{rt:g}s），请检查网络或服务端。"
                    ) from None
                except websockets.exceptions.ConnectionClosed as e:
                    lg(f"WebSocket 连接关闭: code={e.code} reason={e.reason!r}")
                    break

                if isinstance(raw, bytes):
                    binary_count += 1
                    binary_total += len(raw)
                    fout.write(raw)
                    lg(f"收到音频二进制块 #{binary_count}，本段 {len(raw)} 字节，累计 {binary_total} 字节")
                    continue

                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    preview = raw if len(raw) <= 500 else raw[:500] + "…"
                    lg(f"收到非 JSON 文本（预览）: {preview!r}")
                    continue

                header = message.get("header") or {}
                event = header.get("event")
                lg(f"收到 JSON 消息: {json.dumps(message, ensure_ascii=False)}")

                if event == "task-started":
                    task_started_mono = time.monotonic()
                elif event == "result-generated":
                    pass
                elif event == "task-finished":
                    if task_started_mono is not None:
                        task_receive_seconds = time.monotonic() - task_started_mono
                    lg("事件 task-finished，准备关闭 WebSocket")
                    await ws.close()
                    break
                elif event == "task-failed":
                    err = header.get("error_message") or header.get("message") or str(header)
                    lg(f"事件 task-failed: {err}")
                    await ws.close()
                    raise RuntimeError(err)
                else:
                    lg(f"未特别处理的事件: {event!r}")

    if output_path.suffix.lower() == ".wav":
        repair_wav_chunk_sizes(output_path)
        lg("已根据实际文件大小修正 WAV 头部 data/RIFF 长度（便于播放器读全时长）")

    line = f"合成流程结束，输出文件: {output_path.resolve()}"
    if task_receive_seconds is not None:
        line += f"（接收耗时 task-started→task-finished: {task_receive_seconds:.3f}s）"
    lg(line)


def run_sambert_tts_sync(**kwargs) -> None:
    asyncio.run(synthesize_sambert_tts(**kwargs))
