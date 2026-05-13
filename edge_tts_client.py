# -- coding: utf-8 --
"""Microsoft Edge 在线 TTS（edge-tts）异步封装，供 GUI 调用。

逻辑对齐仓库参考脚本 ``edgeTTS_cli.py``（``Communicate`` + ``save`` / ``list_voices``）。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any


def apply_windows_selector_event_loop_policy() -> None:
    """与 CLI 一致：Windows 下使用 Selector 策略，避免部分环境下默认策略异常。"""
    if sys.platform == "win32":
        import asyncio

        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


async def list_edge_voice_short_names() -> list[str]:
    """返回所有可用音色的 ``ShortName`` 列表（已排序去重）。"""
    import edge_tts

    raw: list[dict[str, Any]] = await edge_tts.list_voices()
    names = sorted(
        {
            str(v.get("ShortName", "")).strip()
            for v in raw
            if str(v.get("ShortName", "")).strip()
        }
    )
    return names


async def synthesize_edge_tts_to_file(
    text: str,
    voice: str,
    rate: str,
    output_path: Path,
    *,
    volume: str | None = None,
    pitch: str | None = None,
) -> None:
    """合成语音并写入本地文件（默认按扩展名保存；GUI Edge 页限制为 ``.mp3``）。"""
    import edge_tts

    r = (rate or "").strip() or "+0%"
    kwargs: dict[str, Any] = {"text": text, "voice": voice.strip(), "rate": r}
    vol = (volume or "").strip()
    if vol:
        kwargs["volume"] = vol
    pch = (pitch or "").strip()
    if pch:
        kwargs["pitch"] = pch
    comm = edge_tts.Communicate(**kwargs)
    outp = output_path.resolve()
    outp.parent.mkdir(parents=True, exist_ok=True)
    await comm.save(str(outp))
