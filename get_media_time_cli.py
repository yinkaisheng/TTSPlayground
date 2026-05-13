# -- coding: utf-8 --
"""读取本地音频文件时长：分别以 miniaudio、ffprobe（FFmpeg）、PyQt5 估算。"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path


def duration_miniaudio_seconds(path: Path) -> float:
    """使用 miniaudio 解码后的样本长度推算时长（秒）。

    说明：与 GUI 波形线程思路一致，按 ``num_frames/sr`` 取值（原生采样率解码）。
    """
    import miniaudio

    audio = miniaudio.decode_file(str(path))
    sr = audio.sample_rate
    if sr <= 0:
        raise RuntimeError("miniaudio 返回无效采样率。")
    n = audio.num_frames
    if n == 0:
        raise RuntimeError("miniaudio 解码帧数为 0。")
    return float(n) / float(sr)


def duration_ffprobe_seconds(path: Path) -> float:
    """使用 ffprobe（随 FFmpeg 安装）读取容器 ``format.duration``（秒）。"""
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=120,
        )
    except FileNotFoundError as e:
        raise RuntimeError(
            "未找到 ffprobe：请安装 FFmpeg 并将 bin 目录加入 PATH。"
        ) from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("ffprobe 执行超时。") from e
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"ffprobe 退出码 {proc.returncode}: {err or '无输出'}")
    line = (proc.stdout or "").strip().splitlines()
    if not line:
        raise RuntimeError("ffprobe 无 stdout。")
    try:
        return float(line[0].strip())
    except ValueError as e:
        raise RuntimeError(f"无法解析时长数值：{line[0]!r}") from e


def duration_pyqt5_seconds(path: Path, *, timeout_sec: float = 6.0) -> float:
    """使用 PyQt5 ``QMediaPlayer`` 读取 ``duration()``（毫秒 → 秒）。

    部分解码后端会异步更新时长，故在超时内轮询 ``processEvents``。
    """
    from PyQt5.QtCore import QUrl
    from PyQt5.QtMultimedia import QMediaContent, QMediaPlayer
    from PyQt5.QtWidgets import QApplication

    abs_path = path.resolve()
    app = QApplication.instance()
    created_app = False
    if app is None:
        app = QApplication(sys.argv)
        created_app = True

    player = QMediaPlayer()
    player.setMedia(QMediaContent(QUrl.fromLocalFile(str(abs_path))))

    deadline = time.perf_counter() + float(timeout_sec)
    best_ms = 0
    try:
        while time.perf_counter() < deadline:
            app.processEvents()
            d = int(player.duration())
            if d > best_ms:
                best_ms = d
            time.sleep(0.05)
    finally:
        player.setMedia(QMediaContent())

    if created_app:
        app.quit()

    if best_ms <= 0:
        raise RuntimeError(
            "QMediaPlayer 未给出大于 0 的时长（文件不支持或当前 Qt 多媒体后端不可用）。"
        )
    return best_ms / 1000.0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="读取音频文件时长（秒，打印三位小数），分别尝试 miniaudio、ffprobe、PyQt5。"
    )
    parser.add_argument(
        "-f",
        "--file",
        required=True,
        type=Path,
        metavar="PATH",
        help="音频文件路径",
    )
    args = parser.parse_args()
    path = args.file.expanduser().resolve()
    if not path.is_file():
        print(f"错误：不是可读文件：{path}", file=sys.stderr)
        return 2

    rows: list[tuple[str, float]] = []
    errors: list[tuple[str, str]] = []

    for label, fn in (
        ("miniaudio", duration_miniaudio_seconds),
        ("ffmpeg(ffprobe)", duration_ffprobe_seconds),
        ("PyQt5", duration_pyqt5_seconds),
    ):
        try:
            sec = fn(path)
            rows.append((label, sec))
        except Exception as e:
            errors.append((label, str(e)))

    for label, sec in rows:
        print(f"{label}: {sec:.3f}")

    for label, msg in errors:
        print(f"{label}: （失败）{msg}")

    return 0 if len(rows) == 3 else 1


if __name__ == "__main__":
    raise SystemExit(main())
