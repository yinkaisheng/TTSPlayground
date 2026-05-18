# -- coding: utf-8 --
"""Qwen3 TTS Realtime 标签页（WebSocket，对齐 ``qwen3_tts_api_cli.py`` 流程）。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PyQt5 import sip
from PyQt5.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from gui.config import load_api_key_entries, load_qwen3_yaml, mask_api_key, qwen3_voice_description
from gui.constants import (
    API_KEY_YAML,
    DEFAULT_TTS_TEXT,
    QWEN3_DEFAULT_OUTPUT_FILENAME,
    ROOT,
    QWEN3_YAML,
    TTS_ACTION_BAR_HEIGHT_PX,
    TTS_WEBSOCKET_OPEN_TIMEOUT_SEC,
    TTS_WEBSOCKET_RECV_TIMEOUT_SEC,
)
from gui.format_log import format_log_line
from gui.miniaudio_player import MiniAudioPlayer
import gui.play_time_format as ptf
from gui.waveform import WaveformLoadThread, WaveformWidget, waveform_target_bars_for_widget
from gui.widgets import GtArrowComboBox, SingleLineElidingInfoLabel
from qwen3_tts_ws import SessionMode, synthesize_qwen3_realtime_to_file


@dataclass
class Qwen3SynthesisArgs:
    ws_url: str
    api_key: str
    voice: str
    mode: SessionMode
    language_type: str
    text: str
    output_path: Path
    sample_rate: int
    instructions: str | None


class Qwen3SynthesisWorker(QThread):
    finished_ok = pyqtSignal(str)
    failed = pyqtSignal(str)
    log_line = pyqtSignal(str)

    def __init__(self, args: Qwen3SynthesisArgs) -> None:
        super().__init__()
        self._args = args

    def run(self) -> None:
        import asyncio

        a = self._args

        def emit_log(line: str) -> None:
            self.log_line.emit(format_log_line(line))

        try:
            asyncio.run(
                synthesize_qwen3_realtime_to_file(
                    ws_url=a.ws_url,
                    api_key=a.api_key,
                    voice=a.voice,
                    mode=a.mode,
                    language_type=a.language_type,
                    text=a.text,
                    output_path=a.output_path,
                    sample_rate=a.sample_rate,
                    log=emit_log,
                    instructions=a.instructions,
                )
            )
            self.finished_ok.emit(str(a.output_path.resolve()))
        except TimeoutError as e:
            self.log_line.emit(format_log_line(f"合成超时: {e}"))
            self.failed.emit(str(e))
        except Exception as e:
            self.log_line.emit(format_log_line(f"合成中断（异常）: {e}"))
            self.failed.emit(str(e))


class Qwen3TtsTab(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("qwen3TabRoot")
        self._cfg = load_qwen3_yaml(QWEN3_YAML)
        self._models: list[dict[str, Any]] = list(self._cfg.get("models") or [])
        self._voices: list[dict[str, Any]] = list(self._cfg.get("voices") or [])
        self._voice_by_id: dict[str, dict[str, Any]] = {}
        for v in self._voices:
            vid = str(v.get("voice_id", "")).strip()
            if vid:
                self._voice_by_id[vid] = v
        self._synth_worker: Qwen3SynthesisWorker | None = None
        self._last_output: Path | None = None
        self._play_cycle_pending = False
        self._seek_display_pos_ms: int | None = None
        self._audio_duration_sec = 0.0
        self._waveform_gen = 0
        self._waveform_thread: WaveformLoadThread | None = None
        self._player = MiniAudioPlayer(self)
        self._player.setNotifyInterval(50)
        self._player.finished.connect(self._on_playback_finished)
        self._player.error_occurred.connect(self._on_playback_error)
        self._player.durationChanged.connect(self._on_duration_changed)
        self._player.positionChanged.connect(self._on_media_position_changed)

        self._play_time_timer = QTimer(self)
        self._play_time_timer.setInterval(250)
        self._play_time_timer.timeout.connect(self._tick_play_time_display)

        self._build_ui()
        self._wire_description_updates()
        QTimer.singleShot(0, self._bootstrap_existing_audio_file)

    def collect_user_tab_settings(self) -> dict[str, Any]:
        """汇总当前页控件状态，供写入 ``user_setting.yaml``。"""
        entries = load_api_key_entries(API_KEY_YAML)
        idx = int(self.api_key_combo.currentIndex())
        key_name = ""
        if 0 <= idx < len(entries):
            key_name = str(entries[idx].get("key_name", "default"))
        mode_raw = self.mode_combo.currentData()
        if isinstance(mode_raw, SessionMode):
            mode_s = mode_raw.value
        else:
            mode_s = str(mode_raw or SessionMode.COMMIT.value)
        sr_raw = self.sample_rate_combo.currentData()
        try:
            sample_rate_hz = int(sr_raw) if sr_raw is not None else 24000
        except (TypeError, ValueError):
            sample_rate_hz = 24000
        return {
            "realtime_base_url": self.base_url_edit.text(),
            "api_key_key_name": key_name,
            "api_key_index": idx,
            "text": self.text_edit.toPlainText(),
            "model_id": self._current_model_id(),
            "voice_id": self._current_voice_id(),
            "language_code": self._current_language_code(),
            "session_mode": mode_s,
            "sample_rate_hz": sample_rate_hz,
            "output_filename": self.output_name_edit.text(),
            "instructions": self.instructions_edit.text(),
        }

    def apply_user_tab_settings(self, data: dict[str, Any] | None) -> None:
        """从 ``user_setting.yaml`` 恢复本页控件；字段缺失或非法时静默跳过。"""
        if not isinstance(data, dict):
            return
        base = data.get("realtime_base_url")
        if isinstance(base, str):
            self.base_url_edit.setText(base)
        text = data.get("text")
        if isinstance(text, str):
            self.text_edit.setPlainText(text)
        out_fn = data.get("output_filename")
        if isinstance(out_fn, str):
            self.output_name_edit.setText(out_fn)

        entries = load_api_key_entries(API_KEY_YAML)
        key_name = str(data.get("api_key_key_name", "") or "").strip()
        api_idx = -1
        if key_name:
            for i, ent in enumerate(entries):
                if str(ent.get("key_name", "")).strip() == key_name:
                    api_idx = i
                    break
        if api_idx < 0:
            try:
                api_idx = int(data.get("api_key_index", 0))
            except (TypeError, ValueError):
                api_idx = 0
        if self.api_key_combo.count() > 0:
            api_idx = max(0, min(api_idx, self.api_key_combo.count() - 1))
            self.api_key_combo.setCurrentIndex(api_idx)

        mid = str(data.get("model_id", "") or "").strip()
        if mid:
            mi = self.model_combo.findData(mid)
            if mi >= 0:
                self.model_combo.setCurrentIndex(mi)

        vid = str(data.get("voice_id", "") or "").strip()
        lang = str(data.get("language_code", "") or "").strip()
        self._refresh_voice_combo_for_model(vid, lang)

        mode_s = str(data.get("session_mode", "") or "").strip().lower()
        mode_enum: SessionMode | None = None
        if mode_s == SessionMode.SERVER_COMMIT.value:
            mode_enum = SessionMode.SERVER_COMMIT
        elif mode_s == SessionMode.COMMIT.value:
            mode_enum = SessionMode.COMMIT
        if mode_enum is not None:
            mx = self.mode_combo.findData(mode_enum)
            if mx >= 0:
                self.mode_combo.setCurrentIndex(mx)

        sr_raw = data.get("sample_rate_hz")
        if sr_raw is not None:
            try:
                sr = int(sr_raw)
                sx = self.sample_rate_combo.findData(sr)
                if sx >= 0:
                    self.sample_rate_combo.setCurrentIndex(sx)
            except (TypeError, ValueError):
                pass

        instructions_val = data.get("instructions")
        if isinstance(instructions_val, str):
            self.instructions_edit.setText(instructions_val)

        self._refresh_ws_url_preview()

    def _resolved_ws_url(self) -> str:
        base = self.base_url_edit.text().strip().rstrip("/")
        mid = self._current_model_id()
        if not base:
            return ""
        if not mid:
            return base
        return f"{base}?model={mid}" if "?" not in base else f"{base}&model={mid}"

    def _current_output_path(self) -> Path:
        out = Path(self.output_name_edit.text().strip() or QWEN3_DEFAULT_OUTPUT_FILENAME)
        if not out.is_absolute():
            out = ROOT / out
        return out.resolve()

    def _bootstrap_existing_audio_file(self) -> None:
        path = self._current_output_path()
        if not path.is_file():
            return
        ext = path.suffix.lower()
        if ext not in (".wav", ".mp3"):
            return
        self._last_output = path
        self.play_btn.setEnabled(True)
        self.play_btn.setToolTip("使用 miniaudio 播放已保存的 WAV/MP3。")
        self._sync_action_row_tail(show_waveform=True)
        self._player.load(path)
        self._start_waveform_load(path)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        conn_card = QFrame()
        conn_card.setObjectName("card")
        conn_layout = QVBoxLayout(conn_card)
        header_row = QHBoxLayout()
        header_conn = QLabel("🔗 连接与 API")
        header_conn.setObjectName("cardSectionTitle")
        header_row.addWidget(header_conn)
        self.model_info_label = SingleLineElidingInfoLabel(conn_card)
        info_txt = str(self._cfg.get("info") or "").strip()
        self.model_info_label.set_full_text(info_txt)
        self.model_info_label.setVisible(bool(info_txt))
        header_row.addWidget(self.model_info_label, 1)
        usage_url = str(self._cfg.get("model_usage") or "").strip()
        usage_lbl = QLabel()
        usage_lbl.setObjectName("cardSectionUsageLink")
        if usage_url:
            usage_lbl.setText(f'<a href="{usage_url}">模型用量</a>')
            usage_lbl.setOpenExternalLinks(True)
            usage_lbl.setToolTip(usage_url)
        else:
            usage_lbl.setVisible(False)
        header_row.addWidget(usage_lbl, 0, Qt.AlignVCenter)
        conn_layout.addLayout(header_row)

        conn_inputs = QHBoxLayout()
        url_vbox = QVBoxLayout()
        url_vbox.addWidget(QLabel("Realtime 基础地址（不含 ?model=）"))
        self.base_url_edit = QLineEdit(str(self._cfg.get("realtime_base_url") or "").strip())
        self.base_url_edit.setPlaceholderText(
            "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
        )
        self.base_url_edit.textChanged.connect(self._refresh_ws_url_preview)
        url_vbox.addWidget(self.base_url_edit)
        conn_inputs.addLayout(url_vbox, 2)

        api_vbox = QVBoxLayout()
        api_vbox.addWidget(QLabel("百炼 / DashScope Key"))
        self.api_key_combo = GtArrowComboBox()
        for ent in load_api_key_entries(API_KEY_YAML):
            name = str(ent.get("key_name", "default"))
            key = str(ent.get("bailian_api_key", "")).strip()
            label = f"{name} — {mask_api_key(key)}"
            self.api_key_combo.addItem(label, key)
        if self.api_key_combo.count() == 0:
            self.api_key_combo.addItem("（未配置密钥）", "")
        api_vbox.addWidget(self.api_key_combo)
        conn_inputs.addLayout(api_vbox, 1)
        conn_layout.addLayout(conn_inputs)

        self.ws_preview_label = QLabel("")
        self.ws_preview_label.setObjectName("voiceDesc")
        self.ws_preview_label.setWordWrap(True)
        conn_layout.addWidget(self.ws_preview_label)
        layout.addWidget(conn_card)

        middle_layout = QHBoxLayout()
        middle_layout.setSpacing(10)

        synth_card = QFrame()
        synth_card.setObjectName("card")
        synth_vbox = QVBoxLayout(synth_card)
        header_synth = QLabel("🎙 合成内容")
        header_synth.setObjectName("cardSectionTitle")
        synth_vbox.addWidget(header_synth)

        synth_vbox.addWidget(QLabel("合成文本"))
        self.text_edit = QTextEdit()
        self.text_edit.setObjectName("ttsText")
        self.text_edit.setPlainText(DEFAULT_TTS_TEXT)
        self.text_edit.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.text_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        synth_vbox.addWidget(self.text_edit, 1)

        model_row = QHBoxLayout()
        model_row.addWidget(QLabel("模型"), 0, Qt.AlignVCenter)
        self.model_combo = GtArrowComboBox()
        self.model_combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.model_combo.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        for m in self._models:
            mid = str(m.get("model_id", "")).strip()
            if not mid:
                continue
            self.model_combo.addItem(mid, mid)
        self.model_combo.updateGeometry()
        model_row.addWidget(self.model_combo, 0)
        self.model_desc_label = QLabel("")
        self.model_desc_label.setObjectName("voiceDesc")
        self.model_desc_label.setWordWrap(True)
        self.model_desc_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        model_row.addWidget(self.model_desc_label, 1)
        synth_vbox.addLayout(model_row)

        voice_row = QHBoxLayout()
        voice_row.addWidget(QLabel("音色"), 0, Qt.AlignVCenter)
        self.voice_combo = GtArrowComboBox()
        self.voice_combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.voice_combo.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        voice_row.addWidget(self.voice_combo, 0)
        self.voice_desc_label = QLabel("")
        self.voice_desc_label.setObjectName("voiceDesc")
        self.voice_desc_label.setWordWrap(True)
        self.voice_desc_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        voice_row.addWidget(self.voice_desc_label, 1)
        synth_vbox.addLayout(voice_row)

        lang_row = QHBoxLayout()
        lang_row.addWidget(QLabel("语言"), 0, Qt.AlignVCenter)
        self.language_combo = GtArrowComboBox()
        lang_row.addWidget(self.language_combo, 1)
        synth_vbox.addLayout(lang_row)

        middle_layout.addWidget(synth_card, 2)

        param_card = QFrame()
        param_card.setObjectName("card")
        param_card.setFixedWidth(380)
        param_vbox = QVBoxLayout(param_card)
        header_param = QLabel("⚙ 参数")
        header_param.setObjectName("cardSectionTitle")
        param_vbox.addWidget(header_param)

        param_vbox.addWidget(QLabel("会话模式 session.mode"))
        self.mode_combo = GtArrowComboBox()
        for sm in list(self._cfg.get("session_modes") or []):
            if not isinstance(sm, dict):
                continue
            sid = str(sm.get("id", "")).strip()
            if sid == "commit":
                mode_enum = SessionMode.COMMIT
            elif sid == "server_commit":
                mode_enum = SessionMode.SERVER_COMMIT
            else:
                continue
            lbl = str(sm.get("label", sid))
            self.mode_combo.addItem(lbl, mode_enum)
        if self.mode_combo.count() == 0:
            self.mode_combo.addItem("commit", SessionMode.COMMIT)
            self.mode_combo.addItem("server_commit", SessionMode.SERVER_COMMIT)
        param_vbox.addWidget(self.mode_combo)

        param_vbox.addSpacing(8)
        param_vbox.addWidget(QLabel("采样率"))
        self.sample_rate_combo = GtArrowComboBox()
        for row in list(self._cfg.get("sample_rates") or []):
            try:
                r = int(row)
            except (TypeError, ValueError):
                continue
            lab = f"{r} Hz"
            self.sample_rate_combo.addItem(lab, r)
        if self.sample_rate_combo.count() == 0:
            self.sample_rate_combo.addItem("16000 Hz", 16000)
        idx_16k = self.sample_rate_combo.findData(16000)
        if idx_16k >= 0:
            self.sample_rate_combo.setCurrentIndex(idx_16k)
        param_vbox.addWidget(self.sample_rate_combo)

        param_vbox.addSpacing(8)
        self.instructions_label = QLabel("语音指令 instructions")
        param_vbox.addWidget(self.instructions_label)
        self.instructions_edit = QLineEdit()
        self.instructions_edit.setPlaceholderText("描述语速、语调、情感等…")
        param_vbox.addWidget(self.instructions_edit)
        self.instructions_label.hide()
        self.instructions_edit.hide()

        param_vbox.addSpacing(8)
        param_vbox.addWidget(QLabel("保存文件名(wav/mp3)"))
        default_name = str(self._cfg.get("default_output_filename") or QWEN3_DEFAULT_OUTPUT_FILENAME).strip()
        self.output_name_edit = QLineEdit(default_name or QWEN3_DEFAULT_OUTPUT_FILENAME)
        param_vbox.addWidget(self.output_name_edit)

        param_vbox.addStretch()
        middle_layout.addWidget(param_card)
        layout.addLayout(middle_layout, 0)

        self.synth_btn = QPushButton("▶ 开始合成")
        self.synth_btn.setObjectName("primaryBtn")
        self.synth_btn.clicked.connect(self._on_synthesize)
        self.play_btn = QPushButton("🔊 播放")
        self.play_btn.setObjectName("secondaryBtn")
        self.play_btn.setEnabled(False)
        self.play_btn.clicked.connect(self._on_play)
        h_bar = int(TTS_ACTION_BAR_HEIGHT_PX)
        self._action_bar = QWidget()
        self._action_bar.setObjectName("actionBarRow")
        self._action_bar.setFixedHeight(h_bar)
        self._action_bar.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._action_row_layout = QHBoxLayout(self._action_bar)
        self._action_row_layout.setContentsMargins(0, 0, 0, 0)
        self._action_row_layout.setSpacing(8)
        self.synth_btn.setFixedHeight(h_bar)
        self.play_btn.setFixedHeight(h_bar)
        self._action_row_layout.addWidget(self.synth_btn)
        self._action_row_layout.addWidget(self.play_btn)
        self.waveform_canvas = WaveformWidget(self._action_bar)
        self.waveform_canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.waveform_canvas.seek_requested.connect(self._on_waveform_seek)
        self.waveform_canvas.hide()
        self._sync_action_row_tail(show_waveform=False)

        layout.addWidget(self._action_bar)

        log_card = QFrame()
        log_card.setObjectName("card")
        log_layout = QVBoxLayout(log_card)
        log_header = QHBoxLayout()
        log_header.addWidget(QLabel("📟 日志"))
        log_header.addStretch()
        self.status_label = QLabel("就绪")
        self.status_label.setObjectName("statusMuted")
        log_header.addWidget(self.status_label)
        log_layout.addLayout(log_header)

        self.ws_log_edit = QTextEdit()
        self.ws_log_edit.setObjectName("wsLog")
        self.ws_log_edit.setMinimumHeight(100)
        self.ws_log_edit.setReadOnly(False)
        self.ws_log_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.ws_log_edit.setPlaceholderText(
            "连接、session.update、文本缓冲与响应事件将显示于此…"
        )
        self.ws_log_edit.document().setMaximumBlockCount(4000)
        log_layout.addWidget(self.ws_log_edit, 1)
        layout.addWidget(log_card, 1)

        self._refresh_ws_url_preview()

    def _refresh_ws_url_preview(self) -> None:
        full = self._resolved_ws_url()
        self.ws_preview_label.setText(f"实际请求 URL：{full}" if full else "")

    def _ws_log_append_line(self, line: str) -> None:
        self.ws_log_edit.append(line)
        sb = self.ws_log_edit.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _current_model_id(self) -> str:
        combo = getattr(self, "model_combo", None)
        if combo is None:
            return ""
        i = combo.currentIndex()
        if i < 0:
            return ""
        d = combo.currentData()
        return str(d).strip() if d else ""

    def _current_voice_id(self) -> str:
        i = self.voice_combo.currentIndex()
        if i < 0:
            return ""
        d = self.voice_combo.currentData()
        return str(d).strip() if d else ""

    def _current_language_code(self) -> str:
        i = self.language_combo.currentIndex()
        if i < 0:
            return ""
        d = self.language_combo.currentData()
        return str(d).strip() if d else ""

    def _current_model_row(self) -> dict[str, Any] | None:
        i = self.model_combo.currentIndex()
        if i < 0 or i >= len(self._models):
            return None
        return self._models[i]

    def _current_voice_row(self) -> dict[str, Any] | None:
        vid = self.voice_combo.currentData()
        if not vid or not str(vid).strip():
            return None
        return self._voice_by_id.get(str(vid).strip())

    def _refresh_voice_combo_for_model(
        self, preferred_voice_id: str = "", preferred_lang_code: str = ""
    ) -> None:
        pv = (preferred_voice_id or "").strip()
        pl = (preferred_lang_code or "").strip()
        self.voice_combo.blockSignals(True)
        self.voice_combo.clear()
        row = self._current_model_row()
        if row:
            allowed = [str(x).strip() for x in (row.get("voices") or []) if str(x).strip()]
            for vid in allowed:
                v = self._voice_by_id.get(vid)
                if not v:
                    continue
                name = str(v.get("name", vid))
                self.voice_combo.addItem(f"{vid} {name}", vid)
        if self.voice_combo.count() > 0:
            idx = self.voice_combo.findData(pv) if pv else -1
            self.voice_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.voice_combo.blockSignals(False)
        self.voice_combo.updateGeometry()
        self._update_voice_description()
        self._refresh_language_combo_for_voice(pl)

    def _refresh_language_combo_for_voice(self, preferred_lang_code: str = "") -> None:
        pref = (preferred_lang_code or "").strip()
        self.language_combo.blockSignals(True)
        self.language_combo.clear()
        vr = self._current_voice_row()
        if vr:
            for lang in list(vr.get("voice_languages") or []):
                if not isinstance(lang, dict):
                    continue
                code = str(lang.get("code", "")).strip()
                if not code:
                    continue
                label = str(lang.get("label", code))
                self.language_combo.addItem(label, code)
        self.language_combo.blockSignals(False)
        if self.language_combo.count() == 0:
            self.language_combo.addItem("Chinese", "Chinese")
        elif pref:
            li = self.language_combo.findData(pref)
            self.language_combo.setCurrentIndex(li if li >= 0 else 0)
        else:
            self.language_combo.setCurrentIndex(0)

    def _wire_description_updates(self) -> None:
        self.model_combo.currentIndexChanged.connect(self._on_model_selection_changed)
        self.voice_combo.currentIndexChanged.connect(self._on_voice_selection_changed)
        self.model_combo.currentIndexChanged.connect(self._refresh_ws_url_preview)
        self._on_model_selection_changed()

    def _on_model_selection_changed(self, *_args: Any) -> None:
        preferred_voice = self._current_voice_id()
        preferred_lang = self._current_language_code()
        self._update_model_description()
        self._refresh_voice_combo_for_model(preferred_voice, preferred_lang)
        self._refresh_instructions_visibility()

    def _refresh_instructions_visibility(self) -> None:
        mid = self._current_model_id().lower()
        show = "instruct" in mid
        self.instructions_label.setVisible(show)
        self.instructions_edit.setVisible(show)

    def _on_voice_selection_changed(self, *_args: Any) -> None:
        preferred_lang = self._current_language_code()
        self._update_voice_description()
        self._refresh_language_combo_for_voice(preferred_lang)

    def _update_model_description(self) -> None:
        row = self._current_model_row()
        if row:
            self.model_desc_label.setText(str(row.get("note") or ""))
        else:
            self.model_desc_label.setText("未加载模型列表，请检查 qwen3_tts.yaml")

    def _update_voice_description(self) -> None:
        row = self._current_voice_row()
        if row:
            self.voice_desc_label.setText(qwen3_voice_description(row))
        else:
            self.voice_desc_label.setText("未加载音色配置，请检查 qwen3_tts.yaml")

    def _sync_action_row_tail(self, *, show_waveform: bool) -> None:
        lay = self._action_row_layout
        while lay.count() > 2:
            item = lay.takeAt(2)
            if item is not None:
                sip.delete(item)
        if show_waveform:
            lay.addWidget(self.waveform_canvas, 1)
            self.waveform_canvas.setMinimumWidth(160)
            self.waveform_canvas.setMaximumWidth(16777215)
            self.waveform_canvas.setFixedHeight(int(TTS_ACTION_BAR_HEIGHT_PX))
            self.waveform_canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self.waveform_canvas.show()
        else:
            lay.addStretch(1)
            self.waveform_canvas.hide()

    def _trusted_total_ms(self) -> int:
        return ptf.trusted_total_ms(self._audio_duration_sec)

    def _playback_ratio(self) -> float:
        td = self._trusted_total_ms()
        pos = self._player.position()
        dur = self._player.duration()
        return ptf.waveform_progress_ratio(td, pos, dur, None, None)

    def _elapsed_display_ms(self) -> int:
        return ptf.elapsed_display_ms(
            self._seek_display_pos_ms,
            self._trusted_total_ms(),
            self._playback_ratio(),
            self._player.position(),
        )

    def _total_display_ms(self) -> int:
        return ptf.total_display_ms(
            self._trusted_total_ms(),
            self._player.duration(),
            self._player.position(),
        )

    def _play_time_total_label_text(self) -> str:
        """状态栏右侧总时长文案：优先用波形线程给出的秒浮点，与其它 Tab 一位小数样式一致。"""
        if self._audio_duration_sec > 0:
            return ptf.fmt_mmss_tenth_from_seconds(self._audio_duration_sec)
        tot_ms = self._total_display_ms()
        return ptf.fmt_mmss_tenth(tot_ms) if tot_ms > 0 else "--:--"

    def _refresh_play_time_label(self) -> None:
        el = self._elapsed_display_ms()
        tot_txt = self._play_time_total_label_text()
        if tot_txt == "--:--":
            self.status_label.setText(f"{ptf.fmt_mmss_no_fraction(el)}/--:--")
        else:
            self.status_label.setText(
                f"{ptf.fmt_mmss_no_fraction(el)}/{tot_txt}"
            )

    def _tick_play_time_display(self) -> None:
        if not self._play_cycle_pending:
            self._play_time_timer.stop()
            return
        self._refresh_play_time_label()

    def _on_duration_changed(self, duration_ms: int) -> None:
        del duration_ms
        if self._play_cycle_pending:
            self._refresh_play_time_label()
        self._on_media_position_changed(int(self._player.position()))

    def _on_media_position_changed(self, position_ms: int) -> None:
        pos = int(position_ms)
        sk = self._seek_display_pos_ms
        trusted = self._trusted_total_ms()
        if sk is not None:
            if abs(pos - sk) <= 250:
                self._seek_display_pos_ms = None
                self.waveform_canvas.set_playhead(self._playback_ratio())
            elif trusted > 0:
                self.waveform_canvas.set_playhead(max(0.0, min(1.0, float(sk) / float(trusted))))
            self._refresh_play_time_label()
            return
        self.waveform_canvas.set_playhead(self._playback_ratio())
        if self._play_cycle_pending:
            self._refresh_play_time_label()

    def _on_waveform_seek(self, ratio: float) -> None:
        if not self._last_output or not self._last_output.is_file():
            return
        if self._last_output.suffix.lower() not in (".wav", ".mp3"):
            return
        r = max(0.0, min(1.0, float(ratio)))
        trusted = self._trusted_total_ms()
        dur = max(0, self._player.duration())
        if trusted > 0:
            pos_ms = int(round(r * trusted))
        elif dur > 0:
            pos_ms = int(round(r * dur))
        else:
            return
        moving = self._player.state() == 1  # Playing
        self._seek_display_pos_ms = pos_ms
        self._player.setPosition(pos_ms)
        self.waveform_canvas.set_playhead(r)
        self._refresh_play_time_label()
        if not moving:
            self._play_cycle_pending = True
            self.play_btn.setEnabled(False)
            self._player.play()
            self._play_time_timer.start()
            self._refresh_play_time_label()

    def _start_waveform_load(self, path: Path) -> None:
        self._waveform_gen += 1
        gen = self._waveform_gen
        self.waveform_canvas.clear()
        self._audio_duration_sec = 0.0
        bars = waveform_target_bars_for_widget(self.waveform_canvas)
        print(f"qwen3 bars: {bars}")
        thr = WaveformLoadThread(path.resolve(), target_bars=bars)

        def _on_done(peaks: list, dur_sec: float) -> None:
            if gen != self._waveform_gen:
                return
            self._audio_duration_sec = float(dur_sec)
            self.waveform_canvas.set_peaks(peaks)
            self._on_media_position_changed(int(self._player.position()))
            self._refresh_play_time_label()

        def _on_fail(msg: str) -> None:
            if gen != self._waveform_gen:
                return
            self._ws_log_append_line(format_log_line(f"波形加载失败: {msg}"))

        thr.done.connect(_on_done)
        thr.failed.connect(_on_fail)
        self._waveform_thread = thr
        thr.start()

    def _on_synthesize(self) -> None:
        ws_url = self._resolved_ws_url().strip()
        if not ws_url:
            QMessageBox.warning(self, "配置", "请填写 Realtime 基础地址并选择模型。")
            return
        key = self.api_key_combo.currentData()
        if not key or not str(key).strip():
            QMessageBox.warning(self, "配置", "请在 bailian_api_key.yaml 中配置有效的 bailian_api_key。")
            return
        text = self.text_edit.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, "输入", "请输入要合成的文本。")
            return
        voice = self._current_voice_id()
        if not voice:
            QMessageBox.warning(self, "配置", "没有可用音色（voice）。")
            return
        mode = self.mode_combo.currentData()
        if not isinstance(mode, SessionMode):
            mode = SessionMode.COMMIT
        lang = str(self.language_combo.currentData() or "Chinese").strip() or "Chinese"
        sr = int(self.sample_rate_combo.currentData() or 24000)

        out = Path(self.output_name_edit.text().strip() or QWEN3_DEFAULT_OUTPUT_FILENAME)
        if not out.is_absolute():
            out = ROOT / out
        if out.suffix.lower() not in (".wav", ".mp3"):
            QMessageBox.warning(
                self,
                "保存文件名",
                "Qwen3 Realtime 请使用扩展名 .wav 或 .mp3（将对应请求服务端 ``response_format`` 为 wav / mp3）。",
            )
            return

        instructions_val = self.instructions_edit.text().strip() or None

        args = Qwen3SynthesisArgs(
            ws_url=ws_url,
            api_key=str(key).strip(),
            voice=voice,
            mode=mode,
            language_type=lang,
            text=text,
            output_path=out,
            sample_rate=sr,
            instructions=instructions_val,
        )

        self.synth_btn.setEnabled(False)
        self.play_btn.setEnabled(False)
        self._play_cycle_pending = False
        self._play_time_timer.stop()
        self._seek_display_pos_ms = None
        self.waveform_canvas.clear()
        self._sync_action_row_tail(show_waveform=False)
        self._audio_duration_sec = 0.0
        self._waveform_gen += 1
        self._player.stop()
        self._player.unload()
        self.status_label.setText("正在合成（接收实时流并写入文件）…")
        self.ws_log_edit.append(format_log_line("—— 开始合成（Qwen3 Realtime）——"))
        self.ws_log_edit.append(
            format_log_line(
                f"WebSocket 超时：握手 {TTS_WEBSOCKET_OPEN_TIMEOUT_SEC:g}s，"
                f"相邻下行空闲 {TTS_WEBSOCKET_RECV_TIMEOUT_SEC:g}s（长音频可持续至服务端返回完毕）。"
            )
        )

        self._synth_worker = Qwen3SynthesisWorker(args)
        self._synth_worker.log_line.connect(self._ws_log_append_line)
        self._synth_worker.finished_ok.connect(self._on_synth_ok)
        self._synth_worker.failed.connect(self._on_synth_fail)
        self._synth_worker.start()

    def _on_synth_ok(self, path_str: str) -> None:
        self.synth_btn.setEnabled(True)
        self._last_output = Path(path_str)
        self.status_label.setText(f"完成：{path_str}")
        ext = self._last_output.suffix.lower()
        if ext in (".wav", ".mp3"):
            self.play_btn.setEnabled(True)
            self.play_btn.setToolTip("使用 miniaudio 播放已保存的 WAV/MP3。")
            self._sync_action_row_tail(show_waveform=True)
            self._player.load(self._last_output)
            self._start_waveform_load(self._last_output)
        else:
            self.play_btn.setEnabled(False)
            self._sync_action_row_tail(show_waveform=False)

    def _on_synth_fail(self, msg: str) -> None:
        self.synth_btn.setEnabled(True)
        if self._last_output and self._last_output.is_file():
            ext = self._last_output.suffix.lower()
            if ext in (".wav", ".mp3"):
                self.play_btn.setEnabled(True)
            else:
                self.play_btn.setEnabled(False)
        else:
            self.play_btn.setEnabled(False)
        self.status_label.setText("合成失败")
        QMessageBox.critical(self, "合成失败", msg)

    def _on_playback_finished(self) -> None:
        if not self._play_cycle_pending:
            return
        self._play_cycle_pending = False
        self._play_time_timer.stop()
        self._seek_display_pos_ms = None
        self.play_btn.setEnabled(True)
        tot_ms = self._total_display_ms()
        self.waveform_canvas.set_playhead(
            1.0 if tot_ms > 0 or self._audio_duration_sec > 0 else 0.0
        )
        tot_txt = self._play_time_total_label_text()
        if tot_txt != "--:--":
            self.status_label.setText(f"{tot_txt}/{tot_txt}")
        else:
            self.status_label.setText("播放结束")

    def _on_playback_error(self, msg: str) -> None:
        self._play_cycle_pending = False
        self._play_time_timer.stop()
        self._seek_display_pos_ms = None
        self.play_btn.setEnabled(True)
        self.status_label.setText("播放失败")
        QMessageBox.critical(self, "播放失败", msg)

    def _on_play(self) -> None:
        if not self._last_output or not self._last_output.is_file():
            QMessageBox.warning(self, "播放", "没有可播放的音频文件。")
            return
        ext = self._last_output.suffix.lower()
        if ext not in (".wav", ".mp3"):
            QMessageBox.warning(
                self,
                "播放",
                f"内置播放仅支持 WAV、MP3；当前为「{ext or '无扩展名'}」。",
            )
            return
        self._seek_display_pos_ms = None
        self.play_btn.setEnabled(False)
        self._refresh_play_time_label()
        self._player.load(self._last_output)
        self._play_cycle_pending = True
        self._player.play()
        self._play_time_timer.start()
