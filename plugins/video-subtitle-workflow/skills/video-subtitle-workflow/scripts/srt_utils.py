#!/usr/bin/env python3
"""SRT 通用工具：解析、序列化、切分、时间轴校验。

被 audio_to_srt.py 复用，同时可作为 CLI 独立使用：

    python3 srt_utils.py verify origin.srt enhanced.srt   # 校验时间轴/条数未被改动
    python3 srt_utils.py stats  enhanced.srt              # 输出条数/时长/超长行统计
    python3 srt_utils.py cards  reference-cards.srt       # 校验字卡格式与密度
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------- 常量

# 中文断句标点，按“断句强度”从强到弱排列，切分时优先在强标点处断开
STRONG_PUNCT = "。！？!?；;"
WEAK_PUNCT = "，,、：:—…"
ALL_PUNCT = STRONG_PUNCT + WEAK_PUNCT

TIME_RE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})"
)
CARD_RE = re.compile(r"^\[字卡\]\(word:\s*(.+?)\s*/\s*type:\s*(.+?)\s*\)$")

VALID_CARD_TYPES = {"金色重点", "白色提醒", "红色警告", "蓝色列点",
                    "金色重點", "白色提醒", "紅色警告", "藍色列點"}


# ---------------------------------------------------------------- 数据结构

@dataclass
class Cue:
    """一条字幕。text 为正文行，cards 为附加的字卡行。"""

    index: int
    start: float
    end: float
    text: str
    cards: list[str] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return self.end - self.start


# ---------------------------------------------------------------- 时间戳

def format_timestamp(seconds: float) -> str:
    """秒 -> SRT 时间戳 HH:MM:SS,mmm（负数钳为 0）。"""
    if seconds < 0:
        seconds = 0.0
    total_ms = int(round(seconds * 1000))
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def parse_timestamp(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


# ---------------------------------------------------------------- 解析 / 序列化

def parse_srt(path: str | Path) -> list[Cue]:
    """解析 SRT 文件。以 `[字卡](...)` 开头的行归入 cards，其余归入 text。"""
    raw = Path(path).read_text(encoding="utf-8-sig")
    cues: list[Cue] = []

    for block in re.split(r"\n\s*\n", raw.strip()):
        lines = [ln.rstrip() for ln in block.strip().splitlines() if ln.strip()]
        if not lines:
            continue

        # 首行可能是序号，也可能被省略
        idx_offset = 0
        index = len(cues) + 1
        if lines[0].strip().isdigit():
            index = int(lines[0].strip())
            idx_offset = 1

        if len(lines) <= idx_offset:
            continue

        m = TIME_RE.search(lines[idx_offset])
        if not m:
            continue
        start = parse_timestamp(*m.groups()[:4])
        end = parse_timestamp(*m.groups()[4:])

        body, cards = [], []
        for ln in lines[idx_offset + 1:]:
            (cards if ln.startswith("[字卡]") else body).append(ln)

        cues.append(Cue(index, start, end, "\n".join(body), cards))

    return cues


def render_srt(cues: list[Cue], renumber: bool = True) -> str:
    """序列化为 SRT 文本（末尾带换行）。"""
    out: list[str] = []
    for i, cue in enumerate(cues, start=1):
        out.append(str(i if renumber else cue.index))
        out.append(f"{format_timestamp(cue.start)} --> {format_timestamp(cue.end)}")
        out.append(cue.text)
        out.extend(cue.cards)
        out.append("")
    return "\n".join(out).rstrip("\n") + "\n"


# ---------------------------------------------------------------- 文本切分

def split_text(text: str, max_chars: int = 22, min_chars: int = 4) -> list[str]:
    """把一段文字切成每行不超过 max_chars 的若干行。

    与上游实现的区别：上游用 ``text.split()`` 按空格切词，中文没有空格，
    整段会被当成单个 token 直接返回，max_chars 完全失效。这里改为：

    1. 先在中文标点处断句（标点保留在行尾，不单独成行）；
    2. 过长的句子按空格边界切（照顾英文）；
    3. 仍然过长的按字符数硬切（照顾无标点长中文）；
    4. 最后把短于 min_chars 的碎片并回相邻行。
    """
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    # 1) 标点断句：标点跟随前文
    sentences: list[str] = []
    buf = ""
    for ch in text:
        buf += ch
        if ch in ALL_PUNCT:
            sentences.append(buf.strip())
            buf = ""
    if buf.strip():
        sentences.append(buf.strip())

    # 2) 贪心装箱 + 过长句再切
    lines: list[str] = []
    current = ""
    for sent in sentences:
        if not sent:
            continue
        if len(current) + len(sent) <= max_chars:
            current += sent
            continue
        if current:
            lines.append(current)
            current = ""
        if len(sent) <= max_chars:
            current = sent
        else:
            lines.extend(_split_long(sent, max_chars))
            # _split_long 的最后一段留作 current，便于后续继续拼接
            current = lines.pop() if lines else ""
    if current:
        lines.append(current)

    # 3) 合并过短碎片
    return _merge_short(lines, max_chars, min_chars)


def _split_long(sent: str, max_chars: int) -> list[str]:
    """切分单个超长句：优先空格边界（英文），否则按字符硬切（中文）。"""
    if " " in sent:
        chunks, cur = [], ""
        for word in sent.split(" "):
            cand = f"{cur} {word}".strip()
            if len(cand) <= max_chars:
                cur = cand
            else:
                if cur:
                    chunks.append(cur)
                # 单词本身就超长 -> 硬切
                while len(word) > max_chars:
                    chunks.append(word[:max_chars])
                    word = word[max_chars:]
                cur = word
        if cur:
            chunks.append(cur)
        return chunks
    return _hard_split(sent, max_chars)


def _is_token_char(ch: str) -> bool:
    """属于「不该被切断」的字符：字母、数字、小数点、连字符。"""
    return ch.isascii() and (ch.isalnum() or ch in "._-")


def _hard_split(sent: str, max_chars: int) -> list[str]:
    """按字数硬切，但不切断英文单词与数字（如 Python3.9 不拆成 Python3. + 9）。"""
    chunks: list[str] = []
    i = 0
    n = len(sent)
    while i < n:
        end = min(i + max_chars, n)
        if end < n and _is_token_char(sent[end - 1]) and _is_token_char(sent[end]):
            # 切点落在 token 中间，向前回退到 token 起点
            back = end
            while back > i and _is_token_char(sent[back - 1]):
                back -= 1
            # 回退后仍有内容才采纳，否则该 token 本身超长，只能硬切
            if back > i:
                end = back
        chunks.append(sent[i:end])
        i = end
    return chunks


def _merge_short(lines: list[str], max_chars: int, min_chars: int) -> list[str]:
    """消除短于 min_chars 的碎片行。

    优先直接并入相邻行；若合并会超出 max_chars，则把两行合起来重新对半切分
    （再平衡），避免出现「22 字 + 1 字」这种难看的结果。
    """
    result: list[str] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if result and len(line) < min_chars:
            prev = result[-1]
            if len(prev) + len(line) <= max_chars:
                result[-1] = prev + line
                continue
            # 合并超限 -> 再平衡：合起来从中点附近重切成两行
            merged = prev + line
            cut = _rebalance_cut(merged, max_chars)
            if cut:
                result[-1] = merged[:cut]
                result.append(merged[cut:])
                continue
        result.append(line)

    # 收尾：首行仍过短则并入后一行
    if len(result) > 1 and len(result[0]) < min_chars \
            and len(result[0]) + len(result[1]) <= max_chars:
        result[1] = result[0] + result[1]
        result.pop(0)
    return result


def _rebalance_cut(merged: str, max_chars: int) -> int | None:
    """为再平衡挑一个切点，使切出的两行都不超限且尽量均衡。

    返回切点下标；找不到合法切点时返回 None。
    """
    n = len(merged)
    if n > max_chars * 2:
        return None
    mid = n // 2
    # 从中点向两侧扩散找合法切点，优先不切断 token
    for offset in range(0, mid):
        for cut in (mid - offset, mid + offset):
            if cut <= 0 or cut >= n:
                continue
            if cut > max_chars or n - cut > max_chars:
                continue
            if _is_token_char(merged[cut - 1]) and _is_token_char(merged[cut]):
                continue  # 别切在单词/数字中间
            return cut
    return None


# ---------------------------------------------------------------- 时间轴

def close_gaps(cues: list[Cue], threshold: float = 0.3) -> list[Cue]:
    """相邻字幕间隔小于 threshold 秒时，把前一条的结束时间延伸到后一条的开始。

    同时修正 end < start 的非法区间，以及后一条早于前一条的重叠。
    """
    for i in range(len(cues) - 1):
        cur, nxt = cues[i], cues[i + 1]
        if cur.end < cur.start:
            cur.end = cur.start
        gap = nxt.start - cur.end
        if 0 <= gap < threshold:
            cur.end = nxt.start
        elif gap < 0:  # 重叠，以后一条为准截断前一条
            cur.end = max(cur.start, nxt.start)
    if cues and cues[-1].end < cues[-1].start:
        cues[-1].end = cues[-1].start
    return cues


def allocate_by_length(start: float, end: float, lines: list[str]) -> list[tuple[float, float]]:
    """按各行字符数比例分配时间区间（上游是等分，长短行不均时会失步）。"""
    total = sum(len(ln) for ln in lines) or 1
    duration = max(end - start, 0.0)
    spans, cursor = [], start
    for i, ln in enumerate(lines):
        if i == len(lines) - 1:
            spans.append((cursor, end))
        else:
            nxt = cursor + duration * (len(ln) / total)
            spans.append((cursor, nxt))
            cursor = nxt
    return spans


# ---------------------------------------------------------------- 校验命令

def cmd_verify(args) -> int:
    """校验 AI 改写后的 SRT 是否保持了原始时间轴与条数。"""
    src, dst = parse_srt(args.original), parse_srt(args.modified)
    problems: list[str] = []

    if len(src) != len(dst):
        problems.append(f"条数不一致：原始 {len(src)} 条，修改后 {len(dst)} 条")

    for a, b in zip(src, dst):
        if abs(a.start - b.start) > 1e-3 or abs(a.end - b.end) > 1e-3:
            problems.append(
                f"#{a.index} 时间轴被改动："
                f"{format_timestamp(a.start)}->{format_timestamp(a.end)} 变为 "
                f"{format_timestamp(b.start)}->{format_timestamp(b.end)}"
            )

    if problems:
        print("❌ 校验未通过：")
        for p in problems[:30]:
            print(f"   - {p}")
        if len(problems) > 30:
            print(f"   ... 另有 {len(problems) - 30} 项")
        return 1

    changed = sum(1 for a, b in zip(src, dst) if a.text != b.text)
    print(f"✅ 时间轴与条数一致（{len(src)} 条），其中 {changed} 条正文有改动")
    return 0


def cmd_stats(args) -> int:
    cues = parse_srt(args.srt)
    if not cues:
        print("❌ 未解析到任何字幕条目")
        return 1
    lengths = [len(c.text.replace("\n", "")) for c in cues]
    over = [c for c, n in zip(cues, lengths) if n > args.max_chars]
    total = cues[-1].end - cues[0].start
    print(f"条数        : {len(cues)}")
    print(f"总时长      : {format_timestamp(total)}")
    print(f"平均字数/条 : {sum(lengths) / len(lengths):.1f}")
    print(f"最长一条    : {max(lengths)} 字")
    print(f"超过 {args.max_chars} 字 : {len(over)} 条")
    for c in over[:10]:
        print(f"   #{c.index} ({len(c.text)}字) {c.text[:40]}")
    return 0


def is_subsequence(needle: str, haystack: str) -> bool:
    """needle 的字符是否按原顺序出现在 haystack 中。

    字卡是对原句的“删字浓缩”，例如把「第一步是检查系统设定」压成
    「第一步检查系统设定」。严格子串匹配会误杀这种正常操作，而子序列
    匹配既允许删字，又能拦住凭空编造的内容。
    """
    it = iter(haystack)
    return all(ch in it for ch in needle)


def cmd_cards(args) -> int:
    cues = parse_srt(args.srt)
    problems: list[str] = []
    total_cards = 0

    for cue in cues:
        plain = cue.text.replace(" ", "").replace("\n", "")
        for card in cue.cards:
            total_cards += 1
            m = CARD_RE.match(card.strip())
            if not m:
                problems.append(f"#{cue.index} 字卡格式不合法：{card}")
                continue
            word, ctype = m.group(1), m.group(2)
            if ctype not in VALID_CARD_TYPES:
                problems.append(f"#{cue.index} 未知字卡类型：{ctype}")
            if not (args.min_word <= len(word) <= args.max_word):
                problems.append(
                    f"#{cue.index} 字卡字数 {len(word)} 超出 "
                    f"{args.min_word}-{args.max_word} 区间：{word}"
                )
            if not is_subsequence(word.replace(" ", ""), plain):
                problems.append(f"#{cue.index} 字卡含原文之外的内容（疑似编造）：{word}")

    expected = (len(cues) // 10) * 2
    if total_cards < expected:
        problems.append(f"字卡密度不足：{len(cues)} 条字幕仅 {total_cards} 张卡，建议至少 {expected} 张")

    if problems:
        print(f"❌ 字卡校验发现 {len(problems)} 个问题：")
        for p in problems[:30]:
            print(f"   - {p}")
        return 1
    print(f"✅ 字卡校验通过：{len(cues)} 条字幕，{total_cards} 张字卡")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="SRT 工具集")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_v = sub.add_parser("verify", help="校验时间轴与条数未被改动")
    p_v.add_argument("original")
    p_v.add_argument("modified")
    p_v.set_defaults(func=cmd_verify)

    p_s = sub.add_parser("stats", help="统计字幕信息")
    p_s.add_argument("srt")
    p_s.add_argument("--max-chars", type=int, default=22)
    p_s.set_defaults(func=cmd_stats)

    p_c = sub.add_parser("cards", help="校验字卡格式与密度")
    p_c.add_argument("srt")
    p_c.add_argument("--min-word", type=int, default=6)
    p_c.add_argument("--max-word", type=int, default=16)
    p_c.set_defaults(func=cmd_cards)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
