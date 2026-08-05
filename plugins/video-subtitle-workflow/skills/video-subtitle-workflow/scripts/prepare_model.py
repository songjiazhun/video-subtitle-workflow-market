#!/usr/bin/env python3
"""预下载 Whisper 模型权重到本地，解决转录时拉不到权重的问题。

逐文件下载并各自重试，避免某个小文件失败导致整批（含几百 MB 大文件）
全部作废。已下载完成的文件会自动跳过，可反复重跑。

默认走 HuggingFace；当官方源/镜像不可达时，可改用 ModelScope 源
（国内与沙箱环境最稳，仓库 pengzhendong/faster-whisper-*）。

    # 自动：先 HF（含镜像回退），失败再换 ModelScope（默认）
    python3 prepare_model.py --model small --verify

    # 显式指定 ModelScope 源（最省心，推荐沙箱/国内）
    python3 prepare_model.py --model small --source modelscope --verify

    # 国内 HF 镜像（先试）
    python3 prepare_model.py --model small --mirror --verify

    # 走本地代理
    python3 prepare_model.py --model small --proxy http://127.0.0.1:7890 --verify

    # 只检查本地已有权重是否完整，不下载
    python3 prepare_model.py --model small --check-only --out ./models
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from pathlib import Path

FW_REPOS = {
    "tiny": "Systran/faster-whisper-tiny",
    "base": "Systran/faster-whisper-base",
    "small": "Systran/faster-whisper-small",
    "medium": "Systran/faster-whisper-medium",
    "large-v2": "Systran/faster-whisper-large-v2",
    "large-v3": "Systran/faster-whisper-large-v3",
}

# ModelScope 上的等价仓库（社区维护，国内/沙箱可达）
MS_REPOS = {
    "tiny": "pengzhendong/faster-whisper-tiny",
    "base": "pengzhendong/faster-whisper-base",
    "small": "pengzhendong/faster-whisper-small",
    "medium": "pengzhendong/faster-whisper-medium",
    "large-v2": "pengzhendong/faster-whisper-large-v2",
    "large-v3": "pengzhendong/faster-whisper-large-v3",
}

# faster-whisper 推理必需的文件。vocabulary 在不同仓库里可能是 .txt 或 .json，
# 因此分成必需与可选两组，可选文件缺失不算失败。
REQUIRED = ["config.json", "model.bin", "tokenizer.json"]
OPTIONAL = ["vocabulary.txt", "vocabulary.json", "preprocessor_config.json"]

MIRROR = "https://hf-mirror.com"
OFFICIAL = "https://huggingface.co"


def apply_network(proxy: str | None, endpoint: str) -> None:
    if proxy:
        for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            os.environ[k] = proxy
        print(f"🌐 代理：{proxy}")
    os.environ["HF_ENDPOINT"] = endpoint
    # 关闭高速传输后端，它在镜像站上容易出现校验失败
    os.environ["HF_HUB_ENABLE_HF_TRANSFER"] = "0"
    print(f"🔗 源站：{endpoint}")


def fetch_one(repo: str, filename: str, target: Path, retries: int = 3) -> bool:
    """下载单个文件，失败重试。已存在且非空则跳过。"""
    from huggingface_hub import hf_hub_download

    dst = target / filename
    if dst.exists() and dst.stat().st_size > 0:
        print(f"   ✓ {filename}（已存在，跳过）")
        return True

    for attempt in range(1, retries + 1):
        try:
            hf_hub_download(
                repo_id=repo,
                filename=filename,
                local_dir=str(target),
            )
            size = dst.stat().st_size / 1024 / 1024
            print(f"   ✓ {filename}（{size:.1f} MB）")
            return True
        except Exception as exc:
            name = type(exc).__name__
            if attempt < retries:
                wait = attempt * 3
                print(f"   ⟳ {filename} 第 {attempt} 次失败（{name}），{wait}s 后重试")
                time.sleep(wait)
            else:
                print(f"   ✗ {filename} 失败：{name}")
                return False
    return False


def download_hf(repo: str, target: Path) -> tuple[bool, list[str]]:
    """返回 (必需文件是否齐全, 失败的必需文件列表)。"""
    failed: list[str] = []

    print("📦 必需文件：")
    for fn in REQUIRED:
        if not fetch_one(repo, fn, target):
            failed.append(fn)

    print("📎 可选文件：")
    got_vocab = False
    for fn in OPTIONAL:
        # 可选文件只试一次，不存在于仓库是正常情况
        if fetch_one(repo, fn, target, retries=1):
            if fn.startswith("vocabulary"):
                got_vocab = True
    if not got_vocab:
        print("   （未找到 vocabulary 文件，多数模型不影响使用）")

    return (not failed), failed


def download_modelscope(repo: str, target: Path) -> tuple[bool, list[str]]:
    """用 ModelScope 一次性拉取整个仓库（最稳的国内/沙箱源）。

    返回 (必需文件是否齐全, 失败的必需文件列表)。
    """
    try:
        from modelscope.hub.snapshot_download import snapshot_download
    except ImportError:
        print("❌ 缺少 modelscope：pip3 install modelscope")
        return False, list(REQUIRED)

    target.mkdir(parents=True, exist_ok=True)
    print(f"⬇️  [ModelScope] {repo}  ->  {target}")
    try:
        snapshot_download(repo_id=repo, local_dir=str(target))
    except Exception as exc:
        print(f"❌ ModelScope 下载失败：{type(exc).__name__}: {str(exc)[:200]}")
        return False, list(REQUIRED)

    # snapshot_download 会把仓库内全部文件拉下来，这里核验必需文件
    failed = [f for f in REQUIRED if not (target / f).exists()
              or (target / f).stat().st_size == 0]
    if failed:
        print(f"❌ 拉取完成但缺少：{', '.join(failed)}")
    return (not failed), failed


def check(target: Path) -> bool:
    if not target.is_dir():
        print(f"❌ 目录不存在：{target}")
        return False
    missing = [f for f in REQUIRED if not (target / f).exists()
               or (target / f).stat().st_size == 0]
    if missing:
        print(f"❌ 缺少或为空：{', '.join(missing)}")
        return False
    total = sum(f.stat().st_size for f in target.rglob("*") if f.is_file())
    print(f"✅ 必需文件齐全，共 {total / 1024 / 1024:.0f} MB")
    return True


def smoke_test(target: Path) -> bool:
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("⚠️  未安装 faster-whisper，跳过加载测试")
        return True
    try:
        print("🧪 加载模型自检...")
        WhisperModel(str(target), device="cpu", compute_type="int8")
        print("✅ 模型加载成功")
        return True
    except Exception as exc:
        print(f"❌ 加载失败：{type(exc).__name__}: {str(exc)[:200]}")
        return False


def clean_cache(target: Path) -> None:
    """清掉 local_dir 内的下载缓存，回收 .incomplete 占用的空间。"""
    cache = target / ".cache"
    if cache.exists():
        shutil.rmtree(cache, ignore_errors=True)
        print("🧹 已清理下载缓存")


def main() -> int:
    p = argparse.ArgumentParser(description="预下载 Whisper 模型权重")
    p.add_argument("--model", default="small", choices=list(FW_REPOS))
    p.add_argument("--out", default="./models", help="输出根目录（默认 ./models）")
    p.add_argument("--proxy", help="HTTP 代理，如 http://127.0.0.1:7890")
    p.add_argument("--mirror", action="store_true", help="使用 hf-mirror.com")
    p.add_argument("--official", action="store_true", help="强制走 huggingface.co")
    p.add_argument("--source", default="auto",
                   choices=["auto", "huggingface", "modelscope"],
                   help="下载源：auto（先 HF 再回退 ModelScope，默认）/ "
                        "huggingface / modelscope")
    p.add_argument("--verify", action="store_true", help="下载后加载模型自检")
    p.add_argument("--check-only", action="store_true", help="只检查本地，不下载")
    args = p.parse_args()

    target = Path(args.out).expanduser().resolve() / args.model

    if args.check_only:
        ok = check(target)
        if ok and args.verify:
            ok = smoke_test(target)
        return 0 if ok else 1

    source = args.source
    ok, failed = False, list(REQUIRED)

    # ---- 路径一：HuggingFace（auto / huggingface）----
    if source in ("auto", "huggingface"):
        try:
            import huggingface_hub  # noqa: F401
        except ImportError:
            print("❌ 缺少 huggingface_hub：pip3 install huggingface_hub")
            if source == "huggingface":
                return 1
            print("   （auto 模式将改用 ModelScope）\n")
            source = "modelscope"
        else:
            endpoint = OFFICIAL if args.official else MIRROR if args.mirror else \
                os.environ.get("HF_ENDPOINT", MIRROR)
            apply_network(args.proxy, endpoint)

            repo = FW_REPOS[args.model]
            target.mkdir(parents=True, exist_ok=True)
            print(f"⬇️  {repo}  ->  {target}\n")

            ok, failed = download_hf(repo, target)

            # 镜像失败时自动换官方源再试一轮
            if not ok and endpoint == MIRROR and not args.official:
                print(f"\n⚠️  镜像下载失败：{', '.join(failed)}")
                print("🔄 换官方源重试（已下载的文件会跳过）...\n")
                apply_network(args.proxy, OFFICIAL)
                ok, failed = download_hf(repo, target)

            if not ok and source == "auto":
                print(f"\n⚠️  HuggingFace 源失败（{', '.join(failed)}）")
                print("🔄 auto 模式改用 ModelScope 重试...\n")
                source = "modelscope"

    # ---- 路径二：ModelScope ----
    if source == "modelscope":
        repo = MS_REPOS[args.model]
        target.mkdir(parents=True, exist_ok=True)
        ok, failed = download_modelscope(repo, target)

    print()
    if not ok:
        print(f"❌ 下载仍存在失败：{', '.join(failed)}")
        print("   建议：")
        print("   - 挂代理后加 --proxy http://127.0.0.1:7890 重跑")
        print("   - 或强制官方源：--official --proxy http://127.0.0.1:7890")
        print("   - 沙箱/国内推荐：--source modelscope")
        print("   - 重跑不会重复下载已完成的文件，可放心多试几次")
        return 1

    clean_cache(target)
    if not check(target):
        return 1
    if args.verify and not smoke_test(target):
        return 1

    print()
    print("🎉 完成。使用方式：")
    print(f"   python3 scripts/audio_to_srt.py <音频> --model {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
