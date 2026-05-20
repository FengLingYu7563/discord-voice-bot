import io
import time
import wave
import discord
import numpy as np
from discord.ext import commands
import edge_tts
import tempfile
import os
import asyncio
import aiohttp
import imageio_ffmpeg
from faster_whisper import WhisperModel
from google import genai
from google.genai import types as gtypes


# VAD 音量門檻：16-bit PCM 峰值，超過視為有聲，否則當靜音
VAD_PEAK_THRESHOLD = 1500


class VADSink(discord.sinks.PCMSink):
    """PCMSink + 用峰值門檻過濾 Discord silence frame，追蹤最後一次「真正聲音」到達時間"""
    def __init__(self):
        super().__init__()
        self.last_voice_ts: float | None = None
        self.first_voice_ts: float | None = None

    def write(self, data, user):
        super().write(data, user)
        pcm = getattr(data, "pcm", data)
        if not pcm:
            return
        try:
            samples = np.frombuffer(pcm, dtype=np.int16)
            peak = int(np.abs(samples).max()) if samples.size else 0
        except Exception:
            return
        if peak < VAD_PEAK_THRESHOLD:
            return  # silence frame
        now = time.time()
        if self.first_voice_ts is None:
            self.first_voice_ts = now
        self.last_voice_ts = now

VOICE = "zh-TW-HsiaoYuNeural"
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

# TTS backend: "edge" (cloud, 預設) 或 "gptsovits" (本地 voice clone)
TTS_BACKEND = os.getenv("TTS_BACKEND", "edge").lower()
GPTSOVITS_URL = os.getenv("GPTSOVITS_URL", "http://127.0.0.1:9880/tts")
# 融合音色設定（A、B 各半、無 C、咬字鬆 temp 1.1）
# 路徑相對於專案根目錄，不寫死本機絕對路徑（可攜 + 不洩漏目錄結構）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FUSION = os.path.join(_PROJECT_ROOT, "refs", "fusion")
GPTSOVITS_REF_AUDIO = os.getenv(
    "GPTSOVITS_REF_AUDIO", os.path.join(_FUSION, "aux_b2.wav")
)
GPTSOVITS_REF_TEXT = os.getenv(
    "GPTSOVITS_REF_TEXT",
    "双排那个我也觉得那个双排那个有点上面那个。",
)
# 主 B2 + aux 3 段 = 共 2B 2A（無 C）
GPTSOVITS_AUX_AUDIO = [
    os.path.join(_FUSION, "aux_b1.wav"),   # B
    os.path.join(_FUSION, "aux_a1.wav"),   # A
    os.path.join(_FUSION, "aux_a2.wav"),   # A
]
# VAD 參數
MIN_RECORD = 1.0         # 最短錄音秒數
MAX_RECORD = 10.0        # 最長錄音秒數（hard cap）
SILENCE_END = 1.2        # 偵測到語音後沉默多久就停錄
POLL_INTERVAL = 0.15     # 偵測週期
MIN_PCM_BYTES = 48000 * 2 * 2  # 1 秒 48kHz 立體聲 16-bit

GEMINI_MODEL = "gemini-2.5-flash"
SYSTEM_PROMPT = (
    "你是『楓小語』，一個在 Discord 語音頻道陪人聊天的台灣女生。"
    "個性：聰明、有點皮、講話直接但不毒舌，喜歡反問、喜歡開玩笑，但不會油。"
    "說話方式："
    "- 全程台灣中文口語、不要用 Markdown、不要條列、不要念表情符號或網址。"
    "- 回覆一律一到兩句、最多三十個字，講太多會被當成廢話。"
    "- 自然用「欸」「對啊」「噢」「齁」「啦」「耶」「吼」「就」等口語詞，但不要每句都塞。"
    "- 不講「作為一個 AI」「我沒有情緒」「我無法」之類的官腔。"
    "- 對方說話聽起來不完整或像雜音，就用『欸你剛說啥？』之類的方式請他再講一次，不要硬猜。"
    "- 對方丟很無聊的話題你可以稍微吐槽，但要好玩不要嗆。"
    "- 對方問你身份就說自己是楓鈴語，不要拆穿自己是 AI。"
)

print("Loading Whisper model...")
_whisper = WhisperModel("small", device="cpu", compute_type="int8")
print("Whisper model ready.")

_genai_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))


