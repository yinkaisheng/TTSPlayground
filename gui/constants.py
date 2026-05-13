# -- coding: utf-8 --
"""工程路径、用户界面 YAML 小节键名、全局 UI 配色与字号边界（与样式表一致）。"""

import ctypes
import sys
from pathlib import Path


def get_dpi_scale() -> float:
    """Windows 下获取系统 DPI 缩放比例（150% → 1.5），非 Windows 返回 1.0。"""
    if sys.platform == "win32":
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        dc = user32.GetDC(None)
        logical_w = gdi32.GetDeviceCaps(dc, 8)   # HORZRES
        physical_w = gdi32.GetDeviceCaps(dc, 118)  # DESKTOPHORZRES
        user32.ReleaseDC(None, dc)
        if logical_w > 0:
            return physical_w / logical_w
    return 1.0


ROOT = Path(__file__).resolve().parent.parent
SAMBERT_YAML = ROOT / "sambert_tts.yaml"
QWEN3_YAML = ROOT / "qwen3_tts.yaml"
EDGE_TTS_YAML = ROOT / "edge_tts.yaml"
API_KEY_YAML = ROOT / "bailian_api_key.yaml"
USER_SETTING_YAML = ROOT / "user_setting.yaml"
# user_setting.yaml 内界面小节：``ui: { font_point_size: ... }``
USER_SETTING_UI_SECTION = "ui"
USER_SETTING_FONT_PT_FIELD = "font_point_size"

DEFAULT_TTS_TEXT = "白日依山尽，黄河入海流。欲穷千里目，更上一层楼。"

SAMBERT_DEFAULT_OUTPUT_FILENAME = "sambert_tts.wav"
QWEN3_DEFAULT_OUTPUT_FILENAME = "qwen3_tts.wav"
EDGE_DEFAULT_OUTPUT_FILENAME = "edge_tts.mp3"

# WebSocket：握手超时、相邻两条消息之间的最长等待（秒）。任一超时则中止并提示。
TTS_WEBSOCKET_OPEN_TIMEOUT_SEC = 20.0
TTS_WEBSOCKET_RECV_TIMEOUT_SEC = 20.0

# 两 TTS Tab：合成 / 播放 / 波形同一行，高度随 DPI 缩放（与 stylesheet #actionBarRow 一致）
_DPI_SCALE = get_dpi_scale()
TTS_ACTION_BAR_HEIGHT_PX = int(40 * _DPI_SCALE)

DEFAULT_UI_FONT_PT = 12
UI_FONT_PT_MIN = 8
UI_FONT_PT_MAX = 24

UI_FONT_FAMILY_QSS = '"Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", sans-serif'
UI_BG_PANEL_HEX = 0xF5F9FF
UI_BG_PANEL = f"#{UI_BG_PANEL_HEX:06x}"
UI_TAB_BG_INACTIVE = "#e3edf9"
UI_TAB_BG_ACTIVE = "#c4d9f0"
UI_WINDOW_BG = "#f0f7ff"

# GtArrowComboBox / GlyphSpinBox 自绘三角：固定像素，不随全局字号与控件高度缩放
UI_ARROW_GLYPH_BASE_PX = 8
UI_ARROW_GLYPH_HEIGHT_PX = 6
