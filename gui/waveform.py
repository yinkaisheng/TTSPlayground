# -- coding: utf-8 --
"""TTS 标签页共用的波形预览组件与 miniaudio 后台加载线程。"""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import QPointF, Qt, QThread, pyqtSignal
from PyQt5.QtGui import QColor, QPainter, QPen
from PyQt5.QtWidgets import QWidget

from gui.constants import UI_BG_PANEL


class WaveformLoadThread(QThread):
    """后台用 miniaudio 读音频并生成归一化包络，避免阻塞界面。

    展示用时长一律取 miniaudio 解码后的 ``len(samples)/sr``，与波形包络同源。
    """

    done = pyqtSignal(list, float)
    failed = pyqtSignal(str)

    def __init__(self, path: Path, target_bars: int = 480) -> None:
        super().__init__()
        self._path = path
        self._target_bars = max(32, int(target_bars))

    def run(self) -> None:
        try:
            import miniaudio
            import numpy as np

            path_str = str(self._path)
            audio = miniaudio.decode_file(path_str)
            # miniaudio 返回 int16 PCM；转为 float32 [-1, 1] 方便后续计算
            raw = np.frombuffer(audio.samples, dtype=np.int16).astype(np.float32)
            raw /= 32768.0
            if audio.nchannels > 1:
                raw = raw.reshape(-1, audio.nchannels).mean(axis=1)
            sr = audio.sample_rate
            n = int(raw.shape[0])
            if n == 0 or sr <= 0:
                self.done.emit([], 0.0)
                return
            dur_sec = float(n) / float(sr)
            # 降采样到约 8000 Hz 等效以减少计算量
            downsample = max(1, int(round(sr / 8000.0)))
            if downsample > 1:
                y = raw[::downsample]
                n = int(y.shape[0])
            else:
                y = raw
            chunk = max(1, n // self._target_bars)
            peaks: list[float] = []
            for i in range(0, n, chunk):
                block = y[i : i + chunk]
                peaks.append(float(np.max(np.abs(block))))
            m = max(peaks) if peaks else 1.0
            if m < 1e-12:
                m = 1.0
            peaks = [p / m for p in peaks]
            self.done.emit(peaks, dur_sec)
        except Exception as e:
            self.failed.emit(str(e))


class WaveformWidget(QWidget):
    """波形预览：点击按比例发出 seek 请求；播放头为竖线。"""

    seek_requested = pyqtSignal(float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("waveformCanvas")
        self._peaks: list[float] = []
        self._playhead = 0.0
        self.setMinimumWidth(160)
        self.setMinimumHeight(28)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("波形预览；点击可跳转到对应播放位置")

    def clear(self) -> None:
        self._peaks = []
        self._playhead = 0.0
        self.update()

    def set_peaks(self, peaks: list[float]) -> None:
        self._peaks = list(peaks)
        self.update()

    def set_playhead(self, ratio: float) -> None:
        r = max(0.0, min(1.0, float(ratio)))
        if abs(r - self._playhead) > 1e-5:
            self._playhead = r
            self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            margin = 2
            w = self.width() - 2 * margin
            if w > 0:
                x = event.pos().x() - margin
                ratio = max(0.0, min(1.0, x / w))
                self.seek_requested.emit(ratio)
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:
        del event
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.Antialiasing)
            full = self.rect()
            p.fillRect(full, QColor(UI_BG_PANEL))
            p.setPen(QPen(QColor("#c8d4e6"), 1))
            p.drawRect(full.adjusted(0, 0, -1, -1))
            margin = 2
            rect = full.adjusted(margin, margin, -margin, -margin)
            w = rect.width()
            h = rect.height()
            if h <= 0 or w <= 0:
                return
            mid = rect.top() + h / 2.0
            n = len(self._peaks)
            if n == 0:
                return
            p.setPen(QPen(QColor("#4a7abf"), 1))
            bar_w = w / float(max(n, 1))
            half = h / 2.0 - 2.0
            for i, pk in enumerate(self._peaks):
                x0 = rect.left() + i * bar_w
                xc = x0 + bar_w / 2.0
                ph = max(1.0, pk * half)
                p.drawLine(QPointF(xc, mid - ph), QPointF(xc, mid + ph))
            xh = rect.left() + self._playhead * w
            p.setPen(QPen(QColor("#c0392b"), 2))
            p.drawLine(QPointF(xh, rect.top()), QPointF(xh, rect.bottom()))
        finally:
            p.end()
