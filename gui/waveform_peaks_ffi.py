# -- coding: utf-8 --
"""波形峰值 cffi 扩展：源码与 ``ffi.compile`` 入口（可动态编译）。

与根目录参考脚本 ``cffi_sum_cli.py`` 相同思路：``import gui._waveform_peaks`` 失败时再编译一次后重试。
手动编译仍可用 ``python gui/waveform_peaks_build.py``。
"""

from __future__ import annotations

import os
from pathlib import Path

from cffi import FFI

WAVEFORM_PEAK_FFI_MODULE = "gui._waveform_peaks"

WAVEFORM_PEAK_CDEF = """int waveform_peaks_int16_pcm(
    const short *interleaved_pcm,
    unsigned int frame_count,
    int channels,
    int peak_target_bars,
    int peaks_capacity,
    short *peaks_out);
"""

WAVEFORM_PEAK_C_SOURCE = r"""
#include <stddef.h>
#include <stdint.h>

static int16_t mono_mean_frame(const int16_t *frame, int channels)
{
    int32_t sum = 0;
    int c;
    for (c = 0; c < channels; c++) {
        sum += (int32_t)frame[c];
    }
    return (int16_t)(sum / channels);
}

int waveform_peaks_int16_pcm(
    const int16_t *interleaved_pcm,
    uint32_t frame_count,
    int channels,
    int peak_target_bars,
    int peaks_capacity,
    int16_t *peaks_out)
{
    uint32_t n;
    uint32_t chunk;

    if (interleaved_pcm == NULL || peaks_out == NULL) {
        return -1;
    }
    if (channels < 1 || channels > 512) {
        return -2;
    }
    if (frame_count == 0u) {
        return 0;
    }
    if (peak_target_bars < 1 || peaks_capacity < 1) {
        return -3;
    }

    n = frame_count;
    chunk = n / (uint32_t)peak_target_bars;
    if (chunk < 1u) {
        chunk = 1u;
    }

    {
        uint32_t i;
        int written = 0;
        for (i = 0u; i < n && written < peaks_capacity; i += chunk) {
            uint32_t j_end = i + chunk;
            uint32_t si;
            int32_t max_abs = 0;

            if (j_end > n) {
                j_end = n;
            }
            for (si = i; si < j_end; si++) {
                uint32_t fi = si;
                int16_t mono;
                int32_t ma;
                if (fi >= frame_count) {
                    break;
                }
                mono = mono_mean_frame(
                    interleaved_pcm + ((ptrdiff_t)fi * (ptrdiff_t)channels),
                    channels);
                ma = (int32_t)mono;
                if (ma < 0) {
                    ma = -ma;
                }
                if (ma > max_abs) {
                    max_abs = ma;
                }
            }
            if (max_abs > 32767) {
                max_abs = 32767;
            }
            peaks_out[written] = (int16_t)max_abs;
            written++;
        }
        return written;
    }
}
"""


def waveform_peaks_repo_root() -> Path:
    """含 ``gui/`` 包目录的工程根（``gui`` 的父目录）。"""
    return Path(__file__).resolve().parent.parent


def compile_waveform_peaks(*, verbose: bool = False) -> None:
    """在工作区根目录下执行 ``ffi.compile``，生成 ``gui/_waveform_peaks*.pyd``。"""
    ffi = FFI()
    ffi.cdef(WAVEFORM_PEAK_CDEF)
    ffi.set_source(WAVEFORM_PEAK_FFI_MODULE, WAVEFORM_PEAK_C_SOURCE, sources=[])

    root = str(waveform_peaks_repo_root())
    old = os.getcwd()
    os.chdir(root)
    try:
        ffi.compile(verbose=verbose)
    finally:
        os.chdir(old)
