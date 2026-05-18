# -- coding: utf-8 --

from __future__ import annotations

import sys

from typing import Any

from PyQt5.QtCore import QSize, Qt
from PyQt5.QtGui import QCloseEvent, QIcon
from PyQt5.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QMainWindow,
    QStyle,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
)

from gui.config import USER_SETTINGS_FILE_VERSION, load_user_settings_yaml, save_user_settings_yaml
from gui.constants import (
    DEFAULT_UI_FONT_PT,
    TTS_WINDOW_ICON_PNG,
    UI_FONT_PT_MAX,
    UI_FONT_PT_MIN,
    USER_SETTING_FONT_PT_FIELD,
    USER_SETTING_UI_SECTION,
    USER_SETTING_YAML,
)
from gui.font_settings import apply_global_ui_font, read_saved_font_pt, write_saved_font_pt
from gui.tabs import EdgeTtsTab, Qwen3TtsTab, SambertTtsTab
from gui.widgets import GlyphSpinBox


class UiFontDialog(QDialog):
    """全局界面字号设置。"""

    def __init__(self, current_pt: int, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("uiFontDialog")
        self.setWindowTitle("界面显示设置")
        self.setModal(True)
        self.resize(360, 140)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self._spin = GlyphSpinBox()
        self._spin.setRange(UI_FONT_PT_MIN, UI_FONT_PT_MAX)
        self._spin.setValue(max(UI_FONT_PT_MIN, min(UI_FONT_PT_MAX, int(current_pt))))
        self._spin.setSuffix(" pt")
        form.addRow("全局字体大小", self._spin)

        hint = QLabel(
            "将应用到窗口内标签、输入框、按钮、分组标题等。"
            "字号与窗口、各 Tab 选项一并写入工程目录下的 user_setting.yaml，关闭窗口时也会保存。"
        )
        hint.setWordWrap(True)
        hint.setObjectName("statusMuted")

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout.addLayout(form)
        layout.addWidget(hint)
        layout.addWidget(buttons)

    def font_point_size(self) -> int:
        return int(self._spin.value())


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self._restore_maximized = False
        self.setWindowTitle("TTS Playground")
        if TTS_WINDOW_ICON_PNG.is_file():
            self.setWindowIcon(QIcon(str(TTS_WINDOW_ICON_PNG)))
        self.resize(1400, 900)
        tabs = QTabWidget()
        edge_tab = EdgeTtsTab()
        tabs.addTab(edge_tab, "Edge TTS")

        sambert_tab = SambertTtsTab()
        tabs.addTab(sambert_tab, "Sambert TTS")

        qwen3_tab = Qwen3TtsTab()
        tabs.addTab(qwen3_tab, "Qwen3 TTS")

        cfg_btn = QToolButton(tabs)
        cfg_btn.setObjectName("tabConfigButton")
        cfg_btn.setToolTip("界面字体大小")
        cfg_btn.setCursor(Qt.PointingHandCursor)
        cfg_btn.setAutoRaise(True)
        app_inst = QApplication.instance()
        pt_hint = 11
        if isinstance(app_inst, QApplication):
            ps = app_inst.font().pointSize()
            if ps > 0:
                pt_hint = ps
        ic_sz = max(20, min(36, int(pt_hint * 1.85)))
        cfg_btn.setIconSize(QSize(ic_sz, ic_sz))
        cfg_icon = QIcon.fromTheme("preferences-system")
        if cfg_icon.isNull():
            cfg_icon = self.style().standardIcon(QStyle.SP_FileDialogDetailedView)
        if cfg_icon.isNull():
            cfg_btn.setText("\u2699")
            cfg_btn.setToolButtonStyle(Qt.ToolButtonTextOnly)
        else:
            cfg_btn.setIcon(cfg_icon)
            cfg_btn.setToolButtonStyle(Qt.ToolButtonIconOnly)
        cfg_btn.clicked.connect(self._open_ui_font_dialog)
        tabs.setCornerWidget(cfg_btn, Qt.TopRightCorner)

        self.setCentralWidget(tabs)
        self._edge_tab = edge_tab
        self._sambert_tab = sambert_tab
        self._qwen3_tab = qwen3_tab
        self._apply_user_settings(load_user_settings_yaml(USER_SETTING_YAML))

    def _apply_user_settings(self, root: dict[str, Any]) -> None:
        """恢复窗口几何与各 Tab 控件状态。"""
        win = root.get("window")
        if isinstance(win, dict):
            try:
                x = int(win.get("x", -1))
                y = int(win.get("y", -1))
                w = int(win.get("width", -1))
                h = int(win.get("height", -1))
            except (TypeError, ValueError):
                x = y = w = h = -1
            maximized = bool(win.get("maximized"))
            self._restore_maximized = maximized
            if w >= 200 and h >= 200:
                self.setGeometry(x, y, w, h)

        tabs_block = root.get("tabs")
        if isinstance(tabs_block, dict):
            self._edge_tab.apply_user_tab_settings(
                tabs_block.get("edge_tts") if isinstance(tabs_block.get("edge_tts"), dict) else None
            )
            self._sambert_tab.apply_user_tab_settings(
                tabs_block.get("sambert_tts") if isinstance(tabs_block.get("sambert_tts"), dict) else None
            )
            self._qwen3_tab.apply_user_tab_settings(
                tabs_block.get("qwen3_tts") if isinstance(tabs_block.get("qwen3_tts"), dict) else None
            )

    def _persist_user_settings(self) -> None:
        app = QApplication.instance()
        font_pt = DEFAULT_UI_FONT_PT
        if isinstance(app, QApplication):
            ps = app.font().pointSize()
            if ps > 0:
                font_pt = ps
        font_pt = max(UI_FONT_PT_MIN, min(UI_FONT_PT_MAX, int(font_pt)))

        prev = load_user_settings_yaml(USER_SETTING_YAML)
        ui_prev = prev.get(USER_SETTING_UI_SECTION)
        ui_block = dict(ui_prev) if isinstance(ui_prev, dict) else {}
        ui_block[USER_SETTING_FONT_PT_FIELD] = font_pt

        geo = self.normalGeometry() if self.isMaximized() else self.geometry()
        payload = {
            "version": USER_SETTINGS_FILE_VERSION,
            USER_SETTING_UI_SECTION: ui_block,
            "window": {
                "x": geo.x(),
                "y": geo.y(),
                "width": max(200, geo.width()),
                "height": max(200, geo.height()),
                "maximized": self.isMaximized(),
            },
            "tabs": {
                "edge_tts": self._edge_tab.collect_user_tab_settings(),
                "sambert_tts": self._sambert_tab.collect_user_tab_settings(),
                "qwen3_tts": self._qwen3_tab.collect_user_tab_settings(),
            },
        }
        save_user_settings_yaml(USER_SETTING_YAML, payload)

    def closeEvent(self, event: QCloseEvent) -> None:
        try:
            self._persist_user_settings()
        except OSError:
            pass
        super().closeEvent(event)

    def _open_ui_font_dialog(self) -> None:
        app = QApplication.instance()
        if not isinstance(app, QApplication):
            return
        cur = app.font().pointSize()
        if cur <= 0:
            cur = read_saved_font_pt()
        dlg = UiFontDialog(cur, self)
        if dlg.exec_() == QDialog.Accepted:
            pt = dlg.font_point_size()
            write_saved_font_pt(pt)
            apply_global_ui_font(app, pt)


def main() -> None:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    if TTS_WINDOW_ICON_PNG.is_file():
        app.setWindowIcon(QIcon(str(TTS_WINDOW_ICON_PNG)))
    initial_pt = read_saved_font_pt()
    apply_global_ui_font(app, initial_pt)
    win = MainWindow()
    if win._restore_maximized:
        win.showMaximized()
    else:
        win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
