# TopCap

**把抖音、B站视频批量转成完整文案的 Markdown，直接归档进 Obsidian。**

给一个主题词、一条视频链接、或一位博主的主页，TopCap 从 [TikHub](https://tikhub.io) 拉取内容清单，
在你本机用 SenseVoice 做语音识别（B站有 CC 字幕就直接用字幕），用 LLM 纠错分段，
每条视频产出一份带 frontmatter 的 Markdown，写进你的 Obsidian 知识库。

它同时是一个 **agent skill**：装好之后让 Claude Code、Codex 这类 agent 帮你挑选和跑。

[English](./README.en.md)

```bash
topcap search "手冲咖啡入门"          # 列出候选，不下载任何媒体
topcap run "手冲咖啡入门" --pick 1,3,5  # 取文案 → 纠错分段 → 写入知识库
```

```markdown
---
title: "保姆级手冲咖啡完整版教程，新手快来学"
source: 抖音
author: "李李咖啡生活"
url: https://www.douyin.com/video/7582523403506240814
published: 2025-12-11
duration: 62
likes: 14887
library: "手冲咖啡入门"
transcript_source: asr
tags: [topcap, 抖音]
---

# 保姆级手冲咖啡完整版教程，新手快来学

手冲咖啡应该怎么做？今天就来教大家一套完整的手冲咖啡操作流程。单人喝的量的话，一般准备15克的咖啡豆……
```

## 特点

- **轻量**：`pip install` 一行装完。不装 torch，不装 ffmpeg，语音模型 165MB，纯 CPU 也有十几倍实时。
- **完整文案，不是摘要**：纠错只改字、分段只插换行，两道守卫在代码里强制执行，模型没有机会改写。
- **三种入口**：主题词搜索、视频链接、博主主页。分享文案整段粘进去就行，不用自己截链接。
- **B站优先用字幕**：有 CC 字幕的视频几秒出稿，不占 GPU 和带宽。
- **为 Obsidian 设计**：YAML frontmatter 带来源、作者、链接、时长、点赞、文案来源，Dataview 直接可查。
- **agent 友好**：两段式命令，相关性筛选交给 agent，机械活交给 CLI。

## 安装

需要 Python 3.12 以上。二选一：

```bash
pipx install topcap          # 或
uv tool install topcap
```

然后配置（会问 TikHub key、LLM key、知识库目录，并下载语音模型）：

```bash
topcap init
topcap doctor                # 自检
```

- **TikHub API key**（必填）：在 [user.tikhub.io](https://user.tikhub.io) 注册获取。大多数接口每次 0.001 美元，
  抖音搜索每页 0.01 美元。
- **LLM key**（选填）：任意 OpenAI 兼容服务，默认 DeepSeek。一个主题约几毛钱。留空则成稿只做繁转简和清理，
  不纠错不分段。ollama 也是兼容的，`base_url` 填 `http://localhost:11434/v1` 即是本地方案。
- **知识库目录**：Obsidian vault 下的一个文件夹。成稿每个库一个子文件夹。

配置文件在 `~/.topcap/config.toml`，环境变量 `TIKHUB_API_KEY` / `LLM_API_KEY` / `TOPCAP_VAULT` 可覆盖。

## 用法

```bash
# 主题
topcap search "手冲咖啡入门"                  # 抖音 + B站各搜 2 页，约 $0.02
topcap search "手冲咖啡入门" --pages 3

# 视频链接（分享文案整段粘进去也行）
topcap search "7.87 复制打开抖音，看看【…】 https://v.douyin.com/xxx/ 复制此链接…"
topcap search "https://www.bilibili.com/video/BV1xxx" --topic 我的收藏

# 博主主页
topcap search "https://space.bilibili.com/12345"
topcap search "https://www.douyin.com/user/MS4wLjABAAAA…"

# 加工
topcap run "手冲咖啡入门" --pick 1,3,5
topcap run "@某博主" --pick all
topcap run "手冲咖啡入门" --pick 11 --allow-long     # 超过 20 分钟的内容要显式确认
topcap run "手冲咖啡入门" --pick 2 --no-subtitle     # B站不用字幕，强制本地转写
```

`search` 只列清单不下载，同一个库可以反复 `search` 追加，已有序号不变。
`run` 把成稿写进知识库，原始文案和候选清单留在 `~/.topcap/work/<库名>/`，媒体转写完即删。

## 作为 agent skill 使用

仓库根目录的 [`skills/topcap/SKILL.md`](./skills/topcap/SKILL.md) 按 [Agent Skills](https://agentskills.io) 规范编写。

- **Claude Code**：把 `skills/topcap` 复制或链接到 `~/.claude/skills/topcap`
- **Codex / 其他支持 Agent Skills 的工具**：放到各自的 skills 目录

然后直接说「帮我收集一下手冲咖啡入门的视频文案」，agent 会跑 `search`、按相关性挑选、再跑 `run`。
超过 20 分钟的内容和博主库全量抓取会回来问你确认。

## GPU 加速（可选）

默认 CPU 推理，10 分钟视频约 40 秒。有 NVIDIA 显卡的话装 CUDA 版 sherpa-onnx（200MB，不在 PyPI 上）：

```bash
pip install sherpa-onnx==1.13.8+cuda12.cudnn9 -f https://k2-fsa.github.io/sherpa/onnx/cuda.html
```

需要本机有 CUDA 12 和 cuDNN 9 的运行库。`topcap doctor` 会显示当前推理后端。

## 设计

| 用途 | 文档 |
|---|---|
| 术语定义 | [CONTEXT.md](./CONTEXT.md) |
| 设计决策及其代价 | [docs/adr/](./docs/adr/) |
| agent 调用规范 | [skills/topcap/SKILL.md](./skills/topcap/SKILL.md) |

六条关键决策：转写用 sherpa-onnx 加载 SenseVoice 而非 Whisper（中文错误率约为一半、快 15 倍、不依赖 torch）；
两段式命令把相关性筛选交给 agent；纠错与分段拆成两次各带守卫的 LLM 调用；直接调 TikHub REST；
B站优先用 CC 字幕；不做阅读页，Obsidian 就是复筛界面。

## 范围外

- 微信公众号、小红书、视频号：不做。TopCap 只做抖音和 B站。
- 英文内容：SenseVoice 的强项是中文，英文建议另用 Whisper。
- 逐字可复现：LLM 在 temperature 0 下仍不确定。文案完整性由守卫保证，逐字一致性不作保证。

## 许可

MIT
