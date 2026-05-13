# -- coding: utf-8 --
"""基于 miniaudio 的音频播放器，替代 QMediaPlayer 的播放职责。

不持有文件句柄（load 时全量解码到内存）。
通过 ``buffersize_msec`` 控制底层回调粒度，实现平滑进度更新。
"""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import QObject, QTimer, pyqtSignal

# 播放回调间隔（毫秒）：miniaudio 以此周期请求 PCM 数据，同时决定进度更新粒度
FRAME_TIME_MS = 40


def _to_bytes(samples) -> bytes:
    """miniaudio.decode_file().samples 可能是 numpy ndarray 或 bytes，统一转为 bytes。"""
    if hasattr(samples, "tobytes"):
        return samples.tobytes()
    return bytes(memoryview(samples).cast("B"))


class PlaybackState:
    Stopped = 0
    Playing = 1
    Paused = 2


class MiniAudioPlayer(QObject):
    """miniaudio 音频播放器。"""

    positionChanged = pyqtSignal(int)
    durationChanged = pyqtSignal(int)
    finished = pyqtSignal()
    error_occurred = pyqtSignal(str)
    stateChanged = pyqtSignal(int)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pcm_bytes = b""
        self._sample_rate = 0
        self._nchannels = 0
        self._total_frames = 0
        self._duration_ms = 0
        self._state = PlaybackState.Stopped
        self._device = None
        self._stream_gen = None
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(FRAME_TIME_MS)
        self._poll_timer.timeout.connect(self._poll_position)
        self._consumed_frames = 0
        self._seek_offset_frames = 0

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def setNotifyInterval(self, ms: int) -> None:
        pass  # 由 FRAME_TIME_MS 统一控制，忽略外部设置

    def load(self, path: Path) -> None:
        self._release_device()
        self._pcm_bytes = b""
        self._sample_rate = 0
        self._nchannels = 0
        self._total_frames = 0
        self._duration_ms = 0

        import miniaudio

        try:
            audio = miniaudio.decode_file(str(path.resolve()))
        except Exception as e:
            self.error_occurred.emit(str(e))
            return

        self._pcm_bytes = _to_bytes(audio.samples)
        self._sample_rate = audio.sample_rate
        self._nchannels = audio.nchannels
        self._total_frames = audio.num_frames
        self._duration_ms = int(self._total_frames / self._sample_rate * 1000)
        self.durationChanged.emit(self._duration_ms)

    def unload(self) -> None:
        self.stop()
        self._pcm_bytes = b""
        self._sample_rate = 0
        self._nchannels = 0
        self._total_frames = 0
        self._duration_ms = 0

    def play(self) -> None:
        if self._state == PlaybackState.Playing:
            return
        if self._state == PlaybackState.Paused:
            self._resume()
            return
        if self._total_frames <= 0 or not self._pcm_bytes:
            return
        self._consumed_frames = self._seek_offset_frames
        self._start_device()
        self._set_state(PlaybackState.Playing)
        self._poll_timer.start()

    def pause(self) -> None:
        if self._state != PlaybackState.Playing:
            return
        self._seek_offset_frames = self._consumed_frames
        self._release_device()
        self._set_state(PlaybackState.Paused)
        self._poll_timer.stop()

    def stop(self) -> None:
        self._release_device()
        self._seek_offset_frames = 0
        self._consumed_frames = 0
        self._set_state(PlaybackState.Stopped)
        self._poll_timer.stop()
        self.positionChanged.emit(0)

    def setPosition(self, ms: int) -> None:
        pos_ms = max(0, min(self._duration_ms, int(ms)))
        target_frame = int(pos_ms / 1000.0 * self._sample_rate)
        was_playing = self._state == PlaybackState.Playing
        if self._device is not None:
            self._release_device()
        self._seek_offset_frames = target_frame
        self._consumed_frames = target_frame
        if was_playing:
            self._start_device()
            self._poll_timer.start()

    def position(self) -> int:
        if self._sample_rate <= 0:
            return 0
        return int(self._consumed_frames / self._sample_rate * 1000)

    def duration(self) -> int:
        return self._duration_ms

    def state(self) -> int:
        return self._state

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    def _set_state(self, state: int) -> None:
        if self._state != state:
            self._state = state
            self.stateChanged.emit(state)

    def _start_device(self) -> None:
        import miniaudio

        nch = self._nchannels
        sr = self._sample_rate
        samp_w = 2  # 16-bit int
        bpf = nch * samp_w  # bytes per frame
        total = self._total_frames
        pcm = self._pcm_bytes
        start_frame = self._consumed_frames
        player_ref = self

        def pcm_generator():
            current = start_frame
            framecount = yield b""
            while True:
                remaining = total - current
                if remaining <= 0:
                    current += framecount
                    framecount = yield b"\x00" * (framecount * bpf)
                    continue
                actual = framecount if framecount <= remaining else remaining
                beg = current * bpf
                end = beg + actual * bpf
                chunk = pcm[beg:end]
                current += actual
                if actual < framecount:
                    chunk += b"\x00" * ((framecount - actual) * bpf)
                player_ref._consumed_frames = current
                framecount = yield chunk

        gen = pcm_generator()
        next(gen)

        self._stream_gen = gen
        self._device = miniaudio.PlaybackDevice(
            output_format=miniaudio.SampleFormat.SIGNED16,
            nchannels=nch,
            sample_rate=sr,
            buffersize_msec=FRAME_TIME_MS,
        )
        self._device.start(gen)

    def _release_device(self) -> None:
        if self._device is not None:
            try:
                self._device.stop()
            except Exception:
                pass
            self._device = None
        self._stream_gen = None

    def _resume(self) -> None:
        if self._state != PlaybackState.Paused:
            return
        self._start_device()
        self._set_state(PlaybackState.Playing)
        self._poll_timer.start()

    def _poll_position(self) -> None:
        if self._state != PlaybackState.Playing:
            return
        pos_ms = self.position()
        self.positionChanged.emit(pos_ms)
        if self._consumed_frames >= self._total_frames:
            self._release_device()
            self._seek_offset_frames = 0
            self._consumed_frames = 0
            self._set_state(PlaybackState.Stopped)
            self._poll_timer.stop()
            self.finished.emit()
