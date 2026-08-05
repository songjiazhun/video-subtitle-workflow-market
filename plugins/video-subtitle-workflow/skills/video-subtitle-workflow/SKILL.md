---
name: video-subtitle-workflow
description: 影片字幕全流程工作流：音频/视频转 SRT 字幕、对照原稿优化字幕、生成画面字卡标注、产出社群平台影片介绍。当用户要求「把音频转成字幕」「生成 SRT」「转逐字稿」「优化字幕」「比对原稿修正字幕」「加字卡」「字幕加标注」「根据字幕生成影片介绍」「生成社群贴文」，或提到 whisper 转录、SRT 文件处理、影片文案时使用。也支持一次性跑完整条流水线。
---

# 影片字幕工作流

从一个音频文件出发，串起四个阶段：**转字幕 → 优化 → 字卡 → 影片介绍**。
每阶段产出一个固定命名的文件，下一阶段消费上一阶段的产物。

## 阶段总览

| 阶段 | 动作 | 输入 | 输出 | 执行方式 |
| --- | --- | --- | --- | --- |
| 1 | 音频转字幕 | 音频/视频 | `origin.srt` | 跑脚本 |
| 2 | 对照原稿优化 | `origin.srt` + `origin.md` | `enhanced.srt` | 模型逐条判断 |
| 3 | 添加字卡 | `enhanced.srt` | `reference-cards.srt` | 模型逐条判断 |
| 4 | 生成影片介绍 | `enhanced.srt` | `summary.md` | 模型撰写 |

**阶段 2、3 必须由模型逐条阅读处理，不要写脚本批量替换。** 这两步依赖语义
理解，正则改写只会制造新错误。脚本只负责阶段 1 的转录和各阶段的机械校验。

## 路由

先判断用户要做哪一步：

- 提到音频文件、要字幕/逐字稿 → **阶段 1**
- 手上已有 SRT，要修正错字/统一术语/加空格 → **阶段 2**
- 要字卡、标注、画面重点 → **阶段 3**
- 要影片介绍、社群贴文、YouTube 描述 → **阶段 4**
- 给了音频且要「一条龙」「全部搞定」 → **依次跑完 1→2→3→4**

跑完整流水线时，每个阶段结束后简短汇报产出（条数、字卡数等），
不要等到最后才一次性说明。

若用户只给了音频却要求阶段 2，先跑阶段 1 补上 `origin.srt`。

---

## 阶段 1 · 音频转字幕

脚本路径用**本 skill 目录的绝对路径**调用（SKILL.md 所在目录），
不要假设当前工作目录就是 skill 目录。产物写到用户的工作目录。

首次使用先自检环境（缺依赖时加 `--install` 自动装）：

```bash
python3 scripts/check_environment.py --install
```

转换：

```bash
python3 scripts/audio_to_srt.py <音频或视频文件>
python3 scripts/audio_to_srt.py input.m4a --max-chars 20 --model medium
```

常用参数：

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--max-chars` | 22 | 每行最多字数，最小 4 |
| `--min-chars` | 4 | 每行最少字数，过短的行会并入相邻行 |
| `--gap` | 0.3 | 小于该秒数的间隔自动闭合 |
| `--lang` | zh | 语言代码，`auto` 自动检测 |
| `--model` | small | `tiny`/`base`/`small`/`medium`/`large-v3` |
| `--engine` | faster-whisper | 可选 `openai-whisper` |
| `--output` | 同目录 origin.srt | 输出路径 |

支持 mp3/wav/m4a/flac/ogg/aac/wma/opus，以及 mp4/mov/mkv/avi/webm/flv
（视频自动抽音轨）。

模型选择：`small` 是速度与准确度的平衡点，够用；口音重或专业术语多再上
`medium`。`large-v3` 在纯 CPU 沙箱里会非常慢，除非用户明确要求，否则别用。
首次运行需要下载模型权重，耗时较长，属正常现象——用后台方式运行并告知用户。

**网络受限时**，先把权重下到本地，之后即可离线转录。三种拉取方式按推荐度排列：

```bash
# ① ModelScope 源（沙箱 / 国内最稳，推荐优先用）
python3 scripts/prepare_model.py --model small --source modelscope --verify

# ② HuggingFace 国内镜像 hf-mirror.com
python3 scripts/prepare_model.py --model small --mirror --verify

