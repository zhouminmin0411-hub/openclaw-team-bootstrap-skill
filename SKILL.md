---
name: discord-team-bootstrap
description: Scan the current OpenClaw environment, generate a Markdown draft for multi-agent team setup, then apply the confirmed draft into openclaw.json with validation and rollback. Supports Discord channels and Feishu groups, and is meant for reusable team bootstrap / reconfiguration workflows without relying on Notion as the source of truth.
---

# Team Bootstrap

Use this skill when a user wants to initialize or reconfigure a multi-agent team setup from the current environment, without relying on Notion as the source of truth.

Supported platforms now include:
- Discord channels
- Feishu groups

## What this skill does

Phase 1: scan current environment and generate a human-editable draft markdown.
Phase 2: read the draft markdown, apply config changes, validate, and report.

## Commands

### 1) Scan environment and generate draft

```bash
python3 /root/clawd/skills/discord-team-bootstrap/scripts/discord_team_bootstrap.py scan
```

Optional:

```bash
python3 /root/clawd/skills/discord-team-bootstrap/scripts/discord_team_bootstrap.py scan \
  --guild-id <guildId> \
  --roles trouble,friday \
  --platforms discord,feishu
```

### 2) Explain the generated draft

```bash
python3 /root/clawd/skills/discord-team-bootstrap/scripts/discord_team_bootstrap.py explain
```

### 3) Preview or apply the draft into openclaw.json

Dry-run first:

```bash
python3 /root/clawd/skills/discord-team-bootstrap/scripts/discord_team_bootstrap.py apply --dry-run
```

Apply:

```bash
python3 /root/clawd/skills/discord-team-bootstrap/scripts/discord_team_bootstrap.py apply
```

Optional validation:

```bash
python3 /root/clawd/skills/discord-team-bootstrap/scripts/discord_team_bootstrap.py apply --validate
```

### 4) Refresh draft after server/channel/group changes

```bash
python3 /root/clawd/skills/discord-team-bootstrap/scripts/discord_team_bootstrap.py scan
```

Use this when new Discord channels were added or Feishu groups were added / bound.

### 5) Inspect current config against draft

```bash
python3 /root/clawd/skills/discord-team-bootstrap/scripts/discord_team_bootstrap.py inspect
```

## Ongoing maintenance

After initialization, continue using this skill for later adjustments instead of hand-editing `openclaw.json`.

Recommended workflow:

1. Edit draft: `/root/clawd/skills/discord-team-bootstrap/discord-team-setup.draft.md`
2. Preview changes:

```bash
python3 /root/clawd/skills/discord-team-bootstrap/scripts/discord_team_bootstrap.py apply --dry-run
```

3. Apply changes:

```bash
python3 /root/clawd/skills/discord-team-bootstrap/scripts/discord_team_bootstrap.py apply --validate
```

## Draft format

### Roles table

Includes:
- `role`
- `base_agent_id`
- `discord_account`
- `feishu_account`
- `default_model`

### Targets table

Includes:
- `platform` → `discord` / `feishu`
- `peer_id` → Discord channel id or Feishu `oc_xxx` / `chat:oc_xxx`
- `target_name`
- `enable`
- `roles`
- `require_mention`
- `custom_models`
- `notes`

`require_mention` supports either:
- single boolean: `true` / `false`
- per-role mapping: `trouble=false;friday=true`

`custom_models` format:
- `trouble=rightcode/gpt-5.4;friday=rightcode/gpt-5.4-codex`

## Platform behavior

### Discord

The script manages:
- `agents.list` → `*_ch_<channelId>`
- `bindings[]` for Discord channels
- `channels.discord.accounts.<account>.guilds.<guildId>.channels.<channelId>.requireMention`
- `allowBots=true` for the managed Discord accounts

### Feishu

The script manages:
- `agents.list` → `*_fg_<chatId>`
- `bindings[]` for Feishu groups
- `channels.feishu.accounts.<account>.groups.<chatId>.requireMention`
- If Feishu account / top-level config uses `groupPolicy=allowlist`, the target group id is appended into `groupAllowFrom`

Important: on Feishu, `requireMention=false` only lowers the reply gate. Actual ability to receive all group messages still depends on the app/plugin permissions.

## Files

- Draft: `/root/clawd/skills/discord-team-bootstrap/discord-team-setup.draft.md`
- Report: `/root/clawd/skills/discord-team-bootstrap/discord-team-setup.report.md`
- Script: `scripts/discord_team_bootstrap.py`

## Safety

- `apply` creates a backup before modifying config.
- `--validate` runs config validation and gateway health check.
- On validation failure, the script restores the backup automatically.
- The script only manages team routing declared in the draft.
