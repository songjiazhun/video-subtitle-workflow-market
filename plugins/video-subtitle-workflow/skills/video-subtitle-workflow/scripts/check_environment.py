#!/usr/bin/env python3
"""环境自检，可选自动安装缺失依赖。

    python3 check_environment.py            # 只检查
    python3 check_environment.py --install  # 检查并自动安装缺失项
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys


def has_module(name: str) -> bool:
    try:
        __import__(name)
        return True
    except ImportError:
        return False


def run(cmd: list[str]) -> bool:
    print(f"   $ {' '.join(cmd)}")
    return subprocess.run(cmd).returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser(description="video-subtitle-workflow 环境检查")
    parser.add_argument("--install", action="store_true", help="自动安装缺失依赖")
    args = parser.parse_args()

    ok = True

    # Python 版本
    if sys.version_info < (3, 9):
        print(f"❌ 需要 Python 3.9+，当前 {sys.version.split()[0]}")
        ok = False
    else:
        print(f"✅ Python {sys.version.split()[0]}")

    # ffmpeg
    if shutil.which("ffmpeg"):
        print("✅ ffmpeg 已安装")
    elif args.install:
        print("⏳ 正在安装 ffmpeg ...")
        if sys.platform == "darwin":
            ok_install = run(["brew", "install", "ffmpeg"])
            hint = "brew install ffmpeg"
        else:
            ok_install = run(["sudo", "apt-get", "update", "-qq"]) and \
                run(["sudo", "apt-get", "install", "-y", "-qq", "ffmpeg"])
            hint = "sudo apt-get install -y ffmpeg"
        if ok_install:
            print("✅ ffmpeg 安装完成")
        else:
            print(f"❌ ffmpeg 安装失败，请手动执行：{hint}")
            ok = False
    else:
        if sys.platform == "darwin":
            print("❌ 缺少 ffmpeg  ->  brew install ffmpeg")
        else:
            print("❌ 缺少 ffmpeg  ->  sudo apt-get install -y ffmpeg")
        ok = False

    # 转写引擎：二选一即可
    fw, ow = has_module("faster_whisper"), has_module("whisper")
    if fw or ow:
        engines = [n for n, flag in (("faster-whisper", fw), ("openai-whisper", ow)) if flag]
        print(f"✅ 转写引擎：{', '.join(engines)}")
    elif args.install:
        print("⏳ 正在安装 faster-whisper ...")
        if run([sys.executable, "-m", "pip", "install", "-q", "faster-whisper"]):
            print("✅ faster-whisper 安装完成")
        else:
            print("❌ 安装失败，请手动执行：sudo pip3 install faster-whisper")
            ok = False
    else:
        print("❌ 缺少转写引擎  ->  sudo pip3 install faster-whisper")
        ok = False

    print()
    print("✅ 环境就绪，可以开始转字幕" if ok else "⚠️  存在缺失项，请先补齐（或加 --install 自动安装）")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
