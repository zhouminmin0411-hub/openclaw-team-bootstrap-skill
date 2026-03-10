---
name: openclaw-team-bootstrap
description: Scan the current OpenClaw environment, generate a Markdown draft for multi-agent team routing, then apply the confirmed draft into openclaw.json with validation and rollback. Use when a user wants to initialize or reconfigure Discord channel or Feishu group routing without hand-editing config.
---

# Team Bootstrap

Use this skill when a user wants to initialize or reconfigure multi-agent team routing from the current OpenClaw environment instead of hand-editing `openclaw.json`.

Supported platforms:

- Discord channels
- Feishu groups

## Workflow

Phase 1: scan the environment and generate a human-editable draft markdown file.

Phase 2: review the draft, preview the config changes, then apply and validate.

## Script

Use `scripts/discord_team_bootstrap.py`.

The script is reusable and no longer assumes a fixed `/root/...` layout.

Default paths:

- config: `~/.openclaw/openclaw.json`
- draft: `<skill-dir>/team-setup.draft.md`
- report: `<skill-dir>/team-setup.report.md`
- validate script: `~/.openclaw/scripts/validate-openclaw-config.py`

Override with CLI flags or environment variables when needed:

- `--skill-dir` / `OPENCLAW_TEAM_BOOTSTRAP_SKILL_DIR`
- `--draft-path` / `OPENCLAW_TEAM_BOOTSTRAP_DRAFT_PATH`
- `--report-path` / `OPENCLAW_TEAM_BOOTSTRAP_REPORT_PATH`
- `--config-path` / `OPENCLAW_TEAM_BOOTSTRAP_CONFIG_PATH`
- `--validate-script` / `OPENCLAW_TEAM_BOOTSTRAP_VALIDATE_SCRIPT`
- `--openclaw-bin` / `OPENCLAW_TEAM_BOOTSTRAP_OPENCLAW_BIN`
- `--fallback-workspace` / `OPENCLAW_TEAM_BOOTSTRAP_FALLBACK_WORKSPACE`

## Commands

From the skill directory:

### 1) Scan environment and generate draft

```bash
python3 scripts/discord_team_bootstrap.py scan
```

Optional:

```bash
python3 scripts/discord_team_bootstrap.py scan \
  --guild-id <guildId> \
  --roles role-a,role-b \
  --platforms discord,feishu
```

`scan` auto-detects base roles from `agents.list` when `--roles` is omitted.

### 2) Explain the generated draft

```bash
python3 scripts/discord_team_bootstrap.py explain
```

### 3) Preview or apply the draft into openclaw.json

Dry-run first:

```bash
python3 scripts/discord_team_bootstrap.py apply --dry-run
```

Apply with validation:

```bash
python3 scripts/discord_team_bootstrap.py apply --validate
```

### 4) Inspect current config against the enabled draft

```bash
python3 scripts/discord_team_bootstrap.py inspect
```

`inspect` compares the enabled draft targets against the current config and reports:

- missing generated agents
- missing or extra bound roles
- `requireMention` mismatches

## Draft fields

### Roles table

Includes:

- `role`
- `base_agent_id`
- `discord_account`
- `feishu_account`
- `default_model`
- `workspace`
- `discord_mentions`

`discord_mentions` is a `;`-separated list of regex or mention patterns. If blank, the script falls back to matching the role name.

### Targets table

Includes:

- `platform`
- `peer_id`
- `target_name`
- `enable`
- `roles`
- `require_mention`
- `custom_models`
- `notes`

`require_mention` supports either:

- a single boolean: `true` / `false`
- per-role mapping: `role-a=false;role-b=true`

`custom_models` format:

- `role-a=model-a;role-b=model-b`

## Behavior

### Discord

The script manages:

- per-target generated agents: `*_ch_<channelId>`
- Discord channel bindings
- `channels.discord.accounts.<account>.guilds.<guildId>.channels.<channelId>.requireMention`
- `allowBots=true` for managed accounts

### Feishu

The script manages:

- per-target generated agents: `*_fg_<groupId>`
- Feishu group bindings
- `channels.feishu.accounts.<account>.groups.<groupId>.requireMention`
- `groupAllowFrom` when Feishu config uses `groupPolicy=allowlist`

Important: on Feishu, `requireMention=false` only lowers the reply gate. Message delivery still depends on Feishu app permissions.

## Safety

- `apply` creates a backup before modifying config
- invalid drafts fail fast instead of silently skipping rows
- `--validate` runs config validation and gateway health
- on validation failure, the script restores the backup automatically
