# -- coding: utf-8 --
"""TTS 标签页共用的波形预览组件与 miniaudio 后台加载线程。"""

from __future__ import annotations

import importlib
import math
import threading
from pathlib import Path
from types import ModuleType

from PyQt5.QtCore import QPointF, Qt, QThread, pyqtSignal
from PyQt5.QtGui import QColor, QPainter, QPen
from PyQt5.QtWidgets import QWidget

from gui.constants import UI_BG_PANEL, WAVEFORM_SOLID_FILL

# 各 Tab 波形控件实际像素宽度一致；首次在「已布局的可见 Tab」上算出条数后复用，
# 避免后续隐藏 Tab 在 restore 时 width() 过小导致 target_bars 偏少。
_waveform_target_bars_cached: int | None = None
_waveform_target_bars_lock = threading.Lock()


def _estimate_waveform_inner_width_px(canvas: QWidget, margin_px: int) -> int:
    """与 ``WaveformWidget.paintEvent`` 一致的绘制区内宽（逻辑像素）。"""
    w = int(canvas.width())
    win = canvas.window()
    ww = int(win.width()) if isinstance(win, QWidget) else 0
    if ww > 0:
        thin = w < max(280, ww // 6)
        if not canvas.isVisible() or thin:
            w = max(w, int(ww * 0.42))
    return max(0, w - 2 * margin_px)


def waveform_target_bars_for_widget(canvas: QWidget, margin_px: int = 2) -> int:
    """按波形控件宽度估算 ``target_bars``（约每 2px 一条峰）。

    全应用共用第一次计算结果（通常来自先加载、已可见的 Tab），其余 Tab 直接复用，
    与界面布局上三处波形条宽度一致的行为相符。
    """
    global _waveform_target_bars_cached

    with _waveform_target_bars_lock:
        if _waveform_target_bars_cached is not None:
            return _waveform_target_bars_cached
        inner = _estimate_waveform_inner_width_px(canvas, margin_px)
        _waveform_target_bars_cached = max(32, inner // 2)
        return _waveform_target_bars_cached


def _pcm_peaks_buckets_loop(
    pcm16: memoryview, *, channels: int, peak_target: int
) -> list[int]:
    """与 ``waveform_peaks_int16_pcm`` 一致：全帧率 mono，再分桶取最大绝对幅值（≤32767）。

    ``pcm16`` 须为交错 int16 ``memoryview.cast('h')``。
    """
    n_int16 = len(pcm16)
    if channels < 1 or peak_target < 1 or n_int16 % channels != 0:
        return []

    frame_count = n_int16 // channels
    if frame_count == 0:
        return []

    mono_sub: list[int] = []
    for fi in range(frame_count):
        off = fi * channels
        mono_sub.append(
            math.trunc(sum(int(pcm16[off + c]) for c in range(channels)) / channels)
        )

    n = len(mono_sub)
    if n == 0:
        return []
    chunk = max(1, n // peak_target)
    mags: list[int] = []
    for i in range(0, n, chunk):
        blk = mono_sub[i : i + chunk]
        ma = max(abs(x) for x in blk)
        mags.append(ma if ma <= 32767 else 32767)
    return mags


_peak_native_uncached = object()
_peak_native_unavailable = object()
_peak_native_lock = threading.Lock()
_cached_waveform_peaks_mod: ModuleType | object = _peak_native_uncached


def _try_waveform_native():
    """返回 ``gui._waveform_peaks``（ffi/lib）；首次失败时按需 ``ffi.compile`` 再试。

    仍失败则记入缓存并改用纯 Python，避免每次加载波形都调编译器。
    """

    global _cached_waveform_peaks_mod

    if _cached_waveform_peaks_mod is not _peak_native_uncached:
        return (
            None
            if _cached_waveform_peaks_mod is _peak_native_unavailable
            else _cached_waveform_peaks_mod
        )

    with _peak_native_lock:
        if _cached_waveform_peaks_mod is not _peak_native_uncached:
            return (
                None
                if _cached_waveform_peaks_mod is _peak_native_unavailable
                else _cached_waveform_peaks_mod
            )

        mod: ModuleType | None = None
        try:
            mod = importlib.import_module("gui._waveform_peaks")
        except (ImportError, OSError):
            pass

        if mod is None:
            try:
                from gui.waveform_peaks_ffi import compile_waveform_peaks

                compile_waveform_peaks(verbose=False)
                importlib.invalidate_caches()
                mod = importlib.import_module("gui._waveform_peaks")
            except Exception:
                mod = None

        if mod is None:
            _cached_waveform_peaks_mod = _peak_native_unavailable
            return None

        _cached_waveform_peaks_mod = mod
        return mod


def _finalize_peaks_int16_mag(mags: list[int]) -> list[float]:
    """Python：``peaks_int16 / 65536`` 后再按最大值归一到约 ``[0,1]``（与原 NumPy 版一致）。"""
    if not mags:
        return []
    scaled = [float(m) / 65536.0 for m in mags]
    mx = max(scaled)
    if mx < 1e-12:
        mx = 1.0
    return [p / mx for p in scaled]


def _pcm_as_bytes_view(samples: object) -> memoryview:
    """统一为可 ``cast('h')`` 的 PCM 缓冲区（零拷贝或非拷一次）。"""
    if isinstance(samples, memoryview):
        return samples if samples.readonly else memoryview(bytes(samples))
    if isinstance(samples, (bytes, bytearray)):
        return memoryview(samples)
    if hasattr(samples, "tobytes"):
        return memoryview(samples.tobytes())
    raise TypeError(f"不支持的 samples 类型: {type(samples)!r}")


def _pcm_frame_count(samples_len_bytes: int, channels: int) -> int:
    pair = channels * 2
    if pair <= 0 or samples_len_bytes % pair != 0:
        return 0
    return samples_len_bytes // pair


class WaveformLoadThread(QThread):
    """后台用 miniaudio 读音频并生成归一化包络。

    展示时长取解码后帧数除以采样率。峰值优先调用 cffi 扩展（参见 ``waveform_peaks_build.py``）；
    未编译或加载失败时回退纯 Python。
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

            path_str = str(self._path)
            audio = miniaudio.decode_file(path_str)
            pcm_mv = _pcm_as_bytes_view(audio.samples)
            sr = int(audio.sample_rate)
            ch = int(audio.nchannels)
            fc = _pcm_frame_count(pcm_mv.nbytes, ch)
            if fc == 0 or sr <= 0:
                self.done.emit([], 0.0)
                return

            pcm16 = pcm_mv.cast("h")
            dur_sec = float(fc) / float(sr)
            peak_target = self._target_bars
            cap = peak_target + 128
            wp = _try_waveform_native()
            mags: list[int]

            if wp is None:
                mags = _pcm_peaks_buckets_loop(pcm16, channels=ch, peak_target=peak_target)
            else:
                try:
                    buf = wp.ffi.from_buffer(pcm_mv)
                    ptr = wp.ffi.cast("const short *", buf)
                    peaks_buf = wp.ffi.new("short[]", cap)
                    written = wp.lib.waveform_peaks_int16_pcm(
                        ptr, fc, ch, peak_target, cap, peaks_buf
                    )
                    if written < 0:
                        raise RuntimeError(f"waveform_peaks_int16_pcm 返回 {written}")
                    mags = [int(peaks_buf[k]) for k in range(written)]
                except Exception:
                    mags = _pcm_peaks_buckets_loop(pcm16, channels=ch, peak_target=peak_target)

            peaks = _finalize_peaks_int16_mag(mags)
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
            n_peaks = len(self._peaks)
            if n_peaks == 0:
                return
            bar_col = QColor("#4a7abf")
            bar_w = w / float(max(n_peaks, 1))
            half = h / 2.0 - 2.0
            if WAVEFORM_SOLID_FILL:
                for i, pk in enumerate(self._peaks):
                    left_f = rect.left() + i * bar_w
                    right_f = rect.left() + (i + 1) * bar_w
                    xl = int(math.floor(left_f))
                    xr = int(math.ceil(right_f))
                    bw = max(1, xr - xl)
                    ph = max(1.0, pk * half)
                    yt = int(math.floor(mid - ph))
                    hb = max(1, int(math.ceil(mid + ph)) - yt)
                    p.fillRect(xl, yt, bw, hb, bar_col)
            else:
                p.setPen(QPen(bar_col, 1))
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
