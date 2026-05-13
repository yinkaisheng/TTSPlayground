# TTS Playground

基于 PyQt5 的本地 TTS 测试工具，支持 **Edge TTS**、阿里云百炼 **Sambert TTS**、**Qwen3 TTS** 三种在线语音合成。

## 功能

| 功能 | Edge TTS | Sambert TTS | Qwen3 TTS |
|------|:--------:|:-----------:|:---------:|
| 在线合成 | ✓ MP3 | ✓ WAV/MP3/PCM | ✓ WAV/MP3 |
| 音色选择 | ✓ ShortName 列表 | ✓ 音色 + 描述 | ✓ 模型→音色→语种联动 |
| 语速调节 | ✓ 滑块 | ✓ 滑块 | — |
| 音量/音调 | ✓ 滑块 | ✓ 滑块 | — |
| 时间戳 | — | ✓ Word/Phoneme | — |
| 语音指令 | — | — | ✓ instruct 模型 |
| 音频播放 | ✓ | ✓ | ✓ |
| 波形预览 | ✓ | ✓ | ✓ |
| 点击跳转 | ✓ | ✓ | ✓ |
| 合成日志 | ✓ | ✓ | ✓ |
| 界面记忆 | ✓ | ✓ | ✓ |

**播放与波形**：使用 miniaudio 统一解码和播放，WAV/MP3 均无需外部编解码器。支持点击波形任意位置跳转播放。

**界面记忆**：窗口位置/大小、字号、各 Tab 控件状态自动保存到 `user_setting.yaml`，下次启动恢复。

## 环境准备

- Python 3.10+
- 安装依赖：`pip install -r requirements.txt`

## 配置

### API Key（`bailian_api_key.yaml`）

```yaml
keys:
  - key_name: default
    bailian_api_key: sk-你的Key
```

### Edge TTS（`edge_tts.yaml`）

预置音色列表、默认语速/音量/音调。首次使用建议点击「刷新音色列表」拉取完整 ShortName。

### Sambert TTS（`sambert_tts.yaml`）

配置 WebSocket 地址和音色列表（`voice_id` / 名称 / 风格 / 语种 / 采样率 / 时间戳支持）。

### Qwen3 TTS（`qwen3_tts.yaml`）

配置 Realtime 地址、模型列表、音色、语种联动、会话模式（commit / server_commit）、采样率。

若模型名包含 `instruct`，界面自动显示「语音指令」输入框，可描述语速、语调、情感等。

## 运行

```bash
python run_tts_gui.py
```

标签栏右上角齿轮图标可调整全局字号（8～24 pt）。

## 音频输出

- **Edge TTS**：仅 `.mp3`
- **Sambert TTS**：`.wav` / `.mp3` / `.pcm`（由扩展名决定请求 format）
- **Qwen3 TTS**：`.wav` / `.mp3`（对应 `response_format`）

## 项目结构

```
├── run_tts_gui.py          # 入口
├── gui/
│   ├── tabs/               # edge_tts / sambert_tts / qwen3_tts
│   ├── waveform.py         # 波形加载与显示
│   ├── miniaudio_player.py # miniaudio 播放器
│   └── ...                 # 常量、样式、配置等
├── edge_tts_client.py      # Edge TTS 异步封装
├── sambert_tts_ws.py       # Sambert WebSocket 合成
├── qwen3_tts_ws.py         # Qwen3 Realtime 客户端
└── *.yaml                  # 配置文件
```
