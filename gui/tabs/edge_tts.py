# -- coding: utf-8 --
"""Edge TTS 标签页（Microsoft Edge 在线朗读，``edge-tts``）。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PyQt5 import sip
from PyQt5.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtWidgets import (
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

from edge_tts_client import (
    apply_windows_selector_event_loop_policy,
    list_edge_voice_short_names,
    synthesize_edge_tts_to_file,
)
from gui.config import edge_slider_axis_bounds, load_edge_tts_yaml
from gui.constants import (
    DEFAULT_TTS_TEXT,
    EDGE_DEFAULT_OUTPUT_FILENAME,
    EDGE_TTS_YAML,
    ROOT,
    TTS_ACTION_BAR_HEIGHT_PX,
)
from gui.format_log import format_log_line
from gui.miniaudio_player import MiniAudioPlayer
import gui.play_time_format as ptf
from gui.waveform import WaveformLoadThread, WaveformWidget
from gui.widgets import GtArrowComboBox, SingleLineElidingInfoLabel


@dataclass
class EdgeSynthArgs:
    text: str
    voice: str
    rate: str
    volume: str
    pitch: str
    output_path: Path


class EdgeVoiceListWorker(QThread):
    """后台拉取 edge-tts 全量音色 ShortName。"""

    done = pyqtSignal(list)
    failed = pyqtSignal(str)

    def run(self) -> None:
        import asyncio

        apply_windows_selector_event_loop_policy()
        try:
            names = asyncio.run(list_edge_voice_short_names())
            self.done.emit(names)
        except Exception as e:
            self.failed.emit(str(e))


class EdgeSynthesisWorker(QThread):
    finished_ok = pyqtSignal(str)
    failed = pyqtSignal(str)
    log_line = pyqtSignal(str)

    def __init__(self, args: EdgeSynthArgs) -> None:
        super().__init__()
        self._args = args

    def run(self) -> None:
        import asyncio

        apply_windows_selector_event_loop_policy()
        a = self._args

        def log(msg: str) -> None:
            self.log_line.emit(format_log_line(msg))

        try:
            log(
                "Edge TTS 调用参数："
                f" voice={a.voice!r} rate={a.rate!r} volume={a.volume!r} pitch={a.pitch!r}"
                f" output={str(a.output_path.resolve())!r} text_len={len(a.text)}"
            )
            asyncio.run(
                synthesize_edge_tts_to_file(
                    a.text,
                    a.voice,
                    a.rate,
                    a.output_path,
                    volume=a.volume,
                    pitch=a.pitch,
                )
            )
            log("Edge TTS：文件已保存")
            self.finished_ok.emit(str(a.output_path.resolve()))
        except Exception as e:
            log(f"Edge TTS 异常：{e}")
            self.failed.emit(str(e))


class EdgeTtsTab(QWidget):
    """Edge TTS：对齐 ``edgeTTS_cli.py`` 的 text / voice / rate / save 流程。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("edgeTabRoot")
        self._cfg = load_edge_tts_yaml(EDGE_TTS_YAML)
        self._bind_edge_slider_axes()
        self._synth_worker: EdgeSynthesisWorker | None = None
        self._voice_worker: EdgeVoiceListWorker | None = None
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
        self._init_edge_param_sliders_from_yaml()
        QTimer.singleShot(0, self._bootstrap_existing_audio_file)

    @staticmethod
    def _fmt_signed_pct(pct: int) -> str:
        if pct > 0:
            return f"+{pct}%"
        if pct < 0:
            return f"{pct}%"
        return "+0%"

    @staticmethod
    def _fmt_signed_hz(hz: int) -> str:
        return f"{hz:+d}Hz"

    @staticmethod
    def _parse_signed_percent_token(s: str) -> int | None:
        t = (s or "").strip().replace("%", "").strip()
        if not t:
            return None
        try:
            return int(t)
        except ValueError:
            return None

    @staticmethod
    def _parse_signed_hz_token(s: str) -> int | None:
        t = (s or "").strip().replace("Hz", "").replace("HZ", "").strip()
        if not t:
            return None
        try:
            return int(t)
        except ValueError:
            return None

    def _bind_edge_slider_axes(self) -> None:
        """从 ``edge_tts.yaml`` 读取三组 min/max/step（界面不暴露编辑，仅配置文件可调）。"""
        c = self._cfg
        (
            self._rate_min_pct,
            self._rate_max_pct,
            self._rate_step_pct,
        ) = edge_slider_axis_bounds(c, "rate")
        (
            self._volume_min_pct,
            self._volume_max_pct,
            self._volume_step_pct,
        ) = edge_slider_axis_bounds(c, "volume")
        (
            self._pitch_min_hz,
            self._pitch_max_hz,
            self._pitch_step_hz,
        ) = edge_slider_axis_bounds(c, "pitch")
        self._rate_slider_max_index = (
            self._rate_max_pct - self._rate_min_pct
        ) // self._rate_step_pct
        self._volume_slider_max_index = (
            self._volume_max_pct - self._volume_min_pct
        ) // self._volume_step_pct
        self._pitch_slider_max_index = (
            self._pitch_max_hz - self._pitch_min_hz
        ) // self._pitch_step_hz

    @staticmethod
    def _axis_physical_to_index(v: int, mn: int, mx: int, st: int) -> int | None:
        if st <= 0 or v < mn or v > mx:
            return None
        if (v - mn) % st != 0:
            return None
        return (v - mn) // st

    def _default_slider_index_for_axis(
        self, target: int, mn: int, mx: int, st: int, max_ix: int
    ) -> int:
        ix = EdgeTtsTab._axis_physical_to_index(target, mn, mx, st)
        if ix is not None:
            return ix
        return max(0, min(max_ix, max_ix // 2))

    def _edge_rate_pct(self) -> int:
        return self._rate_min_pct + int(self.rate_slider.value()) * self._rate_step_pct

    def _edge_volume_pct(self) -> int:
        return self._volume_min_pct + int(self.volume_slider.value()) * self._volume_step_pct

    def _edge_pitch_hz(self) -> int:
        return self._pitch_min_hz + int(self.pitch_slider.value()) * self._pitch_step_hz

    def _edge_rate_string(self) -> str:
        return self._fmt_signed_pct(self._edge_rate_pct())

    def _edge_volume_string(self) -> str:
        return self._fmt_signed_pct(self._edge_volume_pct())

    def _edge_pitch_string(self) -> str:
        return self._fmt_signed_hz(self._edge_pitch_hz())

    def _update_edge_rate_label(self, *_args: Any) -> None:
        self.rate_value_label.setText(self._edge_rate_string())

    def _update_edge_volume_label(self, *_args: Any) -> None:
        self.volume_value_label.setText(self._edge_volume_string())

    def _update_edge_pitch_label(self, *_args: Any) -> None:
        self.pitch_value_label.setText(self._edge_pitch_string())

    def _init_edge_param_sliders_from_yaml(self) -> None:
        dr = str(self._cfg.get("default_rate") or "+0%").strip()
        rp = self._parse_signed_percent_token(dr)
        if rp is not None:
            ri = self._axis_physical_to_index(
                rp,
                self._rate_min_pct,
                self._rate_max_pct,
                self._rate_step_pct,
            )
            if ri is not None and 0 <= ri <= self._rate_slider_max_index:
                self.rate_slider.setValue(ri)
        self._update_edge_rate_label()
        self._update_edge_volume_label()
        self._update_edge_pitch_label()

    def collect_user_tab_settings(self) -> dict[str, Any]:
        return {
            "text": self.text_edit.toPlainText(),
            "voice": str(self.voice_combo.currentData() or self.voice_combo.currentText() or "").strip(),
            "rate": self._edge_rate_string(),
            "volume": self._edge_volume_string(),
            "pitch": self._edge_pitch_string(),
            "output_filename": self.output_name_edit.text(),
        }

    def apply_user_tab_settings(self, data: dict[str, Any] | None) -> None:
        if not isinstance(data, dict):
            return
        text = data.get("text")
        if isinstance(text, str):
            self.text_edit.setPlainText(text)
        voice = str(data.get("voice", "") or "").strip()
        if voice:
            ix = self.voice_combo.findData(voice)
            if ix < 0:
                ix = self.voice_combo.findText(voice)
            if ix >= 0:
                self.voice_combo.setCurrentIndex(ix)
            else:
                self.voice_combo.insertItem(0, voice, voice)
                self.voice_combo.setCurrentIndex(0)
        rs = data.get("rate")
        if isinstance(rs, str) and rs.strip():
            rp = self._parse_signed_percent_token(rs)
            if rp is not None:
                ri = self._axis_physical_to_index(
                    rp,
                    self._rate_min_pct,
                    self._rate_max_pct,
                    self._rate_step_pct,
                )
                if ri is not None and 0 <= ri <= self._rate_slider_max_index:
                    self.rate_slider.setValue(ri)
        vs = data.get("volume")
        if isinstance(vs, str) and vs.strip():
            vp = self._parse_signed_percent_token(vs)
            if vp is not None:
                vi = self._axis_physical_to_index(
                    vp,
                    self._volume_min_pct,
                    self._volume_max_pct,
                    self._volume_step_pct,
                )
                if vi is not None and 0 <= vi <= self._volume_slider_max_index:
                    self.volume_slider.setValue(vi)
        ps = data.get("pitch")
        if isinstance(ps, str) and ps.strip():
            hz = self._parse_signed_hz_token(ps)
            if hz is not None:
                pi = self._axis_physical_to_index(
                    hz,
                    self._pitch_min_hz,
                    self._pitch_max_hz,
                    self._pitch_step_hz,
                )
                if pi is not None and 0 <= pi <= self._pitch_slider_max_index:
                    self.pitch_slider.setValue(pi)
        out_fn = data.get("output_filename")
        if isinstance(out_fn, str):
            self.output_name_edit.setText(out_fn)
        self._update_edge_rate_label()
        self._update_edge_volume_label()
        self._update_edge_pitch_label()

    def _current_output_path(self) -> Path:
        out = Path(self.output_name_edit.text().strip() or EDGE_DEFAULT_OUTPUT_FILENAME)
        if not out.is_absolute():
            out = ROOT / out
        return out.resolve()

    def _bootstrap_existing_audio_file(self) -> None:
        path = self._current_output_path()
        if not path.is_file():
            return
        ext = path.suffix.lower()
        if ext != ".mp3":
            return
        self._last_output = path
        self._player.load(path)
        self.play_btn.setEnabled(True)
        self.play_btn.setToolTip("使用 miniaudio 播放已保存的 MP3。")
        self._sync_action_row_tail(show_waveform=True)
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
        help_url = str(self._cfg.get("help_url") or "").strip()
        help_lbl = QLabel()
        help_lbl.setObjectName("cardSectionUsageLink")
        if help_url:
            help_lbl.setText(f'<a href="{help_url}">edge-tts 文档</a>')
            help_lbl.setOpenExternalLinks(True)
            help_lbl.setToolTip(help_url)
        else:
            help_lbl.setVisible(False)
        header_row.addWidget(help_lbl, 0, Qt.AlignVCenter)
        conn_layout.addLayout(header_row)

        hint = QLabel("本页使用 pip 包 edge-tts，请确保网络可访问微软服务。")
        hint.setObjectName("voiceDesc")
        hint.setWordWrap(True)
        conn_layout.addWidget(hint)
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

        voice_row = QHBoxLayout()
        voice_row.addWidget(QLabel("音色 ShortName"), 0, Qt.AlignVCenter)
        self.voice_combo = GtArrowComboBox()
        default_v = str(self._cfg.get("default_voice") or "zh-CN-XiaoyiNeural").strip()
        voices_raw = self._cfg.get("voices") or []
        voice_list: list[str] = []
        if isinstance(voices_raw, list):
            for x in voices_raw:
                s = str(x).strip()
                if s:
                    voice_list.append(s)
        if not voice_list and default_v:
            voice_list = [default_v]
        for vid in voice_list:
            self.voice_combo.addItem(vid, vid)
        if default_v:
            ix = self.voice_combo.findData(default_v)
            if ix >= 0:
                self.voice_combo.setCurrentIndex(ix)
        self.refresh_voices_btn = QPushButton("刷新音色列表")
        self.refresh_voices_btn.setObjectName("secondaryBtn")
        self.refresh_voices_btn.setToolTip("从 edge-tts 拉取全部 ShortName（需联网）")
        self.refresh_voices_btn.clicked.connect(self._on_refresh_voices)
        voice_row.addWidget(self.voice_combo, 1)
        voice_row.addWidget(self.refresh_voices_btn, 0)
        synth_vbox.addLayout(voice_row)

        middle_layout.addWidget(synth_card, 2)

        param_card = QFrame()
        param_card.setObjectName("card")
        param_card.setFixedWidth(420)
        param_vbox = QVBoxLayout(param_card)
        header_param = QLabel("⚙ 参数")
        header_param.setObjectName("cardSectionTitle")
        param_vbox.addWidget(header_param)

        def _param_slider_header_row(title: str, initial_display: str) -> QLabel:
            hbox = QHBoxLayout()
            hbox.addWidget(QLabel(title))
            hbox.addStretch()
            val_lbl = QLabel(initial_display)
            val_lbl.setObjectName("paramValueAccent")
            val_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            hbox.addWidget(val_lbl)
            param_vbox.addLayout(hbox)
            return val_lbl

        rm, rM = self._rate_min_pct, self._rate_max_pct
        rate_title = f"语速 rate {rm}%～{rM}%"
        self.rate_value_label = _param_slider_header_row(rate_title, "")
        self.rate_slider = QSlider(Qt.Horizontal)
        self.rate_slider.setRange(0, self._rate_slider_max_index)
        self.rate_slider.setValue(
            self._default_slider_index_for_axis(
                0,
                self._rate_min_pct,
                self._rate_max_pct,
                self._rate_step_pct,
                self._rate_slider_max_index,
            )
        )
        self.rate_slider.setSingleStep(1)
        self.rate_slider.setPageStep(1)
        param_vbox.addWidget(self.rate_slider)
        self.rate_slider.valueChanged.connect(self._update_edge_rate_label)

        param_vbox.addSpacing(6)
        vm, vM = self._volume_min_pct, self._volume_max_pct
        volume_title = f"音量 volume {vm}%～{vM}%"
        self.volume_value_label = _param_slider_header_row(volume_title, "")
        self.volume_slider = QSlider(Qt.Horizontal)
        self.volume_slider.setRange(0, self._volume_slider_max_index)
        self.volume_slider.setValue(
            self._default_slider_index_for_axis(
                0,
                self._volume_min_pct,
                self._volume_max_pct,
                self._volume_step_pct,
                self._volume_slider_max_index,
            )
        )
        self.volume_slider.setSingleStep(1)
        self.volume_slider.setPageStep(2)
        param_vbox.addWidget(self.volume_slider)
        self.volume_slider.valueChanged.connect(self._update_edge_volume_label)

        param_vbox.addSpacing(6)
        pm, pM = self._pitch_min_hz, self._pitch_max_hz
        pitch_title = f"音调 pitch {pm}Hz～{pM}Hz"
        self.pitch_value_label = _param_slider_header_row(pitch_title, "")
        self.pitch_slider = QSlider(Qt.Horizontal)
        self.pitch_slider.setRange(0, self._pitch_slider_max_index)
        self.pitch_slider.setValue(
            self._default_slider_index_for_axis(
                0,
                self._pitch_min_hz,
                self._pitch_max_hz,
                self._pitch_step_hz,
                self._pitch_slider_max_index,
            )
        )
        self.pitch_slider.setSingleStep(1)
        self.pitch_slider.setPageStep(2)
        param_vbox.addWidget(self.pitch_slider)
        self.pitch_slider.valueChanged.connect(self._update_edge_pitch_label)

        self._update_edge_rate_label()
        self._update_edge_volume_label()
        self._update_edge_pitch_label()

        param_vbox.addSpacing(10)
        param_vbox.addWidget(QLabel("保存文件名(mp3)"))
        def_name = str(self._cfg.get("default_output_filename") or EDGE_DEFAULT_OUTPUT_FILENAME).strip()
        self.output_name_edit = QLineEdit(def_name or EDGE_DEFAULT_OUTPUT_FILENAME)
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
        self.ws_log_edit.setPlaceholderText("Edge TTS 合成过程与异常将显示于此…")
        self.ws_log_edit.document().setMaximumBlockCount(4000)
        log_layout.addWidget(self.ws_log_edit, 1)
        layout.addWidget(log_card, 1)

    def _ws_log_append_line(self, line: str) -> None:
        self.ws_log_edit.append(line)
        sb = self.ws_log_edit.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _on_refresh_voices(self) -> None:
        if self._voice_worker and self._voice_worker.isRunning():
            return
        self.refresh_voices_btn.setEnabled(False)
        self.status_label.setText("正在拉取音色列表…")
        self._ws_log_append_line(format_log_line("请求 edge-tts list_voices…"))
        self._voice_worker = EdgeVoiceListWorker()
        self._voice_worker.done.connect(self._on_voice_list_ok)
        self._voice_worker.failed.connect(self._on_voice_list_fail)
        self._voice_worker.finished.connect(lambda: self.refresh_voices_btn.setEnabled(True))
        self._voice_worker.start()

    def _on_voice_list_ok(self, names: list) -> None:
        raw = [str(x).strip() for x in names if str(x).strip()]
        if not raw:
            self.status_label.setText("音色列表为空")
            self._ws_log_append_line(format_log_line("音色列表为空"))
            return
        cur = str(self.voice_combo.currentData() or "").strip()
        self.voice_combo.blockSignals(True)
        self.voice_combo.clear()
        for vid in raw:
            self.voice_combo.addItem(vid, vid)
        self.voice_combo.blockSignals(False)
        if cur:
            ix = self.voice_combo.findData(cur)
            self.voice_combo.setCurrentIndex(ix if ix >= 0 else 0)
        else:
            self.voice_combo.setCurrentIndex(0)
        self.status_label.setText(f"已加载 {len(raw)} 个音色")
        self._ws_log_append_line(format_log_line(f"音色列表已更新，共 {len(raw)} 项"))

    def _on_voice_list_fail(self, msg: str) -> None:
        self.status_label.setText("拉取音色失败")
        self._ws_log_append_line(format_log_line(f"list_voices 失败：{msg}"))
        QMessageBox.warning(self, "刷新音色", f"无法拉取音色列表：\n{msg}")

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
                self.waveform_canvas.set_playhead(max(0.0, min(1.0, float(sk) / float(trusted))))
            self._refresh_play_time_label()
            return
        self.waveform_canvas.set_playhead(self._playback_ratio())
        if self._play_cycle_pending:
            self._refresh_play_time_label()

    def _on_waveform_seek(self, ratio: float) -> None:
        if not self._last_output or not self._last_output.is_file():
            return
        if self._last_output.suffix.lower() != ".mp3":
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

    def _current_voice(self) -> str:
        i = self.voice_combo.currentIndex()
        if i >= 0:
            d = self.voice_combo.itemData(i)
            if d:
                return str(d).strip()
            t = self.voice_combo.itemText(i)
            if t.strip():
                return t.strip()
        return ""

    def _on_synthesize(self) -> None:
        text = self.text_edit.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, "输入", "请输入要合成的文本。")
            return
        voice = self._current_voice()
        if not voice:
            QMessageBox.warning(self, "配置", "请选择或填写音色 ShortName。")
            return
        rate = self._edge_rate_string()
        vol = self._edge_volume_string()
        pch = self._edge_pitch_string()

        out = Path(self.output_name_edit.text().strip() or EDGE_DEFAULT_OUTPUT_FILENAME)
        if not out.is_absolute():
            out = ROOT / out
        suf = out.suffix.lower()
        if suf != ".mp3":
            QMessageBox.warning(
                self,
                "保存文件名",
                "Edge TTS 在本工具中仅支持保存为 .mp3，请将扩展名设为 .mp3。",
            )
            return

        args = EdgeSynthArgs(
            text=text,
            voice=voice,
            rate=rate,
            volume=vol,
            pitch=pch,
            output_path=out,
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
        self.status_label.setText("正在合成（Edge TTS）…")
        self._ws_log_append_line(format_log_line("—— 开始合成（Edge TTS）——"))

        self._synth_worker = EdgeSynthesisWorker(args)
        self._synth_worker.log_line.connect(self._ws_log_append_line)
        self._synth_worker.finished_ok.connect(self._on_synth_ok)
        self._synth_worker.failed.connect(self._on_synth_fail)
        self._synth_worker.start()

    def _on_synth_ok(self, path_str: str) -> None:
        self.synth_btn.setEnabled(True)
        self._last_output = Path(path_str)
        self.status_label.setText(f"完成：{path_str}")
        ext = self._last_output.suffix.lower()
        if ext == ".mp3":
            self.play_btn.setEnabled(True)
            self.play_btn.setToolTip("使用 miniaudio 播放已保存的 MP3。")
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
            self.play_btn.setEnabled(ext == ".mp3")
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
        if ext != ".mp3":
            QMessageBox.warning(
                self,
                "播放",
                f"Edge TTS 页仅支持 MP3；当前为「{ext or '无扩展名'}」。",
            )
            return
        self._seek_display_pos_ms = None
        self.play_btn.setEnabled(False)
        self._refresh_play_time_label()
        self._player.load(self._last_output)
        self._play_cycle_pending = True
        self._player.play()
        self._play_time_timer.start()
