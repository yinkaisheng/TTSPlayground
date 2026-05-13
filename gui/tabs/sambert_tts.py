# -- coding: utf-8 --
"""Sambert TTS 标签页（合成 / 波形 / 播放）。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PyQt5 import sip
from PyQt5.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSlider,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from gui.config import load_api_key_entries, load_sambert_yaml, mask_api_key, voice_description
from gui.constants import (
    API_KEY_YAML,
    DEFAULT_TTS_TEXT,
    ROOT,
    SAMBERT_DEFAULT_OUTPUT_FILENAME,
    SAMBERT_YAML,
    TTS_ACTION_BAR_HEIGHT_PX,
)
from gui.format_log import format_log_line
from gui.miniaudio_player import MiniAudioPlayer
import gui.play_time_format as ptf
from gui.waveform import WaveformLoadThread, WaveformWidget
from gui.widgets import GtArrowComboBox, SingleLineElidingInfoLabel
from sambert_tts_ws import synthesize_sambert_tts
from wav_repair import repair_wav_chunk_sizes


@dataclass
class SynthesisArgs:
    ws_url: str
    api_key: str
    model: str
    text: str
    output_path: Path
    audio_format: str
    sample_rate: int
    volume: int
    rate: float
    pitch: float
    word_timestamp_enabled: bool
    phoneme_timestamp_enabled: bool


class SynthesisWorker(QThread):
    finished_ok = pyqtSignal(str)
    failed = pyqtSignal(str)
    log_line = pyqtSignal(str)

    def __init__(self, args: SynthesisArgs) -> None:
        super().__init__()
        self._args = args

    def run(self) -> None:
        import asyncio

        a = self._args

        def emit_log(formatted_line: str) -> None:
            self.log_line.emit(formatted_line)

        try:
            asyncio.run(
                synthesize_sambert_tts(
                    a.ws_url,
                    a.api_key,
                    a.model,
                    a.text,
                    a.output_path,
                    audio_format=a.audio_format,
                    sample_rate=a.sample_rate,
                    volume=a.volume,
                    rate=a.rate,
                    pitch=a.pitch,
                    word_timestamp_enabled=a.word_timestamp_enabled,
                    phoneme_timestamp_enabled=a.phoneme_timestamp_enabled,
                    log=emit_log,
                )
            )
            self.finished_ok.emit(str(a.output_path.resolve()))
        except Exception as e:
            self.log_line.emit(format_log_line(f"合成中断（异常）: {e}"))
            self.failed.emit(str(e))


class SambertTtsTab(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("sambertTabRoot")
        self._cfg = load_sambert_yaml(SAMBERT_YAML)
        self._voices: list[dict[str, Any]] = list(self._cfg.get("voices") or [])
        self._synth_worker: SynthesisWorker | None = None
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
        self._wire_voice_description()
        self._update_rate_slider_label(self.rate_slider.value())
        self._update_pitch_slider_label(self.pitch_slider.value())
        QTimer.singleShot(0, self._bootstrap_existing_audio_file)

    @staticmethod
    def _fmt_slider_rate_pitch(v: int) -> str:
        """滑块刻度 v 对应实际值为 v/20（步进 0.05）。"""
        x = int(v) / 20.0
        return f"{x:.2f}".rstrip("0").rstrip(".")

    def _update_rate_slider_label(self, v: int) -> None:
        self.rate_value_label.setText(f"{self._fmt_slider_rate_pitch(int(v))}x")

    def _update_pitch_slider_label(self, v: int) -> None:
        self.pitch_value_label.setText(self._fmt_slider_rate_pitch(int(v)))

    def collect_user_tab_settings(self) -> dict[str, Any]:
        """汇总当前页控件状态，供写入 ``user_setting.yaml``。"""
        entries = load_api_key_entries(API_KEY_YAML)
        idx = int(self.api_key_combo.currentIndex())
        key_name = ""
        if 0 <= idx < len(entries):
            key_name = str(entries[idx].get("key_name", "default"))
        sr_raw = self.sample_rate_combo.currentData()
        try:
            sample_rate_hz = int(sr_raw)
        except (TypeError, ValueError):
            sample_rate_hz = 16000
        return {
            "ws_url": self.ws_url_edit.text(),
            "api_key_key_name": key_name,
            "api_key_index": idx,
            "text": self.text_edit.toPlainText(),
            "voice_id": str(self.voice_combo.currentData() or ""),
            "volume": int(self.volume_slider.value()),
            "rate": round(float(self.rate_slider.value()) / 20.0, 4),
            "pitch": round(float(self.pitch_slider.value()) / 20.0, 4),
            "sample_rate_hz": sample_rate_hz,
            "word_timestamp_enabled": bool(self.word_ts_cb.isChecked()),
            "phoneme_timestamp_enabled": bool(self.phoneme_ts_cb.isChecked()),
            "output_filename": self.output_name_edit.text(),
        }

    def apply_user_tab_settings(self, data: dict[str, Any] | None) -> None:
        """从 ``user_setting.yaml`` 恢复本页控件；字段缺失或非法时静默跳过。"""
        if not isinstance(data, dict):
            return
        ws_url = data.get("ws_url")
        if isinstance(ws_url, str):
            self.ws_url_edit.setText(ws_url)
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

        vid = str(data.get("voice_id", "") or "").strip()
        if vid:
            ix = self.voice_combo.findData(vid)
            if ix >= 0:
                self.voice_combo.setCurrentIndex(ix)

        try:
            vol = int(data["volume"])
            self.volume_slider.setValue(max(0, min(100, vol)))
        except (KeyError, TypeError, ValueError):
            pass
        try:
            rf = float(data["rate"])
            ri = int(round(rf * 20.0))
            self.rate_slider.setValue(max(10, min(40, ri)))
        except (KeyError, TypeError, ValueError):
            pass
        try:
            pf = float(data["pitch"])
            pi = int(round(pf * 20.0))
            self.pitch_slider.setValue(max(10, min(40, pi)))
        except (KeyError, TypeError, ValueError):
            pass

        sr_raw = data.get("sample_rate_hz")
        if sr_raw is not None:
            try:
                sr = int(sr_raw)
                six = self.sample_rate_combo.findData(sr)
                if six >= 0:
                    self.sample_rate_combo.setCurrentIndex(six)
            except (TypeError, ValueError):
                pass

        if "word_timestamp_enabled" in data:
            self.word_ts_cb.setChecked(bool(data["word_timestamp_enabled"]))
        if "phoneme_timestamp_enabled" in data:
            self.phoneme_ts_cb.setChecked(bool(data["phoneme_timestamp_enabled"]))

    def _current_output_path(self) -> Path:
        out = Path(self.output_name_edit.text().strip() or SAMBERT_DEFAULT_OUTPUT_FILENAME)
        if not out.is_absolute():
            out = ROOT / out
        return out.resolve()

    def _bootstrap_existing_audio_file(self) -> None:
        """启动时若保存文件名对应文件已存在，加载波形并允许试听。"""
        path = self._current_output_path()
        if not path.is_file():
            return
        ext = path.suffix.lower()
        if ext == ".wav":
            repair_wav_chunk_sizes(path)
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
            usage_lbl.setText(
                f'<a href="{usage_url}">模型用量</a>'
            )
            usage_lbl.setOpenExternalLinks(True)
            usage_lbl.setToolTip(usage_url)
        else:
            usage_lbl.setVisible(False)
        header_row.addWidget(usage_lbl, 0, Qt.AlignVCenter)
        conn_layout.addLayout(header_row)

        conn_inputs = QHBoxLayout()
        ws_vbox = QVBoxLayout()
        self.ws_url_label = QLabel("WebSocket 地址 (ws_url)")
        ws_vbox.addWidget(self.ws_url_label)
        self.ws_url_edit = QLineEdit(self._cfg.get("ws_url") or "")
        self.ws_url_edit.setPlaceholderText("wss://dashscope.aliyuncs.com/api-ws/v1/inference/")
        ws_vbox.addWidget(self.ws_url_edit)
        conn_inputs.addLayout(ws_vbox, 2)

        api_vbox = QVBoxLayout()
        self.bailian_api_key_label = QLabel("百炼 / DashScope Key")
        api_vbox.addWidget(self.bailian_api_key_label)
        self.api_key_combo = GtArrowComboBox()
        for ent in load_api_key_entries(API_KEY_YAML):
            name = str(ent.get("key_name", "default"))
            key = str(ent.get("bailian_api_key", "")).strip()
            label = f'{name} — {mask_api_key(key)}'
            self.api_key_combo.addItem(label, key)
        if self.api_key_combo.count() == 0:
            self.api_key_combo.addItem("（未配置密钥）", "")
        api_vbox.addWidget(self.api_key_combo)
        conn_inputs.addLayout(api_vbox, 1)
        conn_layout.addLayout(conn_inputs)
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

        model_hbox = QHBoxLayout()
        model_hbox.addWidget(QLabel("音色"), 0, Qt.AlignVCenter)
        self.voice_combo = GtArrowComboBox()
        for v in self._voices:
            vid = str(v.get("voice_id", ""))
            name = str(v.get("name", vid))
            self.voice_combo.addItem(f"{vid} {name}", vid)
        model_hbox.addWidget(self.voice_combo, 1)

        self.voice_desc_label = QLabel("")
        self.voice_desc_label.setObjectName("voiceDesc")
        self.voice_desc_label.setWordWrap(True)
        model_hbox.addWidget(self.voice_desc_label, 1)
        synth_vbox.addLayout(model_hbox)
        middle_layout.addWidget(synth_card, 2)

        param_card = QFrame()
        param_card.setObjectName("card")
        param_card.setFixedWidth(320)
        param_vbox = QVBoxLayout(param_card)
        header_param = QLabel("⚙ 参数")
        header_param.setObjectName("cardSectionTitle")
        param_vbox.addWidget(header_param)

        def _add_slider_row(label_text: str, initial_val: str) -> tuple[QHBoxLayout, QLabel]:
            hbox = QHBoxLayout()
            hbox.addWidget(QLabel(label_text))
            hbox.addStretch()
            val_lbl = QLabel(initial_val)
            val_lbl.setObjectName("paramValueAccent")
            val_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            hbox.addWidget(val_lbl)
            param_vbox.addLayout(hbox)
            return hbox, val_lbl

        _, self.volume_value_label = _add_slider_row("音量", "100")
        self.volume_slider = QSlider(Qt.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(100)
        self.volume_slider.setSingleStep(1)
        self.volume_slider.setPageStep(10)
        param_vbox.addWidget(self.volume_slider)
        self.volume_slider.valueChanged.connect(lambda v: self.volume_value_label.setText(str(v)))

        _, self.rate_value_label = _add_slider_row("语速 rate", "1x")
        self.rate_slider = QSlider(Qt.Horizontal)
        self.rate_slider.setRange(10, 40)
        self.rate_slider.setValue(20)
        self.rate_slider.setSingleStep(1)
        self.rate_slider.setPageStep(1)
        param_vbox.addWidget(self.rate_slider)
        self.rate_slider.valueChanged.connect(self._update_rate_slider_label)

        _, self.pitch_value_label = _add_slider_row("音调 pitch", "1")
        self.pitch_slider = QSlider(Qt.Horizontal)
        self.pitch_slider.setRange(10, 40)
        self.pitch_slider.setValue(20)
        self.pitch_slider.setSingleStep(1)
        self.pitch_slider.setPageStep(1)
        param_vbox.addWidget(self.pitch_slider)
        self.pitch_slider.valueChanged.connect(self._update_pitch_slider_label)

        param_vbox.addSpacing(10)
        param_vbox.addWidget(QLabel("采样率"))
        self.sample_rate_combo = GtArrowComboBox()
        for sr in ("8000", "16000", "24000", "32000", "48000"):
            self.sample_rate_combo.addItem(f"{sr} Hz", int(sr))
        self.sample_rate_combo.setCurrentIndex(1)
        param_vbox.addWidget(self.sample_rate_combo)

        param_vbox.addSpacing(10)
        self.word_ts_cb = QCheckBox("单词 / Word 时间戳")
        self.word_ts_cb.setToolTip("word_timestamp_enabled")
        self.phoneme_ts_cb = QCheckBox("音素 / Phoneme 时间戳")
        self.phoneme_ts_cb.setToolTip("phoneme_timestamp_enabled")
        param_vbox.addWidget(self.word_ts_cb)
        param_vbox.addWidget(self.phoneme_ts_cb)

        param_vbox.addSpacing(10)
        param_vbox.addWidget(QLabel("保存文件名(wav/mp3)"))
        self.output_name_edit = QLineEdit(SAMBERT_DEFAULT_OUTPUT_FILENAME)
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
            "连接、发送/接收 JSON、二进制音频块等信息将显示于此…"
        )
        self.ws_log_edit.document().setMaximumBlockCount(4000)
        log_layout.addWidget(self.ws_log_edit, 1)
        layout.addWidget(log_card, 0)

    def _ws_log_append_line(self, line: str) -> None:
        self.ws_log_edit.append(line)
        sb = self.ws_log_edit.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _current_voice_row(self) -> dict[str, Any] | None:
        i = self.voice_combo.currentIndex()
        if i < 0 or i >= len(self._voices):
            return None
        return self._voices[i]

    def _wire_voice_description(self) -> None:
        self.voice_combo.currentIndexChanged.connect(self._update_voice_description)
        self._update_voice_description()

    def _update_voice_description(self) -> None:
        row = self._current_voice_row()
        if row:
            self.voice_desc_label.setText(voice_description(row))
        else:
            self.voice_desc_label.setText("未加载音色配置，请检查 sambert_tts.yaml")

    def _sync_action_row_tail(self, *, show_waveform: bool) -> None:
        """无语音/无波形时：与最初一致，两按钮靠左，右侧为弹性留白；有波形时由画布占满右侧。"""
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

    def _refresh_play_time_label(self) -> None:
        el = self._elapsed_display_ms()
        tot = self._total_display_ms()
        if tot <= 0:
            self.status_label.setText(f"{ptf.fmt_mmss_no_fraction(el)}/--:--")
        else:
            self.status_label.setText(
                f"{ptf.fmt_mmss_no_fraction(el)}/{ptf.fmt_mmss_tenth(tot)}"
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
                self.waveform_canvas.set_playhead(
                    max(0.0, min(1.0, float(sk) / float(trusted)))
                )
            self._refresh_play_time_label()
            return
        self.waveform_canvas.set_playhead(self._playback_ratio())
        if self._play_cycle_pending:
            self._refresh_play_time_label()

    def _on_waveform_seek(self, ratio: float) -> None:
        """波形点击：ratio 为 0~1，pos 与 setPosition 均为毫秒。"""
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
        thr = WaveformLoadThread(path.resolve())

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

    def _infer_audio_format(self, path: Path) -> str:
        suf = path.suffix.lower().lstrip(".")
        if suf in ("wav", "mp3", "pcm"):
            return suf
        return "mp3"

    def _on_synthesize(self) -> None:
        ws_url = self.ws_url_edit.text().strip()
        if not ws_url:
            QMessageBox.warning(self, "配置", "请填写 WebSocket 地址。")
            return
        key = self.api_key_combo.currentData()
        if not key or not str(key).strip():
            QMessageBox.warning(self, "配置", "请在 bailian_api_key.yaml 中配置有效的 bailian_api_key。")
            return
        text = self.text_edit.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, "输入", "请输入要合成的文本。")
            return
        row = self._current_voice_row()
        if not row:
            QMessageBox.warning(self, "配置", "没有可用音色。")
            return
        model = str(row.get("voice_id", ""))
        out = Path(self.output_name_edit.text().strip() or SAMBERT_DEFAULT_OUTPUT_FILENAME)
        if not out.is_absolute():
            out = ROOT / out
        audio_format = self._infer_audio_format(out)

        args = SynthesisArgs(
            ws_url=ws_url,
            api_key=str(key).strip(),
            model=model,
            text=text,
            output_path=out,
            audio_format=audio_format,
            sample_rate=int(self.sample_rate_combo.currentData()),
            volume=int(self.volume_slider.value()),
            rate=float(self.rate_slider.value()) / 20.0,
            pitch=float(self.pitch_slider.value()) / 20.0,
            word_timestamp_enabled=self.word_ts_cb.isChecked(),
            phoneme_timestamp_enabled=self.phoneme_ts_cb.isChecked(),
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
        self.status_label.setText("正在合成…")

        self.ws_log_edit.append(format_log_line("—— 开始合成（新任务）——"))

        self._synth_worker = SynthesisWorker(args)
        self._synth_worker.log_line.connect(self._ws_log_append_line)
        self._synth_worker.finished_ok.connect(self._on_synth_ok)
        self._synth_worker.failed.connect(self._on_synth_fail)
        self._synth_worker.start()

    def _on_synth_ok(self, path_str: str) -> None:
        self.synth_btn.setEnabled(True)
        self._last_output = Path(path_str)
        self.status_label.setText(f"完成：{path_str}")
        ext = self._last_output.suffix.lower()
        if ext == ".wav":
            repair_wav_chunk_sizes(self._last_output)
        if ext in (".wav", ".mp3"):
            self.play_btn.setEnabled(True)
            self.play_btn.setToolTip("使用 miniaudio 播放刚合成的音频（支持 WAV、MP3）")
            self._sync_action_row_tail(show_waveform=True)
            self._player.load(self._last_output)
            self._start_waveform_load(self._last_output)
        else:
            self.play_btn.setEnabled(False)
            self._sync_action_row_tail(show_waveform=False)
            self.play_btn.setToolTip(
                "内置播放仅支持 .wav 与 .mp3；请修改保存文件名扩展名后重新合成"
            )

    def _on_synth_fail(self, msg: str) -> None:
        self.synth_btn.setEnabled(True)
        self.status_label.setText("合成失败")
        QMessageBox.critical(self, "合成失败", msg)

    def _on_playback_finished(self) -> None:
        if not self._play_cycle_pending:
            return
        self._play_cycle_pending = False
        self._play_time_timer.stop()
        self._seek_display_pos_ms = None
        self.play_btn.setEnabled(True)
        tot = self._total_display_ms()
        self.waveform_canvas.set_playhead(1.0 if tot > 0 else 0.0)
        if tot > 0:
            ts = ptf.fmt_mmss_tenth(tot)
            self.status_label.setText(f"{ts}/{ts}")
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
        if ext == ".wav":
            repair_wav_chunk_sizes(self._last_output)
        self._player.load(self._last_output)
        self._play_cycle_pending = True
        self._player.play()
        self._play_time_timer.start()
