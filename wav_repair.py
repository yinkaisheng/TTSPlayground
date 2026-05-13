# -- coding: utf-8 --
"""修复 WAV 文件头长度声明（多 TTS / GUI 共用，避免播放器提前结束）。"""

from __future__ import annotations

import struct
from pathlib import Path


def repair_wav_chunk_sizes(path: Path) -> None:
    """修正 WAV 的 data 块大小与 RIFF 总长度，避免部分播放器按错误时长提前结束。

    流式写入或远端生成的 WAV 可能出现头部声明长度与文件实际 PCM 数据不一致。
    """
    path = Path(path)
    if path.suffix.lower() != ".wav" or not path.is_file():
        return
    try:
        buf = bytearray(path.read_bytes())
    except OSError:
        return
    if len(buf) < 12 or bytes(buf[0:4]) != b"RIFF" or bytes(buf[8:12]) != b"WAVE":
        return
    pos = 12
    data_offset: int | None = None
    while pos + 8 <= len(buf):
        chunk_id = bytes(buf[pos : pos + 4])
        chunk_size = struct.unpack_from("<I", buf, pos + 4)[0]
        if chunk_id == b"data":
            data_offset = pos
            pcm_start = pos + 8
            pcm_len = len(buf) - pcm_start
            struct.pack_into("<I", buf, pos + 4, pcm_len)
            break
        pos += 8 + chunk_size + (chunk_size % 2)
    if data_offset is None:
        return
    struct.pack_into("<I", buf, 4, len(buf) - 8)
    path.write_bytes(buf)
