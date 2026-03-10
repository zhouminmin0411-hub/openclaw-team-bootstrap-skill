# openclaw-team-bootstrap-skill

A reusable OpenClaw skill for bootstrapping and reconfiguring multi-agent team routing across **Discord channels** and **Feishu groups**.

## What it does

This skill scans the current OpenClaw environment, generates a human-editable Markdown draft, and then applies the confirmed team routing configuration back into `openclaw.json` with validation and rollback.

It is designed for users who want a reusable team-initialization workflow without relying on Notion as the source of truth.

## Supported platforms

- Discord
- Feishu

## Key capabilities

- Discover current Discord channels and Feishu groups
- Generate a unified draft table for team routing targets
- Configure per-target roles
- Configure per-role `requireMention` behavior
- Configure per-role custom models
- Create per-target agents automatically
- Write bindings and channel/group config back into `openclaw.json`
- Validate config before keeping changes
- Restore backup automatically if validation fails

## Draft fields

The generated draft contains a `## Targets` table with these columns:

- `platform`
- `peer_id`
- `target_name`
- `enable`
- `roles`
- `require_mention`
- `custom_models`
- `notes`

### `require_mention`

Supports either:

- single boolean: `true` / `false`
- per-role mapping: `trouble=false;friday=true`

### `custom_models`

Format:

```text
trouble=rightcode/gpt-5.4;friday=rightcode/gpt-5.4-codex
```

## Files

- `SKILL.md` — skill definition and usage guide
- `scripts/discord_team_bootstrap.py` — main script

## Usage

### 1. Scan and generate draft

```bash
python3 scripts/discord_team_bootstrap.py scan --platforms discord,feishu
```

### 2. Explain draft

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

### 5. Inspect current draft

```bash
python3 scripts/discord_team_bootstrap.py inspect
```

## Feishu note

For Feishu, setting `requireMention=false` only lowers the reply gate. Whether the bot can actually receive all group messages still depends on the app/plugin permissions granted in Feishu.

## Safety

- Creates a backup before apply
- Restores backup automatically on validation failure
- Uses dry-run preview mode before real apply
- Keeps the workflow draft-driven and reviewable