def _gemini_reply(history: list[gtypes.Content], user_text: str) -> str:
    """同步呼叫 Gemini，回傳助理文字。會 in-place 更新 history。
    遇到 503 / 暫時錯誤自動重試 2 次。"""
    history.append(gtypes.Content(role="user", parts=[gtypes.Part(text=user_text)]))

    last_exc = None
    resp = None
    for attempt in range(3):
        try:
            resp = _genai_client.models.generate_content(
                model=GEMINI_MODEL,
                contents=history,
                config=gtypes.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    temperature=0.8,
                    max_output_tokens=512,
                    # 關掉 thinking：聊天不需推理，且 thinking 會吃掉 output token
                    # 導致回覆講一半被截斷，關掉同時也讓回應更快
                    thinking_config=gtypes.ThinkingConfig(thinking_budget=0),
                ),
            )
            break
        except Exception as e:
            last_exc = e
            msg = str(e)
            # 503 / 429 / 500 / DeadlineExceeded 都重試
            if any(code in msg for code in ("503", "429", "500", "UNAVAILABLE", "DeadlineExceeded")):
                wait = 1.5 * (attempt + 1)
                print(f"[WARN] Gemini 暫時錯誤 (attempt {attempt+1}/3)，{wait}s 後重試：{msg[:120]}", flush=True)
                time.sleep(wait)
                continue
            # 其他錯誤不重試
            raise
    if resp is None:
        # 所有重試都失敗，把這輪 user msg 拔掉避免污染後續對話
        history.pop()
        raise last_exc if last_exc else RuntimeError("Gemini 無回應")

    reply = (resp.text or "").strip()
    if reply:
        history.append(gtypes.Content(role="model", parts=[gtypes.Part(text=reply)]))
    # 只保留最近 20 輪避免脈絡爆炸
    if len(history) > 40:
        del history[: len(history) - 40]
    return reply


def _transcribe(path: str) -> str:
    segments, _ = _whisper.transcribe(path, language="zh")
    return "".join(s.text for s in segments).strip()


