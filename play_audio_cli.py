# -- coding: utf-8 --
"""用 miniaudio 播放 MP3/WAV 的命令行测试工具。"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="miniaudio 播放测试")
    parser.add_argument("-f", "--file", required=True, type=Path, metavar="PATH", help="音频文件路径")
    parser.add_argument(
        "--frame_time", type=int, default=40, metavar="MS",
        help="每帧时长（毫秒），决定播放回调粒度与进度更新间隔（默认 40）",
    )
    pos_group = parser.add_mutually_exclusive_group()
    pos_group.add_argument("-p", "--pos", type=float, default=None, metavar="SEC", help="起始秒数（默认 0）")
    pos_group.add_argument("-r", "--ratio", type=float, default=None, metavar="RATIO", help="起始比例 0.0~1.0")
    args = parser.parse_args()

    path = args.file.expanduser().resolve()
    if not path.is_file():
        print(f"错误：文件不存在：{path}", file=sys.stderr)
        return 2

    import miniaudio

    print(f"解码: {path}")
    try:
        audio = miniaudio.decode_file(str(path))
    except Exception as e:
        print(f"解码失败: {e}", file=sys.stderr)
        return 1

    sr = audio.sample_rate
    nch = audio.nchannels
    total_frames = audio.num_frames
    total_sec = total_frames / sr
    pcm = audio.samples.tobytes() if hasattr(audio.samples, "tobytes") else bytes(memoryview(audio.samples).cast("B"))
    bytes_per_frame = nch * 2  # 16-bit

    print(f"采样率={sr}, 声道={nch}, 总帧数={total_frames}, 总时长={total_sec:.3f}s, PCM={len(pcm)}bytes")

    # ---- 参数 ----
    if args.ratio is not None:
        ratio = max(0.0, min(1.0, args.ratio))
        start_sec = ratio * total_sec
        start_frame = int(start_sec * sr)
    elif args.pos is not None:
        start_sec = max(0.0, min(total_sec, args.pos))
        start_frame = int(start_sec * sr)
    else:
        start_sec = 0.0
        start_frame = 0

    frame_time_ms = max(10, int(args.frame_time))

    print(f"起始位置: {start_sec:.3f}s (frame={start_frame}), 回调间隔={frame_time_ms}ms")

    # ---- PCM 生成器 ----
    total = total_frames

    def pcm_generator():
        current = start_frame
        framecount = yield b""
        while True:
            remaining = total - current
            if remaining <= 0:
                current += framecount
                framecount = yield b"\x00" * (framecount * bytes_per_frame)
                continue
            actual = framecount if framecount <= remaining else remaining
            beg = current * bytes_per_frame
            end = beg + actual * bytes_per_frame
            chunk = pcm[beg:end]
            current += actual
            if actual < framecount:
                chunk += b"\x00" * ((framecount - actual) * bytes_per_frame)
            framecount = yield chunk

    gen = pcm_generator()
    next(gen)

    # ---- 播放 ----
    print("开始播放...")
    device = miniaudio.PlaybackDevice(
        output_format=miniaudio.SampleFormat.SIGNED16,
        nchannels=nch,
        sample_rate=sr,
        buffersize_msec=frame_time_ms,
    )
    device.start(gen)

    try:
        t0 = time.perf_counter()
        while True:
            time.sleep(frame_time_ms / 1000.0)
            elapsed = time.perf_counter() - t0
            cur = min(start_sec + elapsed, total_sec)
            print(f"\r  进度: {cur:.2f}s / {total_sec:.2f}s", end="", flush=True)
            if cur >= total_sec - 0.01:
                break
        print()
    except KeyboardInterrupt:
        print("\n中断")
    finally:
        device.stop()

    print("播放结束")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
