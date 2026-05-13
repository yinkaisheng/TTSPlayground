# -- coding: utf-8 --
"""各功能 Tab。新增 Tab 时请在此包下增加模块并在 ``run_tts_gui.MainWindow`` 中注册。"""

from gui.tabs.edge_tts import EdgeTtsTab
from gui.tabs.qwen3_tts import Qwen3TtsTab
from gui.tabs.sambert_tts import SambertTtsTab

__all__ = ["EdgeTtsTab", "Qwen3TtsTab", "SambertTtsTab"]