def _pcm_to_wav(pcm_data: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(48000)
        wf.writeframes(pcm_data)
    return buf.getvalue()


async def _synth_edge(text: str) -> str:
    tmp = tempfile.mktemp(suffix=".mp3")
    await edge_tts.Communicate(text, VOICE).save(tmp)
    return tmp


async def _synth_gptsovits(text: str) -> str:
    payload = {
        "text": text,
        "text_lang": "zh",
        "ref_audio_path": GPTSOVITS_REF_AUDIO,
        "prompt_text": GPTSOVITS_REF_TEXT,
        "aux_ref_audio_paths": GPTSOVITS_AUX_AUDIO,
        "prompt_lang": "zh",
        "media_type": "wav",
        "streaming_mode": False,
        "text_split_method": "cut0",
        "fragment_interval": 0.1,
        "speed_factor": 1.05,
        "temperature": 1.1,
        "top_k": 15,
        "top_p": 0.9,
    }
    timeout = aiohttp.ClientTimeout(total=60)
    async with aiohttp.ClientSession(timeout=timeout) as sess:
        async with sess.post(GPTSOVITS_URL, json=payload) as resp:
            resp.raise_for_status()
            data = await resp.read()
    tmp = tempfile.mktemp(suffix=".wav")
    with open(tmp, "wb") as f:
        f.write(data)
    return tmp


async def speak(vc, text: str):
    if TTS_BACKEND == "gptsovits":
        try:
            tmp = await _synth_gptsovits(text)
        except Exception as e:
            print(f"[WARN] GPT-SoVITS 合成失敗，fallback edge-tts：{e}", flush=True)
            tmp = await _synth_edge(text)
    else:
        tmp = await _synth_edge(text)
    vc.play(discord.FFmpegPCMAudio(tmp, executable=FFMPEG))
    while vc.is_playing():
        await asyncio.sleep(0.1)
    os.remove(tmp)


class VoiceCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.sessions: dict[int, bool] = {}
        self.histories: dict[int, list[gtypes.Content]] = {}

    @discord.slash_command(name="join", description="讓機器人加入你目前的語音頻道")
    async def join(self, ctx: discord.ApplicationContext):
        if not ctx.author.voice or not ctx.author.voice.channel:
            await ctx.respond("你需要先加入一個語音頻道。", ephemeral=True)
            return

        try:
            await ctx.defer()
        except Exception as e:
            print(f"[ERROR] defer 失敗：{e}", flush=True)
            return

        if self.sessions.get(ctx.guild_id):
            await ctx.followup.send("已經在錄音中，請先 /leave。", ephemeral=True)
            return

        channel = ctx.author.voice.channel
        if ctx.guild.voice_client:
            await ctx.guild.voice_client.disconnect()
            await asyncio.sleep(1)

        try:
            vc = await channel.connect(timeout=30.0, reconnect=False)
        except Exception as e:
            print(f"[ERROR] connect 失敗：{e}", flush=True)
            await ctx.followup.send(f"無法加入語音頻道：{e}")
            return

        print(f"[INFO] 已加入頻道：{channel.name}", flush=True)
        await ctx.followup.send(f"已加入 {channel.name}")
        self.sessions[ctx.guild_id] = True
        asyncio.create_task(self._listen_loop(vc, ctx))

    @discord.slash_command(name="leave", description="讓機器人離開語音頻道")
    async def leave(self, ctx: discord.ApplicationContext):
        vc = ctx.guild.voice_client
        if not vc:
            await ctx.respond("我目前不在任何語音頻道。", ephemeral=True)
            return
        self.sessions[ctx.guild_id] = False
        self.histories.pop(ctx.guild_id, None)
        await vc.disconnect()
        await ctx.respond("已離開語音頻道。")

    async def _listen_loop(self, vc: discord.VoiceClient, ctx: discord.ApplicationContext):
        print("[INFO] _listen_loop 開始", flush=True)
        try:
            await speak(vc, "你好，我來了，可以開始聊天了。")
        except Exception as e:
            print(f"[WARN] 歡迎語失敗：{e}", flush=True)
            self.sessions[ctx.guild_id] = False
            return

        loop = asyncio.get_event_loop()
        guild_id = ctx.guild_id

        try:
            while self.sessions.get(guild_id) and vc.is_connected():
                done = asyncio.Event()
                results: dict[int, dict] = {}
                sink = VADSink()

                def on_done(exc):
                    if exc:
                        print(f"[WARN] 錄音錯誤：{exc}", flush=True)
                    for user, audio in sink.audio_data.items():
                        audio.file.seek(0)
                        pcm_data = audio.file.read()
                        uid = user.id if user else 0
                        results[uid] = {"user": user, "data": pcm_data}
                    loop.call_soon_threadsafe(done.set)

                try:
                    vc.start_recording(sink, on_done)
                except Exception as e:
                    print(f"[WARN] 錄音啟動失敗：{e}", flush=True)
                    break

                # VAD 偵測迴圈：sink 沒收到音訊或沉默不夠久就繼續錄
                start = time.time()
                while True:
                    await asyncio.sleep(POLL_INTERVAL)
                    if not vc.is_connected():
                        break
                    elapsed = time.time() - start
                    if elapsed >= MAX_RECORD:
                        print(f"[VAD] 達上限 {MAX_RECORD}s", flush=True)
                        break
                    if elapsed < MIN_RECORD:
                        continue
                    # 必須先有人講話才檢查沉默
                    if sink.last_voice_ts is None:
                        continue
                    silence = time.time() - sink.last_voice_ts
                    if silence >= SILENCE_END:
                        spoke_for = sink.last_voice_ts - (sink.first_voice_ts or sink.last_voice_ts)
                        print(f"[VAD] 沉默 {silence:.1f}s，講話 {spoke_for:.1f}s，停錄", flush=True)
                        break

                if not vc.is_connected():
                    print("[INFO] 錄音期間斷線", flush=True)
                    break

                vc.stop_recording()
                await done.wait()

                for uid, entry in results.items():
                    if not vc.is_connected():
                        break

                    pcm_data = entry["data"]
                    user = entry["user"]

                    print(f"[INFO] {user} PCM={len(pcm_data)} 門檻={MIN_PCM_BYTES}", flush=True)
                    if len(pcm_data) < MIN_PCM_BYTES:
                        print("[INFO] 音訊太短，跳過", flush=True)
                        continue

                    wav_data = _pcm_to_wav(pcm_data)
                    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                        f.write(wav_data)
                        tmp_path = f.name

                    try:
                        text = await loop.run_in_executor(None, _transcribe, tmp_path)
                    finally:
                        os.unlink(tmp_path)

                    print(f"[STT] {user}: '{text}'", flush=True)
                    if not text:
                        print("[INFO] STT 為空，跳過", flush=True)
                        continue

                    history = self.histories.setdefault(guild_id, [])
                    try:
                        reply = await loop.run_in_executor(
                            None, _gemini_reply, history, text
                        )
                    except Exception as e:
                        print(f"[WARN] LLM 失敗：{e}", flush=True)
                        continue

                    print(f"[LLM] {reply}", flush=True)
                    if not reply:
                        print("[INFO] LLM 回空，跳過", flush=True)
                        continue

                    try:
                        await speak(vc, reply)
                    except Exception as e:
                        print(f"[WARN] 播放失敗：{e}", flush=True)
                        break
        finally:
            self.sessions[guild_id] = False
            print("[INFO] _listen_loop 結束", flush=True)


def setup(bot: commands.Bot):
    bot.add_cog(VoiceCog(bot))
