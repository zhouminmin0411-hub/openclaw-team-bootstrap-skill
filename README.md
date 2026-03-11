# openclaw-team-bootstrap-skill

A reusable OpenClaw skill for bootstrapping and reconfiguring multi-agent routing across Discord channels and Feishu groups.

## What it does

The workflow is draft-driven:

1. Scan the current OpenClaw environment.
2. Generate a human-editable Markdown draft.
3. Review and edit roles, targets, mention policy, and per-role models.
4. Preview or apply the resulting routing changes back into `openclaw.json`.

The script creates a backup before apply, can validate the updated config, and restores the backup automatically if validation fails.

## Supported platforms

- Discord
- Feishu

## Public-friendly defaults

This repository no longer assumes a fixed `/root/...` layout.

Default runtime paths:

- Skill directory: the repository root that contains this script
- Draft file: `team-setup.draft.md`
- Report file: `team-setup.report.md`
- OpenClaw config: `~/.openclaw/openclaw.json`
- Validation script: `~/.openclaw/scripts/validate-openclaw-config.py`

Override any of them with CLI flags or environment variables:

- `--skill-dir` / `OPENCLAW_TEAM_BOOTSTRAP_SKILL_DIR`
- `--draft-path` / `OPENCLAW_TEAM_BOOTSTRAP_DRAFT_PATH`
- `--report-path` / `OPENCLAW_TEAM_BOOTSTRAP_REPORT_PATH`
- `--config-path` / `OPENCLAW_TEAM_BOOTSTRAP_CONFIG_PATH`
- `--validate-script` / `OPENCLAW_TEAM_BOOTSTRAP_VALIDATE_SCRIPT`
- `--openclaw-bin` / `OPENCLAW_TEAM_BOOTSTRAP_OPENCLAW_BIN`
- `--fallback-workspace` / `OPENCLAW_TEAM_BOOTSTRAP_FALLBACK_WORKSPACE`

## Draft format

The generated draft contains:

- `## Roles`
- `## Targets`

### Roles columns

- `role`
- `base_agent_id`
- `discord_account`
- `feishu_account`
- `default_model`
- `workspace`
- `discord_mentions`

`discord_mentions` accepts `;`-separated regex or mention patterns. If left empty, the script falls back to matching the role name.

### Targets columns

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
- a per-role mapping: `role-a=false;role-b=true`

`custom_models` format:

```text
role-a=model-a;role-b=model-b
```

## Usage

Run commands from the repository root.

### 1. Generate a draft

```bash
python3 scripts/discord_team_bootstrap.py scan --platforms discord,feishu
```

By default, `scan` auto-detects base roles from `agents.list` and skips generated `*_ch_*` / `*_fg_*` agents. You can pin roles explicitly:

```bash
python3 scripts/discord_team_bootstrap.py scan \
  --roles role-a,role-b \
  --guild-id <discordGuildId>
```

### 2. Explain the draft

```bash
python3 scripts/discord_team_bootstrap.py explain
```

### 3. Preview changes

```bash
python3 scripts/discord_team_bootstrap.py apply --dry-run
```

### 4. Apply changes with validation

```bash
python3 scripts/discord_team_bootstrap.py apply --validate
```

For long-running or timeout-prone hosts, use the stepwise workflow instead:

```bash
python3 scripts/discord_team_bootstrap.py apply
python3 scripts/discord_team_bootstrap.py validate
python3 scripts/discord_team_bootstrap.py health
```

### 5. Inspect drift between draft and current config

```bash
python3 scripts/discord_team_bootstrap.py inspect
```

`inspect` now compares the enabled draft targets with the current config and reports missing roles, missing generated agents, and `requireMention` mismatches.

### 6. Check a single target across draft, config, and runtime

```bash
python3 scripts/discord_team_bootstrap.py check-target --platform discord --peer-id <channelId>
python3 scripts/discord_team_bootstrap.py check-target --platform feishu --peer-id <groupId>
```

Use `check-target` when you want to debug one specific channel or group. It reports:

- the current draft state
- the `openclaw.json` binding state
- configured model vs latest observed runtime model
- config validation and gateway health summaries

## Platform behavior

### Discord

The script manages:

- generated per-target agents in `agents.list` as `*_ch_<channelId>`
- Discord channel bindings in `bindings[]`
- `channels.discord.accounts.<account>.guilds.<guildId>.channels.<channelId>.requireMention`
- `allowBots=true` for managed Discord accounts

### Feishu

The script manages:

- generated per-target agents in `agents.list` as `*_fg_<groupId>`
- Feishu group bindings in `bindings[]`
- `channels.feishu.accounts.<account>.groups.<groupId>.requireMention`
- `groupAllowFrom` when account-level or top-level Feishu config uses `groupPolicy=allowlist`

Important: on Feishu, `requireMention=false` only lowers the reply gate. Whether the bot can receive all group messages still depends on Feishu-side permissions.

## Validation and safety

- `apply` creates `openclaw.json.bak.discord-team-bootstrap`
- `apply --validate` now writes progress to the report after each phase
- `validate` runs only the validation script
- `health` runs only `openclaw gateway health`
- if validation fails, the script restores the backup automatically
- invalid draft rows now fail fast instead of silently skipping targets
- if the host interrupts `apply --validate`, the report should still show the last completed phase and recommended follow-up checks

## Limits

- Feishu discovery is config-based, not API-based. New groups that have never appeared in config or bindings will not be discovered automatically.
- Markdown table parsing is intentionally lightweight; keep the table structure unchanged.

## Mention priority rule

When configuring multi-bot targets, treat an explicit bot mention as a higher-priority signal than the default reply policy. The intended behavior is:

- if a message explicitly mentions one bot, that bot should be the only default responder for that message
- other bots should stay silent unless they were also explicitly mentioned
- only when no bot was explicitly mentioned should the default `requireMention=false` responders answer

This skill can encode `requireMention` and mention patterns, but strict enforcement of this rule still depends on OpenClaw runtime behavior. Use `check-target` to surface this expectation in reports when debugging target behavior.
