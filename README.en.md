# TopCap

**Turn Douyin and Bilibili videos into full-text Markdown transcripts, filed straight into Obsidian.**

Give it a topic keyword, a video link, or a creator's profile page. TopCap pulls the content list from
[TikHub](https://tikhub.io), transcribes speech locally with SenseVoice (or uses Bilibili CC subtitles when
present), fixes typos and paragraphs with an LLM, and writes one Markdown file per video, with YAML frontmatter,
into your Obsidian vault.

It is also an **agent skill**: once installed, let Claude Code, Codex or any Agent-Skills-aware tool pick and run.

[中文](./README.md)

```bash
topcap search "手冲咖啡入门"            # list candidates, download nothing
topcap run "手冲咖啡入门" --pick 1,3,5    # transcript → correction → Markdown in your vault
```

## Highlights

- **Lightweight**: one `pip install`. No torch, no ffmpeg, a 165 MB speech model, 15x realtime on CPU.
- **Full transcripts, never summaries**: correction may only change characters, segmentation may only insert
  newlines. Both guards are enforced in code; the model never gets a chance to rewrite.
- **Three entry points**: topic search, video link, creator profile. Paste the whole share text, no URL trimming.
- **Subtitles first on Bilibili**: videos with CC subtitles finish in seconds.
- **Built for Obsidian**: frontmatter with source, author, url, duration, likes and transcript origin.
- **Agent friendly**: two-stage commands. The agent judges relevance, the CLI does the mechanical work.

## Install

Python 3.12+.

```bash
pipx install topcap          # or
uv tool install topcap
topcap init                  # asks for TikHub key, LLM key, vault folder; downloads the model
topcap doctor
```

- **TikHub API key** (required): sign up at [user.tikhub.io](https://user.tikhub.io). Most calls cost $0.001,
  Douyin search $0.01 per page.
- **LLM key** (optional): any OpenAI-compatible endpoint, DeepSeek by default. Leave empty to skip correction.
  Ollama works too: set `base_url` to `http://localhost:11434/v1`.
- **Vault folder**: a directory inside your Obsidian vault. Each library gets its own subfolder.

Config lives in `~/.topcap/config.toml`; `TIKHUB_API_KEY`, `LLM_API_KEY` and `TOPCAP_VAULT` override it.

## Usage

```bash
topcap search "手冲咖啡入门"                          # topic: Douyin + Bilibili, 2 pages each
topcap search "https://v.douyin.com/xxx/"             # one video → library「收件箱」(--topic to rename)
topcap search "https://space.bilibili.com/12345"      # creator → library「@name」
topcap run "手冲咖啡入门" --pick 1,3,5
topcap run "@name" --pick all
topcap run "手冲咖啡入门" --pick 11 --allow-long       # videos over 20 min need explicit consent
topcap run "手冲咖啡入门" --pick 2 --no-subtitle       # force local ASR on Bilibili
```

`search` never downloads media and can be repeated on the same library to append candidates; existing
indices stay stable. `run` writes finished Markdown into the vault; raw transcripts and candidate lists stay in
`~/.topcap/work/<library>/`, media is deleted after transcription.

## As an agent skill

[`skills/topcap/SKILL.md`](./skills/topcap/SKILL.md) follows the [Agent Skills](https://agentskills.io) spec.
Copy or symlink `skills/topcap` into `~/.claude/skills/` (Claude Code) or your tool's skills directory, then ask:
"collect transcripts about pour-over coffee". The agent runs `search`, picks by relevance, runs `run`, and comes
back to you before processing long videos or a creator's whole catalogue.

## GPU (optional)

CPU by default. For NVIDIA GPUs install the CUDA build of sherpa-onnx (not on PyPI):

```bash
pip install sherpa-onnx==1.13.8+cuda12.cudnn9 -f https://k2-fsa.github.io/sherpa/onnx/cuda.html
```

Requires CUDA 12 and cuDNN 9 runtimes. `topcap doctor` shows the active backend.

## Design

Glossary in [CONTEXT.md](./CONTEXT.md); decisions and their trade-offs in [docs/adr/](./docs/adr/):
sherpa-onnx + SenseVoice over Whisper, two-stage commands with the agent picking, two guarded LLM calls for
correction and segmentation, direct TikHub REST, Bilibili subtitles first, and no reading UI because Obsidian is
the review surface.

## Out of scope

WeChat, Xiaohongshu, WeChat Channels. English-heavy content (use Whisper). Byte-for-byte reproducibility.

## License

MIT
