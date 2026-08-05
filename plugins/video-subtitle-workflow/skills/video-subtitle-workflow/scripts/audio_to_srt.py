#!/usr/bin/env python3
"""音频/视频 -> SRT 字幕。

    python3 audio_to_srt.py input.m4a
    python3 audio_to_srt.py input.mp4 --max-chars 20 --lang zh --model small
    python3 audio_to_srt.py input.wav --engine openai-whisper --output out.srt

转写引擎优先级：faster-whisper > openai-whisper（可用 --engine 强制指定）。
faster-whisper 在纯 CPU 沙箱里通常快 4~5 倍且更省内存，因此作为默认。
输出默认为音频同目录下的 origin.srt。
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from srt_utils import Cue, allocate_by_length, close_gaps, render_srt, split_text  # noqa: E402

MEDIA_EXT = {
    ".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac", ".wma", ".opus",
    ".mp4", ".mov", ".mkv", ".avi", ".webm", ".flv",  # 视频直接抽音轨
}


# ---------------------------------------------------------------- 环境

def ensure_ffmpeg() -> None:
    if shutil.which("ffmpeg"):
        return
    print("❌ 未检测到 ffmpeg。请先安装：")
    print("   Ubuntu/Debian : sudo apt-get update && sudo apt-get install -y ffmpeg")
    print("   macOS         : brew install ffmpeg")
    sys.exit(1)


def pick_engine(preferred: str) -> str:
    """返回实际可用的引擎名；都不可用则给出安装提示并退出。"""

    def available(mod: str) -> bool:
        try:
            __import__(mod)
            return True
        except ImportError:
            return False

    if preferred == "faster-whisper":
        if available("faster_whisper"):
            return "faster-whisper"
        if available("whisper"):
            print("ℹ️  未安装 faster-whisper，回退到 openai-whisper")
            return "openai-whisper"
    elif preferred == "openai-whisper":
        if available("whisper"):
            return "openai-whisper"
        if available("faster_whisper"):
            print("ℹ️  未安装 openai-whisper，回退到 faster-whisper")
            return "faster-whisper"

    print("❌ 未找到可用的语音识别引擎，请安装其一：")
    print("   sudo pip3 install faster-whisper   # 推荐，CPU 更快")
    print("   sudo pip3 install openai-whisper")
    sys.exit(1)


def extract_audio(src: Path, workdir: Path) -> Path:
    """视频统一抽成 16k 单声道 wav，避免各引擎解码差异。"""
    dst = workdir / f"{src.stem}.__extracted__.wav"
    cmd = ["ffmpeg", "-y", "-i", str(src), "-vn",
           "-ac", "1", "-ar", "16000", "-f", "wav", str(dst)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        print("❌ ffmpeg 抽取音轨失败：")
        print(proc.stderr[-1500:])
        sys.exit(1)
    return dst


# ---------------------------------------------------------------- 转写

def model_download_hint(model_name: str, engine: str) -> None:
    """权重拉取失败时给出可操作的提示，而不是甩一堆 traceback。"""
    print("❌ 无法获取模型权重，通常是网络无法访问模型仓库所致。")
    print("   可尝试以下任一方案：")
    print("   1) 使用国内镜像后重跑：")
    print("      export HF_ENDPOINT=https://hf-mirror.com")
    print(f"   2) 换用更小的模型：--model tiny")
    if engine == "faster-whisper":
        print("   3) 预先下载权重到本地，再用本地路径：")
        print(f"      huggingface-cli download Systran/faster-whisper-{model_name} \\")
        print(f"        --local-dir ./models/{model_name}")
        print(f"      python3 audio_to_srt.py <音频> --model ./models/{model_name}")
    else:
        print("   3) 手动下载 .pt 权重放入 ~/.cache/whisper/ 后重跑")


def transcribe(audio: Path, engine: str, model_name: str, lang: str | None) -> list[dict]:
    """返回 [{'start': float, 'end': float, 'text': str}, ...]"""
    # 未显式配置时默认走镜像，国内网络下成功率更高；已配置则尊重用户设置
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

    if engine == "faster-whisper":
        from faster_whisper import WhisperModel

        print(f"🔊 faster-whisper 加载模型 {model_name}（首次会下载权重，请耐心等待）...")
        try:
            model = WhisperModel(model_name, device="cpu", compute_type="int8")
        except Exception as exc:  # 网络/权重问题统一给出友好提示
            model_download_hint(model_name, engine)
            print(f"   原始错误：{type(exc).__name__}: {str(exc)[:200]}")
            sys.exit(1)

        segments, info = model.transcribe(
            str(audio), language=lang, vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
        )
        print(f"🗣️  识别语言：{info.language}（置信度 {info.language_probability:.2f}）")
        return [{"start": s.start, "end": s.end, "text": s.text} for s in segments]

    import whisper

    print(f"🔊 openai-whisper 加载模型 {model_name}（首次会下载权重，请耐心等待）...")
    try:
        model = whisper.load_model(model_name)
    except Exception as exc:
        model_download_hint(model_name, engine)
        print(f"   原始错误：{type(exc).__name__}: {str(exc)[:200]}")
        sys.exit(1)

    result = model.transcribe(str(audio), language=lang, verbose=False)
    print(f"🗣️  识别语言：{result.get('language', lang or 'auto')}")
    return [{"start": s["start"], "end": s["end"], "text": s["text"]}
            for s in result["segments"]]


# ---------------------------------------------------------------- 组装

def build_cues(segments: list[dict], max_chars: int, min_chars: int) -> list[Cue]:
    """把转写片段按字数上限拆行，并按字符比例分配时间。"""
    cues: list[Cue] = []
    for seg in segments:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        lines = split_text(text, max_chars, min_chars)
        if not lines:
            continue
        for line, (start, end) in zip(lines, allocate_by_length(seg["start"], seg["end"], lines)):
            cues.append(Cue(len(cues) + 1, start, end, line))
    return cues


def main() -> int:
    parser = argparse.ArgumentParser(description="音频/视频转 SRT 字幕")
    parser.add_argument("media", help="输入音频或视频文件")
    parser.add_argument("--max-chars", type=int, default=22, help="每行最多字数（默认 22）")
    parser.add_argument("--min-chars", type=int, default=4, help="每行最少字数（默认 4）")
    parser.add_argument("--gap", type=float, default=0.3, help="小于该秒数的间隔自动闭合（默认 0.3）")
    parser.add_argument("--lang", default="zh", help="语言代码，auto 表示自动检测（默认 zh）")
    parser.add_argument("--model", default="small", help="模型规格 tiny/base/small/medium/large-v3（默认 small）")
    parser.add_argument("--engine", default="faster-whisper",
                        choices=["faster-whisper", "openai-whisper"], help="转写引擎")
    parser.add_argument("--output", help="输出路径（默认同目录 origin.srt）")
    args = parser.parse_args()

    if args.max_chars < 4:
        print("❌ --max-chars 不得小于 4")
        return 1
    if args.min_chars >= args.max_chars:
        print("❌ --min-chars 必须小于 --max-chars")
        return 1

    media = Path(args.media).expanduser()
    if not media.exists():
        print(f"❌ 文件不存在：{media}")
        return 1
    if media.suffix.lower() not in MEDIA_EXT:
        print(f"❌ 不支持的格式：{media.suffix}")
        print(f"   支持：{', '.join(sorted(MEDIA_EXT))}")
        return 1

    ensure_ffmpeg()
    engine = pick_engine(args.engine)
    lang = None if args.lang.lower() in ("auto", "") else args.lang

    audio, tmp = media, None
    if media.suffix.lower() in {".mp4", ".mov", ".mkv", ".avi", ".webm", ".flv"}:
        print("🎬 检测到视频，正在抽取音轨...")
        tmp = audio = extract_audio(media, media.parent)

    try:
        segments = transcribe(audio, engine, args.model, lang)
    finally:
        if tmp and tmp.exists():
            tmp.unlink()

    if not segments:
        print("❌ 未识别到任何语音内容")
        return 1

    cues = close_gaps(build_cues(segments, args.max_chars, args.min_chars), args.gap)
    out = Path(args.output) if args.output else media.parent / "origin.srt"
    out.write_text(render_srt(cues), encoding="utf-8")

    longest = max(len(c.text) for c in cues)
    print(f"✅ 已生成 {out}")
    print(f"   共 {len(cues)} 条字幕，最长一条 {longest} 字（上限 {args.max_chars}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
