# Discord 語音對話機器人

[English](README.en.md) | 繁體中文

一個即時的 Discord 語音頻道對話機器人：你在語音頻道說話，機器人聽懂、思考、再用語音回你。

```
你說話 -> STT 語音辨識 -> LLM 產生回覆 -> TTS 合成語音 -> 機器人在頻道講出來
```

## 功能

- 即時語音接收：在語音頻道聽使用者說話（支援 Discord DAVE E2E 加密）
- STT：faster-whisper（本地、中文）把語音轉文字
- LLM：Gemini 2.5 Flash 產生口語化回覆，可自訂人設、保留對話脈絡
- TTS 雙後端：
  - `edge-tts`：雲端、免費、品質好、反應快（約 1 秒）
  - `GPT-SoVITS`：本地、可用參考音做 voice cloning（約 5 秒）
- VAD 動態切段：偵測到你講完就停錄，短句約 2 秒就回應

## 需求

- Python 3.13
- （選用）NVIDIA GPU，跑 GPT-SoVITS 本地 TTS 用
- ffmpeg（由 `imageio-ffmpeg` 內建，不需另裝）
- Discord Bot Token、Gemini API Key

## 安裝

```bash
python -m venv venv
venv\Scripts\activate          # Windows
pip install -r requirements.txt
```

py-cord 使用支援 DAVE 解密的 fork（見 `requirements.txt`）。

## 設定

在專案根目錄建立 `.env`：

```ini
DISCORD_TOKEN=你的_discord_bot_token
GEMINI_API_KEY=你的_gemini_api_key

# TTS 後端：edge（雲端，預設）或 gptsovits（本地 voice clone）
TTS_BACKEND=edge
```

> 注意：`.env` 含密鑰，已被 `.gitignore` 排除，請勿上傳。

人設、語音、VAD 參數都在 `cogs/voice.py` 開頭的常數區可調。

## 聲音怎麼設定

### 方式 A：edge-tts（最簡單）

`.env` 設 `TTS_BACKEND=edge`，在 `cogs/voice.py` 改 `VOICE` 常數：

```python
VOICE = "zh-TW-HsiaoYuNeural"   # 台灣女聲（活潑）
# 其他：zh-TW-HsiaoChenNeural（溫柔女）、zh-TW-YunJheNeural（男聲）
```

### 方式 B：GPT-SoVITS 本地 voice cloning

1. 安裝 [GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS)，啟動它的 `api_v2.py`（預設 port 9880）
2. 準備參考音檔：乾淨、單人、無背景音樂的講話片段，每段 3 到 10 秒的 `.wav`
3. 把參考音放進 `refs/`，在 `cogs/voice.py` 設定路徑：

```python
GPTSOVITS_REF_AUDIO = "refs/你的主參考.wav"      # 主聲音（決定音色）
GPTSOVITS_REF_TEXT  = "這段參考音實際說的逐字稿"    # 必須跟音檔內容一致
GPTSOVITS_AUX_AUDIO = [                          # 選用：多個輔助參考音
    "refs/aux1.wav",
    "refs/aux2.wav",
]
```

4. `.env` 設 `TTS_BACKEND=gptsovits`

多人融合：在 `GPTSOVITS_AUX_AUDIO` 放多個不同聲音的片段，模型會平均它們的音色。給某個聲音越多段，它在融合裡的比重越重。

參考音品質要點：

- 純講話、單人、安靜、無 BGM、無唱歌
- 不要含背景音樂或唱歌段（去背後會有空靈撕裂感，合成出來像鬼聲）
- 逐字稿要跟音檔對得上，否則污染合成品質

## 啟動

```bash
# 若用 GPT-SoVITS，先啟動它的 api_v2.py（port 9880）
python main.py
```

到 Discord：

- `/join`：機器人加入你所在的語音頻道、開始對話
- `/leave`：離開

## 架構

| 元件 | 用途 |
|---|---|
| `main.py` | bot 進入點，載入 cog |
| `cogs/voice.py` | 核心：錄音、VAD、STT、LLM、TTS |
| `VADSink` | 自訂 sink，用音量峰值過濾靜音、做動態切段 |
| `_gemini_reply` | 呼叫 Gemini，含對話脈絡與自動重試 |
| `speak` | TTS 合成與播放，支援 edge / gptsovits 切換與 fallback |

## 授權

本專案的**程式碼**採用 [MIT License](LICENSE)，可自由使用、修改、散布。

第三方相依套件各自有其授權（py-cord、faster-whisper、GPT-SoVITS、edge-tts 等），請依各自條款使用。

## 責任聲明

使用語音複製功能時，請確保你對參考音來源擁有合法使用權。未經授權複製他人聲音可能涉及人格權、隱私權或著作權問題，使用者需自負責任。本專案的 MIT 授權僅涵蓋程式碼本身，不包含任何聲音資料的使用權。
