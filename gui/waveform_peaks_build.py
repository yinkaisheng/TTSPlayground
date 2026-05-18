# -- coding: utf-8 --
"""离线编译波形峰值 cffi 扩展。

在项目根目录执行::

    pip install cffi setuptools
    python gui/waveform_peaks_build.py

GUI 运行时若扩展缺失且无 C 编译器，会自动回退纯 Python。
"""

from __future__ import annotations

from gui.waveform_peaks_ffi import compile_waveform_peaks

if __name__ == "__main__":
    compile_waveform_peaks(verbose=True)