# ③ 走本地代理（如 Clash 默认 7890 端口）
python3 scripts/prepare_model.py --model small --proxy http://127.0.0.1:7890 --verify
```

```bash
# 下载完成后离线转录
python3 scripts/audio_to_srt.py <音频> --model ./models/small
```

- `--source modelscope`：仓库 `pengzhendong/faster-whisper-*`，一次性拉取完整模型，
  国内与沙箱环境最可靠，遇到 HF 官方源/镜像拉不到权重时首选它。
- `--mirror` 走 hf-mirror.com，`--proxy` 走本地代理，二者二选一。
- 不指定 `--source` 时默认 `auto`：先试 HuggingFace（含镜像回退），失败自动换 ModelScope。
- 还可以先 `python3 scripts/prepare_model.py --model small --check-only` 只核验本地权重是否齐全。

完成后检查一下结果：

```bash
python3 scripts/srt_utils.py stats origin.srt
```

---

## 阶段 2 · 对照原稿优化

**前置条件**：同目录需要有原稿 `origin.md`。没有就问用户要，或确认是否
跳过这一步直接进入阶段 3——不要自己编原稿。

完整规则读 `references/enhancement-rules.md`，要点：

- 时间戳、序号、条目数**一个字符都不能改**
- 只修正字幕里已有的内容，原稿里多出来的句子**不要补进去**
- 中英文、数字之间加半角空格（`Python3.9` → `Python 3.9`）
- 对照原稿纠正错别字和专有名词拼写
- 口头禅、语气词保留，那是讲者真实说的
- 拿不准时保持原样，保守优先

产出 `enhanced.srt` 后**必须**校验：

```bash
python3 scripts/srt_utils.py verify origin.srt enhanced.srt
```

校验不通过就改到通过为止，别把问题留给下一阶段。

---

## 阶段 3 · 添加字卡

先读 `references/card-types.yaml` 拿到四种字卡类型的定义和触发词。

在字幕正文的**下一行**追加字卡，不改动正文：

```
1
00:00:00,000 --> 00:00:03,500
今天要教大家如何避免常见的错误
[字卡](word:如何避免常见的错误 / type:红色警告)
```

规则：

- 格式固定 `[字卡](word:关键短语 / type:类型名)`
- 关键短语 6-16 字，**必须是本条字幕正文的「删字浓缩」**——可以删字，
  不能加字。加了原文没有的字会被校验拦下
- 每 10 条字幕至少 2 张卡，同一条字幕最多 1 张
- 四种类型：金色重点（核心结论）、白色提醒（建议提示）、
  红色警告（风险踩坑）、蓝色列点（步骤清单）
- 同时命中多类时，优先级：红色警告 > 蓝色列点 > 金色重点 > 白色提醒

处理节奏：先通读全文理解脉络，再挑出真正值得标注的段落，最后写卡。
不要边读边标，那样会在开头堆一堆卡、后面全空。

产出 `reference-cards.srt` 后校验：

```bash
python3 scripts/srt_utils.py cards reference-cards.srt
python3 scripts/srt_utils.py verify enhanced.srt reference-cards.srt
```

---

## 阶段 4 · 生成影片介绍

依据 `enhanced.srt`（没有就用 `origin.srt`），按
`assets/summary-template.md` 的结构写入 `summary.md`。
平台规范和钩子写法见 `references/platform-specs.md`。

默认三个平台全出（Facebook / Threads / YouTube），用户指定了就只出指定的。

动笔前先从字幕提炼：核心主题、目标受众、3-5 个关键收获、一句金句。
口播里的重复和跑题内容全部剔除。

YouTube 章节时间轴必须取自字幕真实时间戳，不能编。

---

## 输出语言

字幕内容默认沿用音频原本的语言和用字习惯。原稿是繁体就产出繁体，
简体就产出简体，**不要自作主张做繁简转换**。用户明确要求转换时再转。

## 常见问题

| 现象 | 原因与处理 |
| --- | --- |
| 首次运行卡很久 | 在下载模型权重，正常。放后台跑并告知用户 |
| 拉不到模型权重 | 用 `scripts/prepare_model.py` 预下载，再用 `--model <本地目录>` |
| 提示缺 ffmpeg | `sudo apt-get install -y ffmpeg` |
| 提示没有转写引擎 | `sudo pip3 install faster-whisper` |
| 断句太碎或太长 | 调 `--max-chars`，重跑阶段 1 |
| 识别准确率差 | 换 `--model medium`；确认 `--lang` 是否设对 |
| verify 报时间轴被改 | 阶段 2/3 动了时间戳，回退重做，只改正文 |
| cards 报「疑似编造」 | 字卡加了原文没有的字，改成纯删字浓缩 |
