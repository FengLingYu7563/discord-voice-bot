# Discord Voice Chat Bot

English | [繁體中文](README.md)

A real-time Discord voice channel chat bot: you talk in a voice channel, the bot listens, thinks, and replies back with speech.

```
You speak -> STT -> LLM generates a reply -> TTS synthesizes speech -> bot speaks in the channel
```

## Features

- Real-time voice reception in a voice channel (supports Discord DAVE E2E encryption)
- STT: faster-whisper (local, Chinese) transcribes speech to text
- LLM: Gemini 2.5 Flash produces conversational replies, with a customizable persona and conversation history
- Dual TTS backends:
  - `edge-tts`: cloud, free, good quality, fast (~1s)
  - `GPT-SoVITS`: local, voice cloning from reference audio (~5s)
- VAD-based dynamic segmentation: stops recording once you finish speaking; short utterances respond in ~2s
- Triple noise defense: VAD silence filtering + Whisper built-in VAD + hallucination blocklist, with an option to respond only to the initiator

## Requirements

- Python 3.13
- (Optional) NVIDIA GPU for the local GPT-SoVITS TTS
- ffmpeg (bundled via `imageio-ffmpeg`, no separate install needed)
- A Discord Bot Token and a Gemini API Key

## Installation

```bash
python -m venv venv
venv\Scripts\activate          # Windows
pip install -r requirements.txt
```

py-cord uses a fork with DAVE decryption support (see `requirements.txt`).

## Configuration

Create a `.env` file in the project root:

```ini
DISCORD_TOKEN=your_discord_bot_token
GEMINI_API_KEY=your_gemini_api_key

# TTS backend: edge (cloud, default) or gptsovits (local voice clone)
TTS_BACKEND=edge
```

> Note: `.env` contains secrets and is excluded by `.gitignore`. Do not commit it.

Persona, voice, and VAD parameters are all tunable in the constants block at the top of `cogs/voice.py`.

## Setting Up the Voice

### Option A: edge-tts (simplest)

Set `TTS_BACKEND=edge` in `.env`, then change the `VOICE` constant in `cogs/voice.py`:

```python
VOICE = "zh-TW-HsiaoYuNeural"   # Taiwanese female (lively)
# others: zh-TW-HsiaoChenNeural (gentle female), zh-TW-YunJheNeural (male)
```

### Option B: GPT-SoVITS local voice cloning

1. Install [GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS) and start its `api_v2.py` (default port 9880)
2. Prepare reference audio: clean, single-speaker, no background music, each clip a 3 to 10 second `.wav`
3. Place the reference audio in `refs/` and set the paths in `cogs/voice.py`:

```python
GPTSOVITS_REF_AUDIO = "refs/your_main_ref.wav"   # main voice (defines the timbre)
GPTSOVITS_REF_TEXT  = "exact transcript of the reference clip"  # must match the audio
GPTSOVITS_AUX_AUDIO = [                           # optional: extra reference clips
    "refs/aux1.wav",
    "refs/aux2.wav",
]
```

4. Set `TTS_BACKEND=gptsovits` in `.env`

Multi-voice blending: put clips from different voices in `GPTSOVITS_AUX_AUDIO` and the model averages their timbres. The more clips you give a voice, the heavier its weight in the blend.

Reference audio quality tips:

- Pure speech, single speaker, quiet, no BGM, no singing
- Avoid clips with background music or singing (vocal isolation leaves an airy/torn artifact that synthesizes into a ghostly sound)
- The transcript must match the audio, otherwise it degrades synthesis quality

## Running

```bash
# If using GPT-SoVITS, start its api_v2.py first (port 9880)
python main.py
```

In Discord:

- `/join`: the bot joins your current voice channel and starts the conversation
- `/leave`: the bot leaves

## Architecture

| Component | Purpose |
|---|---|
| `main.py` | Bot entry point, loads the cog |
| `cogs/voice.py` | Core: recording, VAD, STT, LLM, TTS |
| `VADSink` | Custom sink that filters silence by peak amplitude for dynamic segmentation |
| `_gemini_reply` | Calls Gemini with conversation history and automatic retries |
| `speak` | TTS synthesis and playback, with edge / gptsovits switching and fallback |

## License

The **code** in this project is licensed under the [MIT License](LICENSE) - free to use, modify, and distribute.

Third-party dependencies have their own licenses (py-cord, faster-whisper, GPT-SoVITS, edge-tts, etc.); use them under their respective terms.

## Disclaimer

When using voice cloning, ensure you have the legal right to use the reference audio source. Cloning someone's voice without authorization may involve personality rights, privacy, or copyright issues. Users are responsible for their own use. The MIT license covers only the code itself, not any usage rights to voice data.
