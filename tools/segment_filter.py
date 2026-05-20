"""
GPT-SoVITS 訓練片段篩選 CLI。
依序播放 slicer_opt 裡的片段、顯示 ASR 逐字稿、讓使用者用鍵盤判斷留/刪。

使用：
    python tools/segment_filter.py <slicer_dir> <asr_list> <out_dir>

按鍵：
    Y / Enter : 留（複製到 out_dir、寫入 kept.list）
    N / Space : 刪（不複製、寫入 rejected.list）
    R        : 重播當前片段
    B        : 退回上一個重判
    Q        : 存檔並離開
"""
from __future__ import annotations
import sys
import os
import wave
import shutil
import time
from pathlib import Path

import numpy as np
import sounddevice as sd

try:
    import msvcrt  # Windows
    def get_key():
        ch = msvcrt.getch()
        if ch in (b'\x00', b'\xe0'):  # 特殊鍵 prefix
            msvcrt.getch()
            return ''
        try:
            return ch.decode('utf-8').lower()
        except UnicodeDecodeError:
            return ''
except ImportError:
    import termios, tty
    def get_key():
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            ch = sys.stdin.read(1)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        return ch.lower()


def load_list(list_path: Path) -> dict[str, str]:
    """讀 ASR 對照表，回傳 {filename: text}"""
    mapping = {}
    if not list_path.exists():
        return mapping
    for line in list_path.read_text(encoding='utf-8').splitlines():
        parts = line.split('|')
        if len(parts) < 4:
            continue
        path = parts[0]
        text = parts[3]
        mapping[Path(path).name] = text
    return mapping


def play(path: Path):
    """非阻塞播放 wav"""
    try:
        with wave.open(str(path), 'rb') as w:
            sr = w.getframerate()
            ch = w.getnchannels()
            data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
            if ch == 2:
                data = data.reshape(-1, 2)
        sd.play(data, sr)
    except Exception as e:
        print(f"  ! 播放失敗：{e}")


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)

    slicer_dir = Path(sys.argv[1])
    asr_list = Path(sys.argv[2])
    out_dir = Path(sys.argv[3])
    out_dir.mkdir(parents=True, exist_ok=True)

    # 收集所有片段，依檔名排序
    files = sorted(slicer_dir.glob("*.wav"))
    if not files:
        print(f"找不到 wav：{slicer_dir}")
        sys.exit(1)

    text_map = load_list(asr_list)

    kept_path = out_dir / "kept.list"
    rejected_path = out_dir / "rejected.list"
    # 續跑：已判過的跳過
    judged: set[str] = set()
    for p in (kept_path, rejected_path):
        if p.exists():
            for line in p.read_text(encoding='utf-8').splitlines():
                judged.add(line.split('|')[0])

    print(f"\n總片段：{len(files)}，已判：{len(judged)}，待判：{len(files) - len(judged)}")
    print("按鍵：Y 留 / N 刪 / R 重播 / B 退回 / Q 存檔離開")
    print("-" * 60)

    history: list[tuple[Path, str]] = []  # (file, decision)
    i = 0
    while i < len(files):
        f = files[i]
        if f.name in judged:
            i += 1
            continue

        text = text_map.get(f.name, "(無逐字稿)")
        try:
            with wave.open(str(f), 'rb') as w:
                dur = w.getnframes() / w.getframerate()
        except Exception:
            dur = 0
        kept = sum(1 for _, d in history if d == 'y')
        rej = sum(1 for _, d in history if d == 'n')
        print(f"\n[{i+1}/{len(files)}] 留={kept} 刪={rej}  ({dur:.1f}s)")
        print(f"  {f.name}")
        print(f"  「{text}」")
        play(f)

        while True:
            print("  Y 留 / N 刪 / R 重播 / B 退回 / Q 離開 > ", end='', flush=True)
            k = get_key()
            print(k)
            if k in ('y', '\r', '\n', ' '):
                # 'y' or enter = keep; 'space' for keep too (more ergonomic)
                # actually let's separate: y=keep, n=reject, space=ambiguous default to keep
                sd.stop()
                with kept_path.open('a', encoding='utf-8') as fp:
                    fp.write(f"{f.name}|{text}\n")
                shutil.copy2(f, out_dir / f.name)
                history.append((f, 'y'))
                judged.add(f.name)
                i += 1
                break
            elif k == 'n':
                sd.stop()
                with rejected_path.open('a', encoding='utf-8') as fp:
                    fp.write(f"{f.name}|{text}\n")
                history.append((f, 'n'))
                judged.add(f.name)
                i += 1
                break
            elif k == 'r':
                sd.stop()
                time.sleep(0.05)
                play(f)
                continue
            elif k == 'b':
                if not history:
                    print("  (沒有上一筆)")
                    continue
                last_f, last_d = history.pop()
                judged.discard(last_f.name)
                # 從 list 移除最後一行
                target = kept_path if last_d == 'y' else rejected_path
                if target.exists():
                    lines = target.read_text(encoding='utf-8').splitlines()
                    if lines:
                        lines.pop()
                        target.write_text('\n'.join(lines) + ('\n' if lines else ''), encoding='utf-8')
                # 也把 kept 複製出的檔刪掉
                if last_d == 'y':
                    (out_dir / last_f.name).unlink(missing_ok=True)
                # i 倒退到上一個
                i = files.index(last_f)
                print("  (退回)")
                break
            elif k == 'q':
                sd.stop()
                print(f"\n離開。已判 {len(history)} 個（留 {sum(1 for _,d in history if d=='y')}、刪 {sum(1 for _,d in history if d=='n')}）")
                return
            else:
                continue  # invalid key, ask again

    print(f"\n全部判完！留 {sum(1 for _,d in history if d=='y')} 個，刪 {sum(1 for _,d in history if d=='n')} 個")
    print(f"  kept list:    {kept_path}")
    print(f"  rejected list: {rejected_path}")
    print(f"  output dir:   {out_dir}")


if __name__ == '__main__':
    main()
