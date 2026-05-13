# -- coding: utf-8 --
"""播放进度条状态栏：毫秒格式化与 miniaudio 解码时长对齐逻辑（多 TTS Tab 共用）。"""

from __future__ import annotations


def fmt_mmss_no_fraction(ms: int) -> str:
    """斜杠左侧进度：仅 ``MM:SS``（毫秒向下取整到秒后格式化）。"""
    ms = max(0, int(ms))
    sec = ms // 1000
    m, s = divmod(sec, 60)
    return f"{m:02d}:{s:02d}"


def fmt_mmss_tenth(ms: int) -> str:
    """总时长等：``MM:SS.d``，秒位始终一位小数。"""
    ms = max(0, int(ms))
    total_sec = ms / 1000.0
    m = int(total_sec // 60)
    s_rem = total_sec - m * 60
    centis = int(round(s_rem * 10))
    if centis >= 600:
        m += centis // 600
        centis = centis % 600
    si = centis // 10
    tenth = centis % 10
    return f"{m:02d}:{si:02d}.{tenth}"


def fmt_mmss_tenth_from_seconds(sec: float) -> str:
    """与 `fmt_mmss_tenth` 一致，输入为秒浮点（避免先 round 成毫秒丢小数）。"""
    sec = max(0.0, float(sec))
    m = int(sec // 60)
    s_rem = sec - m * 60.0
    centis = int(round(s_rem * 10.0))
    if centis >= 600:
        m += centis // 600
        centis = centis % 600
    si = centis // 10
    tenth = centis % 10
    return f"{m:02d}:{si:02d}.{tenth}"


def trusted_total_ms(audio_duration_sec: float) -> int:
    """波形线程给出的 `_audio_duration_sec` → 毫秒（未就绪为 0）。"""
    if audio_duration_sec > 0:
        return int(round(audio_duration_sec * 1000))
    return 0


def playback_ratio(trusted_ms: int, pos_ms: int, dur_ms: int) -> float:
    """依据播放器 position/duration 得到 0~1。"""
    pos = max(0, int(pos_ms))
    dur = max(0, int(dur_ms))
    if dur > 0:
        return max(0.0, min(1.0, pos / float(dur)))
    td = max(0, int(trusted_ms))
    if td > 0:
        return max(0.0, min(1.0, pos / float(td)))
    return 0.0


def total_display_ms(trusted_ms: int, dur_ms: int, pos_ms: int) -> int:
    """状态栏总毫秒：优先波形可信时长，否则回退播放器 duration/position。"""
    if trusted_ms > 0:
        return trusted_ms
    d = max(0, int(dur_ms))
    p = max(0, int(pos_ms))
    return max(d, p)


def elapsed_display_ms(
    seek_display_pos_ms: int | None,
    trusted_ms: int,
    ratio: float,
    pos_ms: int,
) -> int:
    """当前「已播」展示毫秒（含 seek 预览与 trusted 比例缩放）。"""
    if seek_display_pos_ms is not None:
        return int(seek_display_pos_ms)
    if trusted_ms > 0:
        return int(round(float(ratio) * trusted_ms))
    return max(0, int(pos_ms))


def waveform_progress_ratio(
    trusted_ms: int,
    pos_ms: int,
    dur_ms: int,
    anchor_wave_ratio: float | None,
    anchor_pos_ms: int | None,
) -> float:
    """波形播放头 0~1：默认从播放器 position/duration 计算。

    anchor 参数留作用于未来精细对齐，当前仅回退到 ``playback_ratio``。
    """
    td = max(0, int(trusted_ms))
    pos = max(0, int(pos_ms))
    if (
        anchor_wave_ratio is not None
        and anchor_pos_ms is not None
        and td > 0
    ):
        r = float(anchor_wave_ratio) + (
            pos - int(anchor_pos_ms)
        ) / float(td)
        return max(0.0, min(1.0, r))
    return playback_ratio(td, pos, max(0, int(dur_ms)))
