# -- coding: utf-8 --
"""全局字号持久化与应用到 QApplication + 样式表。

读写工程根目录 ``user_setting.yaml`` 中的 ``ui.font_point_size``；缺少或非法时使用 ``DEFAULT_UI_FONT_PT``。
"""

from PyQt5.QtGui import QFont, QFontDatabase
from PyQt5.QtWidgets import QApplication

from gui.config import load_user_settings_yaml, save_user_settings_yaml
from gui.constants import (
    DEFAULT_UI_FONT_PT,
    UI_FONT_PT_MAX,
    UI_FONT_PT_MIN,
    USER_SETTING_FONT_PT_FIELD,
    USER_SETTING_UI_SECTION,
    USER_SETTING_YAML,
)
from gui.stylesheet import build_app_stylesheet


def read_saved_font_pt() -> int:
    root = load_user_settings_yaml(USER_SETTING_YAML)
    ui = root.get(USER_SETTING_UI_SECTION)
    if isinstance(ui, dict):
        v = ui.get(USER_SETTING_FONT_PT_FIELD)
        if v is not None:
            try:
                return max(UI_FONT_PT_MIN, min(UI_FONT_PT_MAX, int(v)))
            except (TypeError, ValueError):
                pass
    return max(UI_FONT_PT_MIN, min(UI_FONT_PT_MAX, DEFAULT_UI_FONT_PT))


def write_saved_font_pt(pt: int) -> None:
    clamped = max(UI_FONT_PT_MIN, min(UI_FONT_PT_MAX, int(pt)))
    root = dict(load_user_settings_yaml(USER_SETTING_YAML))
    ui_raw = root.get(USER_SETTING_UI_SECTION)
    ui = dict(ui_raw) if isinstance(ui_raw, dict) else {}
    ui[USER_SETTING_FONT_PT_FIELD] = clamped
    root[USER_SETTING_UI_SECTION] = ui
    save_user_settings_yaml(USER_SETTING_YAML, root)


def apply_global_ui_font(app: QApplication, font_pt: int) -> None:
    pt = max(UI_FONT_PT_MIN, min(UI_FONT_PT_MAX, int(font_pt)))
    db = QFontDatabase()
    fam = "Segoe UI"
    for candidate in ("Microsoft YaHei UI", "Microsoft YaHei"):
        if candidate in db.families():
            fam = candidate
            break
    app.setFont(QFont(fam, pt))
    app.setStyleSheet(build_app_stylesheet(pt))
