# AGENTS.md

## Agent skills

### Issue tracker

Issues are tracked as GitHub Issues in this repo, via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Default vocabulary: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `CONTEXT.md` plus `docs/adr/` at the repo root. See `docs/agents/domain.md`.

## Project

TopCap：抖音、B站视频 → 完整文案 Markdown → Obsidian。术语见 `CONTEXT.md`，决策见 `docs/adr/`，
agent 调用规范见 `skills/topcap/SKILL.md`（那是唯一的真相源）。

### Development

```bash
uv sync --group dev        # 装依赖（开发用 uv；用户侧走 pip / pipx）
uv run pytest -q           # 只测纯函数，不碰网络和模型
uv run topcap doctor
```

- 配置在 `~/.topcap/config.toml`。测试时用 `TOPCAP_HOME=<临时目录>` 隔离。
- 不改写文案的守卫在 `src/topcap/text.py`，改纠错或分段逻辑必须保住 `guard_correction` 和锚点切分。
- 语音模型钉在 `config.MODEL_TARBALL`，换模型前先用中文样本比对（见 ADR 0001）。
