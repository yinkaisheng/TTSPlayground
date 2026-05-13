# -- coding: utf-8 --
"""全局 QSS（随界面字号动态计算 px/pt）。

沿用 Python f-string 而非静态 .qss 文件的原因：大量尺寸依赖运行时 ``font_pt`` 与其它常量插值；
若改为独立 .qss，需维护占位符 + 二次替换，调试成本更高。仅当样式基本固定时再拆 .qss 更合适。
"""

from gui.constants import (
    TTS_ACTION_BAR_HEIGHT_PX,
    UI_BG_PANEL,
    UI_FONT_FAMILY_QSS,
    UI_FONT_PT_MAX,
    UI_FONT_PT_MIN,
    UI_TAB_BG_ACTIVE,
    UI_TAB_BG_INACTIVE,
    UI_WINDOW_BG,
)


def build_app_stylesheet(font_pt: int) -> str:
    """界面样式（参考 beauty_tab.py 卡片布局配色），字号随设置缩放。"""
    pt = max(UI_FONT_PT_MIN, min(UI_FONT_PT_MAX, int(font_pt)))
    pt_hint = max(UI_FONT_PT_MIN, pt - 1)
    pt_card_title = max(14, min(20, pt + 4))
    tab_pad_v = max(6, min(20, int(8 + (pt - 10) * 0.7)))
    tab_pad_h = max(12, min(28, int(16 + (pt - 10) * 1.0)))
    field_pad_v = max(6, min(12, int(6 + (pt - 10) * 0.35)))
    field_pad_h = max(8, min(14, int(8 + (pt - 10) * 0.35)))
    line_px = max(14, int(round(pt * (96 / 72) * 1.22)))
    te_tts_4lines = 4 * line_px + 2 * field_pad_v + 10
    w_primary = max(14, min(20, int(14 + (pt - 9) * 0.4)))
    slider_groove_h = 6  # 与 QSlider::groove height 一致；手柄为正方形，border-radius=边长一半即圆形
    slide_handle_sz = w_primary + 1
    if slide_handle_sz % 2 == 1:
        slide_handle_sz += 1  # 奇数边长 + 半像素级圆角会在顶/底留一条平边
    slide_handle_mv_y = -int(round((slide_handle_sz - slider_groove_h) / 2))
    slider_horiz_min_h = slide_handle_sz + 4  # 保证整圆不被父布局裁掉上沿
    # SpinBox：纵向 padding 小于 QLineEdit，整体更矮；上下键不设固定 height，否则总高度略大时中间会露出一条空白缝。
    spin_pad_v = max(1, min(5, int(1 + (pt - 10) * 0.35)))
    spin_min_h = max(20, line_px + 2 * spin_pad_v + 6)
    ws_log_min = max(56, min(112, int(48 + (pt - 10) * 6)))
    action_bar_h = int(TTS_ACTION_BAR_HEIGHT_PX)

    return f"""
QWidget {{
    font-family: {UI_FONT_FAMILY_QSS};
    font-size: {pt}pt;
}}
QWidget#sambertTabRoot,
QWidget#qwen3TabRoot,
QWidget#edgeTabRoot {{
    background-color: {UI_WINDOW_BG};
}}
QMainWindow {{
    background-color: {UI_WINDOW_BG};
}}
QTabWidget::pane {{
    border: 1px solid #d0d7e2;
    border-radius: 8px;
    background-color: {UI_WINDOW_BG};
    top: -1px;
    padding: 0px;
}}
QTabBar::tab {{
    background: {UI_TAB_BG_INACTIVE};
    color: #1a1c1e;
    font-family: {UI_FONT_FAMILY_QSS};
    font-size: {pt}pt;
    padding: {tab_pad_v}px {tab_pad_h}px;
    margin-right: 4px;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    min-width: 100px;
}}
QTabBar::tab:selected {{
    background: {UI_TAB_BG_ACTIVE};
    color: #001d36;
}}
QToolButton#tabConfigButton {{
    background: transparent;
    border: none;
    border-radius: 6px;
    padding: 4px 8px;
    color: #475569;
    font-family: {UI_FONT_FAMILY_QSS};
    font-size: {max(pt, 14)}pt;
}}
QToolButton#tabConfigButton:hover {{
    background: #d1e4ff;
    color: #001d36;
}}
QLabel {{
    font-family: {UI_FONT_FAMILY_QSS};
    color: #1a1c1e;
}}
QLabel#cardSectionTitle {{
    font-weight: bold;
    font-size: {pt_card_title}pt;
    color: #1a1c1e;
    font-family: {UI_FONT_FAMILY_QSS};
}}
QLabel#cardSectionUsageLink {{
    font-family: {UI_FONT_FAMILY_QSS};
    font-size: {pt}pt;
}}
QLabel#cardSectionModelInfo {{
    font-family: {UI_FONT_FAMILY_QSS};
    font-size: {pt}pt;
    color: #5e6066;
}}
QLabel#cardSectionUsageLink a {{
    color: #0061a4;
    text-decoration: none;
}}
QLabel#cardSectionUsageLink a:hover {{
    text-decoration: underline;
}}
QLabel#voiceDesc {{
    background-color: {UI_BG_PANEL};
    border: 1px solid #e0e2ec;
    padding: 10px;
    border-radius: 4px;
    color: #5e6066;
    font-weight: normal;
    font-family: {UI_FONT_FAMILY_QSS};
}}
QLabel#paramValueAccent {{
    color: #0061a4;
    font-weight: bold;
    min-width: 40px;
    font-family: {UI_FONT_FAMILY_QSS};
}}
QLabel#statusMuted {{
    color: #5e6066;
    font-size: {pt_hint}pt;
    font-family: {UI_FONT_FAMILY_QSS};
}}
QLabel#tabPlaceholder {{
    color: #5e6066;
    font-size: {pt}pt;
    padding: 40px;
    font-family: {UI_FONT_FAMILY_QSS};
}}
QFrame#card {{
    background-color: {UI_BG_PANEL};
    border: 1px solid #e0e2ec;
    border-radius: 8px;
}}
QDialog#uiFontDialog {{
    background-color: {UI_WINDOW_BG};
}}
QDialog#uiFontDialog QDialogButtonBox {{
    background-color: transparent;
}}
QLineEdit, QTextEdit {{
    background-color: {UI_BG_PANEL};
    border: 1px solid #d9dadd;
    border-radius: 4px;
    padding: {field_pad_v}px {field_pad_h}px;
    font-size: {pt}pt;
    font-family: {UI_FONT_FAMILY_QSS};
    color: #1a1c1e;
    selection-background-color: #0061a4;
    selection-color: #ffffff;
}}
QComboBox {{
    background-color: {UI_BG_PANEL};
    border: 1px solid #d9dadd;
    border-radius: 4px;
    padding: {field_pad_v}px {field_pad_h}px;
    padding-right: 4px;
    font-size: {pt}pt;
    font-family: {UI_FONT_FAMILY_QSS};
    color: #1a1c1e;
    selection-background-color: #0061a4;
    selection-color: #ffffff;
}}
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 26px;
    border-left: 1px solid #b8c5d9;
    border-top-right-radius: 3px;
    border-bottom-right-radius: 3px;
    background-color: #d2deed;
}}
QComboBox::drop-down:hover {{
    background-color: #c5d4e8;
}}
QComboBox::down-arrow {{
    image: none;
    width: 14px;
    height: 14px;
}}
QSpinBox {{
    background-color: {UI_BG_PANEL};
    border: 1px solid #d9dadd;
    border-radius: 4px;
    padding: {spin_pad_v}px {field_pad_h}px;
    padding-right: 2px;
    min-height: {spin_min_h}px;
    font-size: {pt}pt;
    font-family: {UI_FONT_FAMILY_QSS};
    color: #1a1c1e;
}}
QSpinBox::up-button, QSpinBox::down-button {{
    subcontrol-origin: border;
    width: 20px;
    border-left: 1px solid #b8c5d9;
    background-color: #d2deed;
}}
QSpinBox::up-button {{
    subcontrol-position: top right;
    border-bottom: 1px solid #b8c5d9;
    border-top-right-radius: 3px;
}}
QSpinBox::up-button:hover {{
    background-color: #b9cce5;
}}
QSpinBox::down-button {{
    subcontrol-position: bottom right;
    border-bottom-right-radius: 3px;
}}
QSpinBox::down-button:hover {{
    background-color: #b9cce5;
}}
QSlider:horizontal {{
    min-height: {slider_horiz_min_h}px;
}}
QSlider::groove:horizontal {{
    border: 1px solid #bbb;
    height: 6px;
    background: #dee2e6;
    margin: 2px 0;
    border-radius: 3px;
}}
QSlider::handle:horizontal {{
    background-color: #0061a4;
    border: none;
    width: {slide_handle_sz}px;
    height: {slide_handle_sz}px;
    margin: {slide_handle_mv_y}px 0;
    border-radius: {slide_handle_sz // 2}px;
}}
QTextEdit#ttsText {{
    min-height: {te_tts_4lines}px;
}}
QTextEdit#wsLog {{
    min-height: {ws_log_min}px;
    background-color: {UI_BG_PANEL};
    border: none;
    font-family: \"Cascadia Mono\", \"Consolas\", \"Courier New\", \"Microsoft YaHei UI\", monospace;
    font-size: {max(8, pt - 1)}pt;
    color: #44474e;
}}
QWidget#actionBarRow {{
    min-height: {action_bar_h}px;
    max-height: {action_bar_h}px;
}}
QWidget#actionBarRow QPushButton#primaryBtn,
QWidget#actionBarRow QPushButton#secondaryBtn {{
    padding: 4px 14px;
    min-height: 1px;
    max-height: {action_bar_h}px;
}}
QWidget#actionBarRow QWidget#waveformCanvas {{
    min-height: 1px;
    max-height: {action_bar_h}px;
}}
QPushButton#primaryBtn {{
    background-color: #0061a4;
    color: white;
    border-radius: 4px;
    padding: 10px 20px;
    font-weight: bold;
    font-size: {pt}pt;
    font-family: {UI_FONT_FAMILY_QSS};
    border: none;
    min-height: 20px;
}}
QPushButton#primaryBtn:hover {{
    background-color: #004f87;
}}
QPushButton#primaryBtn:pressed {{
    background-color: #003d6b;
}}
QPushButton#primaryBtn:disabled {{
    background-color: #94a3b8;
    color: #e2e8f0;
}}
QPushButton#secondaryBtn {{
    background-color: #d1e4ff;
    color: #001d36;
    border-radius: 4px;
    padding: 10px 20px;
    font-size: {pt}pt;
    font-family: {UI_FONT_FAMILY_QSS};
    border: none;
    min-height: 20px;
}}
QPushButton#secondaryBtn:hover {{
    background-color: #b8d4ff;
}}
QPushButton#secondaryBtn:pressed {{
    background-color: #9ec5ff;
}}
QPushButton#secondaryBtn:disabled {{
    background-color: #e8ecf5;
    color: #9ca3af;
}}
"""
