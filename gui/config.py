# -- coding: utf-8 --
"""YAML 配置与界面辅助（多 Tab 可共用）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml

# user_setting.yaml 根级版本号（写入时由 save_user_settings_yaml 填充）
USER_SETTINGS_FILE_VERSION = 1


def mask_api_key(secret: str) -> str:
    s = (secret or "").strip()
    if not s:
        return ""
    if len(s) <= 16:
        return s
    return s[:16] + "..."


def load_sambert_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"ws_url": "", "voices": []}
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data if isinstance(data, dict) else {}


_EDGE_AXIS_DEFAULTS: dict[str, tuple[int, int, int]] = {
    "rate": (-50, 50, 5),
    "volume": (-100, 100, 5),
    "pitch": (-100, 100, 5),
}


def edge_slider_axis_bounds(
    cfg: dict[str, Any], axis: Literal["rate", "volume", "pitch"]
) -> tuple[int, int, int]:
    """读取 ``edge_tts.yaml`` 中 ``{axis}_min`` / ``{axis}_max`` / ``{axis}_step``；非法或缺失时回退默认。"""
    d = _EDGE_AXIS_DEFAULTS[axis]
    try:
        mn = int(cfg[f"{axis}_min"])
        mx = int(cfg[f"{axis}_max"])
        st = int(cfg[f"{axis}_step"])
    except (KeyError, TypeError, ValueError):
        return d
    if st <= 0 or mx < mn or (mx - mn) % st != 0:
        return d
    return (mn, mx, st)


def load_edge_tts_yaml(path: Path) -> dict[str, Any]:
    """Edge TTS 标签默认音色列表与说明。"""
    builtin: dict[str, Any] = {
        "voices": [],
        "default_voice": "zh-CN-XiaoyiNeural",
        "default_rate": "+0%",
        "default_output_filename": "edge_tts.mp3",
        "rate_min": -50,
        "rate_max": 50,
        "rate_step": 5,
        "volume_min": -100,
        "volume_max": 100,
        "volume_step": 5,
        "pitch_min": -100,
        "pitch_max": 100,
        "pitch_step": 5,
    }
    if not path.is_file():
        return dict(builtin)
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        return dict(builtin)
    out = dict(builtin)
    out.update(data)
    return out


def load_qwen3_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {
            "realtime_base_url": "wss://dashscope.aliyuncs.com/api-ws/v1/realtime",
            "voices": [],
            "models": [],
        }
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data if isinstance(data, dict) else {}


def load_api_key_entries(path: Path) -> list[dict[str, Any]]:
    """读取 ``bailian_api_key.yaml`` 的 ``keys`` 列表。"""
    if not path.is_file():
        return []
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        return []
    keys = data.get("keys")
    if not isinstance(keys, list):
        return []
    return [x for x in keys if isinstance(x, dict)]


def voice_description(v: dict[str, Any]) -> str:
    ts_ok = bool(v.get("voice_support_timestamp"))
    ts_word = "支持时间戳" if ts_ok else "不支持时间戳"
    parts = [
        str(v.get("voice_type", "")),
        str(v.get("style", "")),
        str(v.get("voice_language", "")),
        str(v.get("voice_sample_rate", "")),
        ts_word,
    ]
    return " ".join(p for p in parts if p)


def qwen3_voice_description(v: dict[str, Any]) -> str:
    parts = [
        str(v.get("style", "")),
        str(v.get("voice_language", "")),
        str(v.get("note", "")),
    ]
    return " ".join(p for p in parts if p)


def load_user_settings_yaml(path: Path) -> dict[str, Any]:
    """读取界面记忆 YAML；文件不存在或格式异常时返回空字典。"""
    if not path.is_file():
        return {}
    try:
        with path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except OSError:
        return {}
    return data if isinstance(data, dict) else {}


def save_user_settings_yaml(path: Path, data: dict[str, Any]) -> None:
    """将界面记忆写入 YAML（UTF-8，便于人工查看）。"""
    payload = dict(data)
    payload.setdefault("version", USER_SETTINGS_FILE_VERSION)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(
            payload,
            f,
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
        )
