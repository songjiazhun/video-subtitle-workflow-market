# video-subtitle-workflow 技能市场

一个 CodeBuddy / WorkBuddy 技能市场清单，提供 **影片字幕全流程工作流** 技能。

## 技能能力

音频/视频 → SRT 字幕 → 对照原稿优化 → 画面字卡标注 → 社群平台影片介绍，一条龙。

- 本地 Whisper 转录（`faster-whisper`），支持 `tiny / base / small / medium` 模型
- 模型权重可从 ModelScope / HuggingFace 预下载，**可完全离线运行**
- 阶段 2/3 由模型逐条语义处理，脚本只做机械校验
- 输出固定命名：`origin.srt` → `enhanced.srt` → `reference-cards.srt` → `summary.md`

## 作为市场安装（推荐）

把本仓库作为自定义市场添加到 CodeBuddy：

1. CodeBuddy 设置 → 市场 / Marketplace → 添加市场
2. 填写本仓库的 git 地址
3. 刷新后，在技能市场搜索 `video-subtitle-workflow`，一键安装

或手动在 `~/.codebuddy/plugins/known_marketplaces.json` 增加一项（参照环境内 `cb_teams_marketplace` 写法）。

## 本地直接安装（免市场）

```bash
# 方式一：解压安装
unzip video-subtitle-workflow.zip -d ~/.codebuddy/skills/

# 方式二：自解压安装器
bash install-skill.sh
```

安装后，把任意音频/视频丢给 CodeBuddy 并说「转字幕 / 生成 SRT / 加字卡 / 写影片介绍」即可触发。

## 仓库结构

```
.codebuddy-plugin/marketplace.json          # 市场清单
plugins/video-subtitle-workflow/
  skills/video-subtitle-workflow/           # 技能本体（SKILL.md + scripts/references/assets）
```

## 许可

MIT
