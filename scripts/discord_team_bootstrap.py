#!/usr/bin/env python3
import argparse
import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_SKILL_DIR = SCRIPT_DIR.parent
DEFAULT_CONFIG_PATH = Path.home() / '.openclaw' / 'openclaw.json'
MANAGED_AGENT_RE = re.compile(r'.+_(ch|fg)_.+')
SUPPORTED_PLATFORMS = {'discord', 'feishu'}


@dataclass
class RuntimeConfig:
    skill_dir: Path
    draft_path: Path
    report_path: Path
    config_path: Path
    validate_script: Optional[Path]
    openclaw_bin: str
    fallback_workspace: str


def _resolve_path(raw: str) -> Path:
    return Path(raw).expanduser().resolve()


def _env(name: str) -> str:
    return os.environ.get(name, '').strip()


def build_runtime(args: Optional[argparse.Namespace] = None) -> RuntimeConfig:
    skill_dir_raw = getattr(args, 'skill_dir', '') or _env('OPENCLAW_TEAM_BOOTSTRAP_SKILL_DIR') or str(DEFAULT_SKILL_DIR)
    skill_dir = _resolve_path(skill_dir_raw)

    config_path_raw = getattr(args, 'config_path', '') or _env('OPENCLAW_TEAM_BOOTSTRAP_CONFIG_PATH') or str(DEFAULT_CONFIG_PATH)
    config_path = _resolve_path(config_path_raw)

    draft_path_raw = getattr(args, 'draft_path', '') or _env('OPENCLAW_TEAM_BOOTSTRAP_DRAFT_PATH') or str(
        skill_dir / 'team-setup.draft.md'
    )
    report_path_raw = getattr(args, 'report_path', '') or _env('OPENCLAW_TEAM_BOOTSTRAP_REPORT_PATH') or str(
        skill_dir / 'team-setup.report.md'
    )

    validate_script_raw = getattr(args, 'validate_script', '') or _env('OPENCLAW_TEAM_BOOTSTRAP_VALIDATE_SCRIPT')
    if validate_script_raw:
        validate_script = _resolve_path(validate_script_raw)
    else:
        validate_script = config_path.parent / 'scripts' / 'validate-openclaw-config.py'

    openclaw_bin = getattr(args, 'openclaw_bin', '') or _env('OPENCLAW_TEAM_BOOTSTRAP_OPENCLAW_BIN') or 'openclaw'
    fallback_workspace = (
        getattr(args, 'fallback_workspace', '')
        or _env('OPENCLAW_TEAM_BOOTSTRAP_FALLBACK_WORKSPACE')
        or str(Path.home())
    )

    return RuntimeConfig(
        skill_dir=skill_dir,
        draft_path=_resolve_path(draft_path_raw),
        report_path=_resolve_path(report_path_raw),
        config_path=config_path,
        validate_script=validate_script,
        openclaw_bin=openclaw_bin,
        fallback_workspace=fallback_workspace,
    )


RUNTIME = build_runtime()


def run(cmd: List[str], check: bool = True) -> subprocess.CompletedProcess:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise RuntimeError(f"Command not found: {cmd[0]}") from exc
    if check and p.returncode != 0:
        raise RuntimeError(
            f"Command failed ({p.returncode}): {' '.join(cmd)}\nSTDOUT:\n{p.stdout}\nSTDERR:\n{p.stderr}"
        )
    return p


def load_json(path: Path) -> dict:
    return json.loads(path.read_text())


def save_json(path: Path, data: dict):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')


def now_str() -> str:
    return datetime.now().astimezone().isoformat(timespec='minutes')


def parse_csv(text: str) -> List[str]:
    return [x.strip() for x in (text or '').split(',') if x.strip()]


def dedupe(values: List[str]) -> List[str]:
    out = []
    for value in values:
        if value and value not in out:
            out.append(value)
    return out


def normalize_peer_id(platform: str, raw: str) -> str:
    value = (raw or '').strip()
    if platform == 'feishu' and value.startswith('chat:'):
        return value.split(':', 1)[1].strip()
    return value


def agent_prefix_for(platform: str) -> str:
    return 'ch' if platform == 'discord' else 'fg'


def make_agent_id(role: str, platform: str, peer_id: str) -> str:
    return f"{role}_{agent_prefix_for(platform)}_{peer_id}"


def is_managed_agent_id(agent_id: str) -> bool:
    return bool(MANAGED_AGENT_RE.fullmatch(agent_id or ''))


def escape_md_cell(value: object) -> str:
    text = str(value or '')
    return text.replace('\\', '\\\\').replace('|', '\\|').replace('\n', '<br>')


def split_md_row(line: str) -> List[str]:
    text = line.strip()
    if text.startswith('|'):
        text = text[1:]
    if text.endswith('|'):
        text = text[:-1]

    parts = []
    current = []
    escape = False
    for char in text:
        if escape:
            current.append(char)
            escape = False
            continue
        if char == '\\':
            escape = True
            continue
        if char == '|':
            parts.append(''.join(current).strip())
            current = []
            continue
        current.append(char)
    if escape:
        current.append('\\')
    parts.append(''.join(current).strip())
    return parts


def parse_patterns(text: str) -> List[str]:
    return [x.strip() for x in (text or '').split(';') if x.strip()]


def serialize_patterns(patterns: List[str]) -> str:
    return ';'.join(dedupe([x.strip() for x in patterns if x and x.strip()]))


def detect_workspace(cfg: dict, role_name: str, base_agent_id: str, platform: str, fallback: str) -> str:
    prefix = f"{role_name}_{agent_prefix_for(platform)}_"
    for agent in cfg.get('agents', {}).get('list', []):
        if agent.get('id', '').startswith(prefix) and agent.get('workspace'):
            return agent['workspace']
    for agent in cfg.get('agents', {}).get('list', []):
        if agent.get('id') == base_agent_id and agent.get('workspace'):
            return agent['workspace']
    return fallback


def find_default_guild(cfg: dict) -> Optional[str]:
    discord = cfg.get('channels', {}).get('discord', {})
    guilds = set()
    for acc_cfg in discord.get('accounts', {}).values():
        if not isinstance(acc_cfg, dict):
            continue
        for guild_id in (acc_cfg.get('guilds') or {}).keys():
            guilds.add(guild_id)
    if guilds:
        return sorted(guilds)[0]
    return None


def list_discord_channels(guild_id: str) -> List[dict]:
    cmd = [RUNTIME.openclaw_bin, 'message', 'channel', 'list', '--channel', 'discord', '--guild-id', guild_id, '--json']
    p = run(cmd, check=False)
    if p.returncode != 0:
        return []
    text = p.stdout.strip()
    try:
        data = json.loads(text)
    except Exception:
        match = re.search(r'\{[\s\S]*\}\s*$', text)
        if not match:
            return []
        try:
            data = json.loads(match.group(0))
        except Exception:
            return []
    channels = []
    if isinstance(data, dict):
        channels = (data.get('payload', {}) or {}).get('channels') or data.get('channels') or []
    out = []
    for channel in channels:
        out.append(
            {
                'platform': 'discord',
                'id': str(channel.get('id', '')),
                'name': channel.get('name', ''),
                'type': channel.get('type'),
                'parent_id': channel.get('parent_id'),
            }
        )
    return [item for item in out if item['id']]


def list_feishu_groups_from_config(cfg: dict) -> List[dict]:
    feishu = cfg.get('channels', {}).get('feishu', {})
    seen: Dict[str, dict] = {}

    def add(group_id: str, name: str = ''):
        normalized = normalize_peer_id('feishu', group_id)
        if not normalized or not normalized.startswith('oc_'):
            return
        if normalized not in seen:
            seen[normalized] = {
                'platform': 'feishu',
                'id': normalized,
                'name': name or '',
            }
        elif name and not seen[normalized].get('name'):
            seen[normalized]['name'] = name

    for group_id in (feishu.get('groups') or {}).keys():
        add(str(group_id))

    for acc_cfg in (feishu.get('accounts') or {}).values():
        if not isinstance(acc_cfg, dict):
            continue
        for group_id in (acc_cfg.get('groups') or {}).keys():
            add(str(group_id))
        for group_id in (acc_cfg.get('groupAllowFrom') or []):
            add(str(group_id))

    for binding in cfg.get('bindings', []):
        match = binding.get('match', {})
        peer = match.get('peer', {}) if isinstance(match.get('peer', {}), dict) else {}
        if match.get('channel') == 'feishu' and peer.get('kind') == 'group' and peer.get('id'):
            add(str(peer.get('id')))

    return [seen[key] for key in sorted(seen.keys())]


def detect_roles(cfg: dict, requested_roles: List[str]) -> List[dict]:
    agents = cfg.get('agents', {}).get('list', [])
    discord_accounts = cfg.get('channels', {}).get('discord', {}).get('accounts', {})
    feishu_accounts = cfg.get('channels', {}).get('feishu', {}).get('accounts', {})

    role_ids = requested_roles or [
        agent.get('id', '')
        for agent in agents
        if agent.get('id') and not is_managed_agent_id(agent.get('id', ''))
    ]

    out = []
    for role in dedupe(role_ids):
        agent = next((item for item in agents if item.get('id') == role), None)
        if not agent:
            continue
        group_chat = agent.get('groupChat', {}) if isinstance(agent.get('groupChat'), dict) else {}
        out.append(
            {
                'role': role,
                'base_agent_id': role,
                'discord_account': role if role in discord_accounts else '',
                'feishu_account': role if role in feishu_accounts else '',
                'default_model': agent.get('model', {}).get('primary', ''),
                'workspace': agent.get('workspace', ''),
                'discord_mentions': serialize_patterns(group_chat.get('mentionPatterns') or []),
            }
        )
    return out


def existing_platform_state(cfg: dict, platform: str, roles: List[dict], guild_id: str = '') -> Dict[str, dict]:
    bindings = cfg.get('bindings', [])
    state: Dict[str, dict] = {}

    if platform == 'discord':
        channel_root = cfg.get('channels', {}).get('discord', {})
        for role in roles:
            role_id = role['role']
            account_id = role.get('discord_account', '')
            if not account_id:
                continue
            guild_cfg = (
                (((channel_root.get('accounts', {}) or {}).get(account_id, {}) or {}).get('guilds', {}) or {})
            ).get(guild_id, {})
            channels_cfg = guild_cfg.get('channels', {}) or {}
            for binding in bindings:
                match = binding.get('match', {})
                peer = match.get('peer', {}) if isinstance(match.get('peer', {}), dict) else {}
                if match.get('channel') != 'discord' or match.get('accountId') != account_id:
                    continue
                if peer.get('kind') != 'channel' or not peer.get('id'):
                    continue
                channel_id = str(peer['id'])
                state.setdefault(channel_id, {'roles': {}, 'bound_roles': []})
                state[channel_id]['bound_roles'].append(role_id)
                state[channel_id]['roles'][role_id] = {
                    'agentId': binding.get('agentId', ''),
                    'requireMention': channels_cfg.get(channel_id, {}).get('requireMention', True),
                    'allow': channels_cfg.get(channel_id, {}).get('allow', True),
                }
    elif platform == 'feishu':
        channel_root = cfg.get('channels', {}).get('feishu', {})
        for role in roles:
            role_id = role['role']
            account_id = role.get('feishu_account', '')
            if not account_id:
                continue
            acc_cfg = ((channel_root.get('accounts', {}) or {}).get(account_id, {}) or {})
            groups_cfg = acc_cfg.get('groups', {}) or {}
            for binding in bindings:
                match = binding.get('match', {})
                peer = match.get('peer', {}) if isinstance(match.get('peer', {}), dict) else {}
                if match.get('channel') != 'feishu' or match.get('accountId') != account_id:
                    continue
                if peer.get('kind') != 'group' or not peer.get('id'):
                    continue
                group_id = str(peer['id'])
                state.setdefault(group_id, {'roles': {}, 'bound_roles': []})
                state[group_id]['bound_roles'].append(role_id)
                state[group_id]['roles'][role_id] = {
                    'agentId': binding.get('agentId', ''),
                    'requireMention': groups_cfg.get(group_id, {}).get('requireMention', True),
                    'allow': True,
                }

    for target in state.values():
        target['bound_roles'] = dedupe(target.get('bound_roles', []))
    return state


def suggest_setup(platform: str, item_type: int, currently_enabled: bool) -> Tuple[str, str, str, str]:
    if currently_enabled:
        return 'yes', '', 'true', 'Existing binding detected; review before applying changes.'
    if platform == 'discord' and item_type in (2, 4):
        return 'no', '', 'true', 'Category or voice channel; left disabled by default.'
    return 'no', '', 'true', 'Disabled by default; choose roles and review mention policy manually.'


def generate_draft(cfg: dict, guild_id: str, roles_csv: str, platforms_csv: str):
    roles = detect_roles(cfg, parse_csv(roles_csv))
    if not roles:
        raise RuntimeError('No matching base roles found in agents.list')

    platforms = parse_csv(platforms_csv) or ['discord', 'feishu']
    platforms = [platform for platform in platforms if platform in SUPPORTED_PLATFORMS]
    if not platforms:
        raise RuntimeError('No supported platforms requested')

    discord_channels = list_discord_channels(guild_id) if 'discord' in platforms else []
    feishu_groups = list_feishu_groups_from_config(cfg) if 'feishu' in platforms else []

    discord_map = {channel['id']: channel for channel in discord_channels}
    feishu_map = {group['id']: group for group in feishu_groups}
    discord_state = existing_platform_state(cfg, 'discord', roles, guild_id)
    feishu_state = existing_platform_state(cfg, 'feishu', roles)

    known_items: List[dict] = []
    for channel_id in sorted(set(discord_map.keys()) | set(discord_state.keys())):
        item = dict(discord_map.get(channel_id, {'platform': 'discord', 'id': channel_id, 'name': 'unknown', 'type': 0}))
        known_items.append(item)
    for group_id in sorted(set(feishu_map.keys()) | set(feishu_state.keys())):
        item = dict(feishu_map.get(group_id, {'platform': 'feishu', 'id': group_id, 'name': ''}))
        known_items.append(item)

    lines = [
        '# Team Setup Draft',
        '',
        f'- generated_at: {now_str()}',
        '- mode: draft',
        f'- guild_id: {guild_id}',
        f'- platforms: {",".join(platforms)}',
        '',
        '## Global Policies',
        '- default_require_mention: true',
        '- allow_bot_to_bot: true',
        '- create_per_group_agents: true',
        '- isolate_group_context: true',
        '- naming_pattern_discord: {role}_ch_{peer_id}',
        '- naming_pattern_feishu: {role}_fg_{peer_id}',
        '',
        '## Roles',
        '| role | base_agent_id | discord_account | feishu_account | default_model | workspace | discord_mentions |',
        '|------|---------------|-----------------|----------------|---------------|-----------|------------------|',
    ]

    for role in roles:
        lines.append(
            "| {} | {} | {} | {} | {} | {} | {} |".format(
                escape_md_cell(role['role']),
                escape_md_cell(role['base_agent_id']),
                escape_md_cell(role['discord_account']),
                escape_md_cell(role['feishu_account']),
                escape_md_cell(role['default_model']),
                escape_md_cell(role.get('workspace', '')),
                escape_md_cell(role.get('discord_mentions', '')),
            )
        )

    lines.extend(
        [
            '',
            '## Targets',
            '| platform | peer_id | target_name | enable | roles | require_mention | custom_models | notes |',
            '|----------|---------|-------------|--------|-------|-----------------|--------------|------|',
        ]
    )

    for item in known_items:
        platform = item['platform']
        peer_id = item['id']
        state = discord_state if platform == 'discord' else feishu_state
        target_state = state.get(peer_id, {})
        roles_here = target_state.get('bound_roles', [])
        if roles_here:
            enable = 'yes'
            role_text = ','.join(roles_here)
            values = []
            per_role = []
            for role_name in roles_here:
                require_mention = str(target_state.get('roles', {}).get(role_name, {}).get('requireMention', True)).lower()
                values.append(require_mention)
                per_role.append(f"{role_name}={require_mention}")
            req = values[0] if len(set(values)) == 1 else ';'.join(per_role)
            notes = 'Existing binding detected; generated from current config.'
        else:
            enable, role_text, req, notes = suggest_setup(platform, item.get('type', 0), False)
        lines.append(
            "| {} | {} | {} | {} | {} | {} | {} | {} |".format(
                escape_md_cell(platform),
                escape_md_cell(peer_id),
                escape_md_cell(item.get('name', '')),
                escape_md_cell(enable),
                escape_md_cell(role_text),
                escape_md_cell(req),
                '',
                escape_md_cell(notes),
            )
        )

    lines.extend(
        [
            '',
            '## How to Apply',
            '1. Review the Roles table first. Fill in any missing account ids, workspace paths, or Discord mention patterns.',
            '2. In ## Targets, set enable=yes/no, choose roles, and set require_mention.',
            '3. require_mention supports either a single value like true/false, or per-role values like role-a=false;role-b=true.',
            '4. custom_models format: role=model;role=model.',
            '5. platform currently supports discord and feishu.',
            '6. For Feishu, peer_id supports either oc_xxx or chat:oc_xxx; it will be normalized on apply.',
            '7. Keep the table headers unchanged.',
            f'8. Run: `python3 {RUNTIME.skill_dir / "scripts/discord_team_bootstrap.py"} apply --validate`',
        ]
    )

    RUNTIME.draft_path.parent.mkdir(parents=True, exist_ok=True)
    RUNTIME.draft_path.write_text('\n'.join(lines) + '\n')
    return roles, known_items, platforms


def parse_md_table(lines: List[str], section_name: str) -> List[Dict[str, str]]:
    start = None
    for index, line in enumerate(lines):
        if line.strip() == section_name:
            start = index + 1
            break
    if start is None:
        return []

    table = []
    headers = None
    for line in lines[start:]:
        stripped = line.strip()
        if not stripped:
            if headers:
                break
            continue
        if not stripped.startswith('|'):
            if headers:
                break
            continue
        parts = split_md_row(stripped)
        if headers is None:
            headers = parts
            continue
        if all(re.fullmatch(r'-+', cell.replace(':', '').strip()) for cell in parts):
            continue
        row = {headers[index]: parts[index] if index < len(parts) else '' for index in range(len(headers))}
        table.append(row)
    return table


def parse_bool(value: str, default: bool = True) -> bool:
    normalized = (value or '').strip().lower()
    if normalized in {'true', 'yes', 'y', '1'}:
        return True
    if normalized in {'false', 'no', 'n', '0'}:
        return False
    return default


def parse_custom_models(text: str) -> Dict[str, str]:
    out = {}
    for part in [x.strip() for x in (text or '').split(';') if x.strip()]:
        if '=' in part:
            key, value = part.split('=', 1)
            out[key.strip()] = value.strip()
    return out


def parse_require_mention_map(text: str, roles_here: List[str], default: bool = True) -> Dict[str, bool]:
    raw = (text or '').strip()
    if not raw:
        return {role: default for role in roles_here}
    normalized = raw.lower()
    if normalized in {'true', 'false', 'yes', 'no', 'y', 'n', '1', '0'}:
        value = parse_bool(normalized, default)
        return {role: value for role in roles_here}

    out = {role: default for role in roles_here}
    for part in [x.strip() for x in raw.split(';') if x.strip()]:
        if '=' not in part:
            continue
        key, value = part.split('=', 1)
        role = key.strip()
        if role in out:
            out[role] = parse_bool(value.strip(), default)
    return out


def mention_patterns_for_role(role: dict) -> List[str]:
    configured = parse_patterns(role.get('discord_mentions', ''))
    if configured:
        return dedupe(configured)
    role_id = role['role'].lower()
    return [rf'(?<!\\d)@?{re.escape(role_id)}(?!\\d)']


def ensure_feishu_group_allowlisted(feishu_node: dict, group_id: str):
    if feishu_node.get('groupPolicy') == 'allowlist':
        current = feishu_node.get('groupAllowFrom')
        if not isinstance(current, list):
            feishu_node['groupAllowFrom'] = []
            current = feishu_node['groupAllowFrom']
        if group_id not in current:
            current.append(group_id)


def parse_draft() -> Tuple[List[dict], List[dict], str]:
    if not RUNTIME.draft_path.exists():
        raise RuntimeError(f'Draft not found: {RUNTIME.draft_path}')

    raw_text = RUNTIME.draft_path.read_text()
    lines = raw_text.splitlines()
    role_rows = parse_md_table(lines, '## Roles')
    target_rows = parse_md_table(lines, '## Targets')
    guild_match = re.search(r'^\s*-\s*guild_id:\s*(.+)$', raw_text, re.M)
    if not guild_match:
        raise RuntimeError('guild_id missing from draft')
    guild_id = guild_match.group(1).strip()

    roles = []
    for row in role_rows:
        roles.append(
            {
                'role': row.get('role', '').strip(),
                'base_agent_id': row.get('base_agent_id', '').strip(),
                'discord_account': row.get('discord_account', '').strip(),
                'feishu_account': row.get('feishu_account', '').strip(),
                'default_model': row.get('default_model', '').strip(),
                'workspace': row.get('workspace', '').strip(),
                'discord_mentions': row.get('discord_mentions', '').strip(),
            }
        )
    roles = [role for role in roles if role['role'] and role['base_agent_id']]
    if not roles:
        raise RuntimeError('No valid roles parsed from draft')

    managed_targets = []
    for row in target_rows:
        platform = row.get('platform', '').strip().lower()
        if platform not in SUPPORTED_PLATFORMS:
            continue
        if not parse_bool(row.get('enable', 'no'), False):
            continue
        peer_id = normalize_peer_id(platform, row.get('peer_id', '').strip())
        if not peer_id:
            continue
        managed_targets.append(
            {
                'platform': platform,
                'peer_id': peer_id,
                'target_name': row.get('target_name', '').strip(),
                'roles': [x.strip() for x in row.get('roles', '').split(',') if x.strip()],
                'require_mention': row.get('require_mention', '').strip(),
                'custom_models': row.get('custom_models', '').strip(),
            }
        )

    return roles, managed_targets, guild_id


def validate_draft(roles: List[dict], managed_targets: List[dict]) -> Dict[str, dict]:
    role_map = {}
    errors = []

    for role in roles:
        role_name = role['role']
        if role_name in role_map:
            errors.append(f'duplicate role "{role_name}" in Roles table')
            continue
        role_map[role_name] = role

    for target in managed_targets:
        label = f"[{target['platform']}] {target['peer_id']}"
        if not target['roles']:
            errors.append(f'{label} has enable=yes but no roles selected')
            continue
        for role_name in target['roles']:
            role = role_map.get(role_name)
            if not role:
                errors.append(f'{label} references unknown role "{role_name}"')
                continue
            if target['platform'] == 'discord' and not role.get('discord_account'):
                errors.append(f'{label} uses role "{role_name}" without discord_account in Roles table')
            if target['platform'] == 'feishu' and not role.get('feishu_account'):
                errors.append(f'{label} uses role "{role_name}" without feishu_account in Roles table')

    if errors:
        raise RuntimeError('Invalid draft:\n- ' + '\n- '.join(errors))
    return role_map


def find_target_in_draft(platform: str, peer_id: str) -> Tuple[List[dict], Optional[dict], str]:
    roles, managed_targets, guild_id = parse_draft()
    normalized_peer_id = normalize_peer_id(platform, peer_id)
    for target in managed_targets:
        if target['platform'] == platform and normalize_peer_id(platform, target['peer_id']) == normalized_peer_id:
            return roles, target, guild_id
    return roles, None, guild_id


def config_validation_summary() -> Tuple[str, str]:
    if not RUNTIME.validate_script or not RUNTIME.validate_script.exists():
        return 'unknown', 'validate script not found'
    result = run(['python3', str(RUNTIME.validate_script)], check=False)
    if result.returncode == 0:
        return 'ok', (result.stdout or '').strip() or 'config-check OK'
    details = '\n'.join(x for x in [result.stdout.strip(), result.stderr.strip()] if x).strip()
    return 'error', details or f'config-check failed ({result.returncode})'


def configured_model_for_agent(cfg: dict, agent_id: str) -> str:
    for agent in cfg.get('agents', {}).get('list', []):
        if agent.get('id') == agent_id:
            return ((agent.get('model') or {}).get('primary') or '').strip()
    return ''


def agent_state_root() -> Path:
    return RUNTIME.config_path.parent / 'agents'


def latest_runtime_model_for_agent(agent_id: str) -> Tuple[str, str, str]:
    sessions_dir = agent_state_root() / agent_id / 'sessions'
    if not sessions_dir.exists():
        return 'unknown', '', 'no session directory found'

    session_files = sorted(sessions_dir.glob('*.jsonl'), key=lambda p: p.stat().st_mtime, reverse=True)
    if not session_files:
        return 'unknown', '', 'no session files found'

    latest = session_files[0]
    model_id = ''
    provider = ''
    timestamp = ''
    try:
        with latest.open('r', encoding='utf-8', errors='ignore') as f:
            for raw in f:
                line = raw.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except Exception:
                    continue
                if event.get('type') == 'custom' and event.get('customType') == 'model-snapshot':
                    data = event.get('data') or {}
                    if data.get('modelId'):
                        model_id = str(data.get('modelId'))
                        provider = str(data.get('provider') or '')
                        timestamp = str(data.get('timestamp') or event.get('timestamp') or '')
                elif event.get('type') == 'model_change' and event.get('modelId') and not model_id:
                    model_id = str(event.get('modelId'))
                    provider = str(event.get('provider') or '')
                    timestamp = str(event.get('timestamp') or '')
        if model_id:
            full = f'{provider}/{model_id}' if provider else model_id
            return full, timestamp, f'latest session: {latest.name}'
        return 'unknown', '', f'no model snapshot found in {latest.name}'
    except Exception as exc:
        return 'unknown', '', f'failed to read {latest.name}: {exc}'


def gateway_health_summary() -> Tuple[str, str]:
    result = run([RUNTIME.openclaw_bin, 'gateway', 'health'], check=False)
    output = '\n'.join(x for x in [result.stdout.strip(), result.stderr.strip()] if x).strip()
    if result.returncode == 0:
        return 'ok', output or 'gateway health OK'
    return 'error', output or f'gateway health failed ({result.returncode})'


def build_target_check_report(cfg: dict, platform: str, peer_id: str) -> str:
    roles, draft_target, guild_id = find_target_in_draft(platform, peer_id)
    normalized_peer_id = normalize_peer_id(platform, peer_id)
    state = existing_platform_state(cfg, platform, roles, guild_id if platform == 'discord' else '')
    current = state.get(normalized_peer_id, {'bound_roles': [], 'roles': {}})
    actual_roles = dedupe(current.get('bound_roles', []))
    agent_ids = {agent.get('id', '') for agent in cfg.get('agents', {}).get('list', [])}

    target_name = ''
    if draft_target:
        target_name = draft_target.get('target_name', '')
    if not target_name:
        if platform == 'discord':
            for channel in list_discord_channels(guild_id):
                if channel.get('id') == normalized_peer_id:
                    target_name = channel.get('name', '')
                    break
        else:
            for group in list_feishu_groups_from_config(cfg):
                if group.get('id') == normalized_peer_id:
                    target_name = group.get('name', '')
                    break

    report = [
        '# Target Check Report',
        f'- platform: {platform}',
        f'- peer_id: {normalized_peer_id}',
        f'- target_name: {target_name or "-"}',
        f'- guild_id: {guild_id or "-"}',
        '',
        '## Draft state',
    ]

    if draft_target:
        report.extend([
            f'- enabled: yes',
            f'- roles: {",".join(draft_target.get("roles", [])) or "-"}',
            f'- require_mention: {draft_target.get("require_mention", "") or "-"}',
            f'- custom_models: {draft_target.get("custom_models", "") or "-"}',
        ])
    else:
        report.extend([
            '- enabled: no',
            '- roles: -',
            '- require_mention: -',
            '- custom_models: -',
            '- note: target not enabled in current draft',
        ])

    report.extend(['', '## Config state'])

    if actual_roles:
        report.append(f'- bound_roles: {",".join(actual_roles)}')
    else:
        report.append('- bound_roles: -')
        report.append('- note: no binding found for this target in openclaw.json')

    role_names = draft_target.get('roles', []) if draft_target else actual_roles
    if not role_names:
        role_names = actual_roles

    report.extend(['', '## Model state'])
    draft_custom_models = parse_custom_models(draft_target.get('custom_models', '')) if draft_target else {}
    role_map = {role['role']: role for role in roles}

    for role_name in role_names:
        expected_agent_id = make_agent_id(role_name, platform, normalized_peer_id)
        actual = current.get('roles', {}).get(role_name)
        report.append(
            f"- role={role_name}: expected_agent={expected_agent_id} agent_present={'yes' if expected_agent_id in agent_ids else 'no'}"
        )
        if actual:
            report.append(
                f"  config_binding_agent={actual.get('agentId', '-') or '-'} requireMention={actual.get('requireMention', True)}"
            )
        else:
            report.append('  config_binding_agent=- requireMention=-')

        role_cfg = role_map.get(role_name, {})
        expected_model = (draft_custom_models.get(role_name) or role_cfg.get('default_model') or '').strip() or '-'
        configured_model = configured_model_for_agent(cfg, expected_agent_id) or '-'
        runtime_model, runtime_timestamp, runtime_source = latest_runtime_model_for_agent(expected_agent_id)
        report.append(f'  expected_model={expected_model}')
        report.append(f'  configured_model={configured_model}')
        report.append(f'  actual_runtime_model={runtime_model or "unknown"}')
        if runtime_timestamp:
            report.append(f'  actual_runtime_model_timestamp={runtime_timestamp}')
        if runtime_source:
            report.append(f'  actual_runtime_model_source={runtime_source}')

    report.extend(['', '## Runtime state'])
    validate_status, validate_details = config_validation_summary()
    gateway_status, gateway_details = gateway_health_summary()
    report.append(f'- config_validation: {validate_status}')
    report.append(f'- gateway_health: {gateway_status}')
    if validate_details:
        report.append('- config_validation_details:')
        report.extend([f'  {line}' for line in validate_details.splitlines()])
    if gateway_details:
        report.append('- gateway_health_details:')
        report.extend([f'  {line}' for line in gateway_details.splitlines()])

    report.extend(['', '## Mention behavior note'])
    report.append('- Desired collaboration rule: if a message explicitly mentions one bot, that bot should be the only default responder for that message; non-mentioned bots should stay silent unless they were also explicitly mentioned.')
    report.append('- Current skill can encode requireMention and mention patterns, but strict runtime enforcement of "mentioned bot only" still depends on OpenClaw runtime behavior.')

    report.extend(['', '## Conclusion'])
    issues = []
    if not draft_target:
        issues.append('target not enabled in draft')
    else:
        expected_roles = draft_target.get('roles', [])
        missing_roles = [role for role in expected_roles if role not in actual_roles]
        extra_roles = [role for role in actual_roles if role not in expected_roles]
        if missing_roles:
            issues.append(f'missing_roles={",".join(missing_roles)}')
        if extra_roles:
            issues.append(f'extra_roles={",".join(extra_roles)}')
        require_map = parse_require_mention_map(draft_target.get('require_mention', ''), expected_roles, True)
        draft_custom_models = parse_custom_models(draft_target.get('custom_models', ''))
        role_cfg_map = {role['role']: role for role in roles}
        for role_name in expected_roles:
            actual = current.get('roles', {}).get(role_name)
            expected_agent_id = make_agent_id(role_name, platform, normalized_peer_id)
            if expected_agent_id not in agent_ids:
                issues.append(f'{role_name}:agent_missing')
            if not actual:
                issues.append(f'{role_name}:binding_missing')
                continue
            if actual.get('agentId') != expected_agent_id:
                issues.append(f'{role_name}:binding_agent_mismatch')
            if actual.get('requireMention', True) != require_map.get(role_name, True):
                issues.append(f'{role_name}:requireMention_mismatch')
            expected_model = (draft_custom_models.get(role_name) or (role_cfg_map.get(role_name, {}) or {}).get('default_model') or '').strip()
            configured_model = configured_model_for_agent(cfg, expected_agent_id)
            if expected_model and configured_model and expected_model != configured_model:
                issues.append(f'{role_name}:configured_model_mismatch')
            runtime_model, _, _ = latest_runtime_model_for_agent(expected_agent_id)
            if expected_model and runtime_model not in {'', 'unknown'} and expected_model != runtime_model:
                issues.append(f'{role_name}:runtime_model_mismatch')
    if validate_status != 'ok':
        issues.append('config_validation_failed')
    if gateway_status != 'ok':
        issues.append('gateway_health_failed')

    if issues:
        report.append(f'- status: mismatch')
        report.append(f'- issues: {"; ".join(issues)}')
    else:
        report.append('- status: ok')
        report.append('- issues: -')
        report.append('- summary: draft, config, and basic runtime health are consistent for this target.')

    return '\n'.join(report).rstrip() + '\n'


def resolve_role_workspace(cfg: dict, role: dict, platform: str) -> str:
    if role.get('workspace'):
        return role['workspace']
    return detect_workspace(cfg, role['role'], role['base_agent_id'], platform, RUNTIME.fallback_workspace)


def expected_model_for_target_role(role_map: Dict[str, dict], target: dict, role_name: str) -> str:
    custom_models = parse_custom_models(target.get('custom_models', ''))
    role = role_map.get(role_name, {}) or {}
    return (custom_models.get(role_name) or role.get('default_model') or '').strip()


def verify_configured_models(cfg: dict, roles: List[dict], managed_targets: List[dict]) -> List[str]:
    role_map = {role['role']: role for role in roles}
    issues = []
    for target in managed_targets:
        for role_name in target.get('roles', []):
            expected_model = expected_model_for_target_role(role_map, target, role_name)
            agent_id = make_agent_id(role_name, target['platform'], target['peer_id'])
            configured_model = configured_model_for_agent(cfg, agent_id)
            if expected_model != configured_model:
                issues.append(
                    f"[{target['platform']}] {target['peer_id']} role={role_name} expected_model={expected_model or '-'} configured_model={configured_model or '-'}"
                )
    return issues


def apply_from_draft(cfg: dict):
    roles, managed_targets, guild_id = parse_draft()
    role_map = validate_draft(roles, managed_targets)

    keep_agents = []
    for agent in cfg.get('agents', {}).get('list', []):
        agent_id = agent.get('id', '')
        if any(agent_id.startswith(f"{role['role']}_ch_") or agent_id.startswith(f"{role['role']}_fg_") for role in roles):
            continue
        keep_agents.append(agent)

    for target in managed_targets:
        platform = target['platform']
        custom_models = parse_custom_models(target['custom_models'])
        for role_name in target['roles']:
            role = role_map[role_name]
            agent = {
                'id': make_agent_id(role_name, platform, target['peer_id']),
                'name': f"{role_name}@{platform}#{target['peer_id']}",
                'workspace': resolve_role_workspace(cfg, role, platform),
                'model': {'primary': custom_models.get(role_name, role['default_model'])},
            }
            if platform == 'discord':
                patterns = mention_patterns_for_role(role)
                if patterns:
                    agent['groupChat'] = {'mentionPatterns': patterns}
            keep_agents.append(agent)

    base_role_map = {role['base_agent_id']: role for role in roles}
    for agent in keep_agents:
        agent_id = agent.get('id')
        role = base_role_map.get(agent_id)
        if not role:
            continue
        patterns = mention_patterns_for_role(role)
        if not patterns:
            continue
        group_chat = agent.setdefault('groupChat', {})
        current = list(group_chat.get('mentionPatterns') or [])
        group_chat['mentionPatterns'] = dedupe(current + patterns)

    cfg.setdefault('agents', {})['list'] = keep_agents

    new_bindings = []
    for binding in cfg.get('bindings', []):
        match = binding.get('match', {})
        peer = match.get('peer', {}) if isinstance(match.get('peer', {}), dict) else {}
        agent_id = binding.get('agentId', '')
        if match.get('channel') == 'discord' and peer.get('kind') == 'channel' and any(
            agent_id.startswith(f"{role['role']}_ch_") for role in roles
        ):
            continue
        if match.get('channel') == 'feishu' and peer.get('kind') == 'group' and any(
            agent_id.startswith(f"{role['role']}_fg_") for role in roles
        ):
            continue
        new_bindings.append(binding)

    discord = cfg.setdefault('channels', {}).setdefault('discord', {})
    feishu = cfg.setdefault('channels', {}).setdefault('feishu', {})
    discord_accounts = discord.setdefault('accounts', {})
    feishu_accounts = feishu.setdefault('accounts', {})

    for role in roles:
        if role.get('discord_account'):
            account = discord_accounts.setdefault(role['discord_account'], {})
            account['allowBots'] = True
            guilds = account.setdefault('guilds', {})
            guild = guilds.setdefault(guild_id, {})
            guild['requireMention'] = True
            if not isinstance(guild.get('channels'), dict):
                guild['channels'] = {}
        if role.get('feishu_account'):
            account = feishu_accounts.setdefault(role['feishu_account'], {})
            if not isinstance(account.get('groups'), dict):
                account['groups'] = {}

    for target in managed_targets:
        platform = target['platform']
        require_mention = parse_require_mention_map(target['require_mention'], target['roles'], True)
        for role_name in target['roles']:
            role = role_map[role_name]
            if platform == 'discord':
                account_id = role['discord_account']
                new_bindings.append(
                    {
                        'agentId': make_agent_id(role_name, 'discord', target['peer_id']),
                        'match': {
                            'channel': 'discord',
                            'accountId': account_id,
                            'peer': {'kind': 'channel', 'id': target['peer_id']},
                        },
                    }
                )
                discord_accounts[account_id]['guilds'][guild_id]['channels'][target['peer_id']] = {
                    'allow': True,
                    'requireMention': require_mention.get(role_name, True),
                }
            else:
                account_id = role['feishu_account']
                new_bindings.append(
                    {
                        'agentId': make_agent_id(role_name, 'feishu', target['peer_id']),
                        'match': {
                            'channel': 'feishu',
                            'accountId': account_id,
                            'peer': {'kind': 'group', 'id': target['peer_id']},
                        },
                    }
                )
                account = feishu_accounts[account_id]
                groups = account.setdefault('groups', {})
                current = groups.get(target['peer_id'], {}) if isinstance(groups.get(target['peer_id']), dict) else {}
                current['requireMention'] = require_mention.get(role_name, True)
                groups[target['peer_id']] = current
                ensure_feishu_group_allowlisted(account, target['peer_id'])
                ensure_feishu_group_allowlisted(feishu, target['peer_id'])

    guilds = discord.setdefault('guilds', {})
    guild = guilds.setdefault(guild_id, {})
    channels = guild.get('channels')
    if not isinstance(channels, dict):
        channels = {}
    channels['*'] = {'allow': True, 'requireMention': True}
    guild['channels'] = channels

    cfg['bindings'] = new_bindings
    return cfg, roles, managed_targets, guild_id


def build_inspect_report(cfg: dict) -> str:
    roles, managed_targets, guild_id = parse_draft()
    role_map = validate_draft(roles, managed_targets)
    states = {
        'discord': existing_platform_state(cfg, 'discord', roles, guild_id),
        'feishu': existing_platform_state(cfg, 'feishu', roles),
    }
    agent_ids = {agent.get('id', '') for agent in cfg.get('agents', {}).get('list', [])}

    report = ['# Inspect Report', f'- guild_id: {guild_id or "-"}', f'- roles: {", ".join(role_map.keys())}', '']

    for target in managed_targets:
        expected_roles = target['roles']
        current = states[target['platform']].get(target['peer_id'], {'bound_roles': [], 'roles': {}})
        actual_roles = dedupe(current.get('bound_roles', []))
        missing_roles = [role for role in expected_roles if role not in actual_roles]
        extra_roles = [role for role in actual_roles if role not in expected_roles]
        target_status = 'ok' if not missing_roles and not extra_roles else 'mismatch'
        report.append(
            f"- [{target['platform']}] {target['peer_id']} {target['target_name']}: status={target_status} expected_roles={','.join(expected_roles)} actual_roles={','.join(actual_roles) or '-'}"
        )
        if missing_roles:
            report.append(f"  missing_roles: {', '.join(missing_roles)}")
        if extra_roles:
            report.append(f"  extra_roles: {', '.join(extra_roles)}")

        require_mention = parse_require_mention_map(target['require_mention'], expected_roles, True)
        for role_name in expected_roles:
            expected_agent_id = make_agent_id(role_name, target['platform'], target['peer_id'])
            actual = current.get('roles', {}).get(role_name)
            issues = []
            if expected_agent_id not in agent_ids:
                issues.append('agent missing')
            if not actual:
                issues.append('binding missing')
            else:
                if actual.get('agentId') and actual['agentId'] != expected_agent_id:
                    issues.append(f"binding agentId={actual['agentId']}")
                actual_require = actual.get('requireMention', True)
                expected_require = require_mention.get(role_name, True)
                if actual_require != expected_require:
                    issues.append(f"requireMention={actual_require} expected={expected_require}")
            status = 'ok' if not issues else 'mismatch'
            report.append(
                f"  role={role_name} status={status} expected_agent={expected_agent_id} expected_require_mention={require_mention.get(role_name, True)} issues={'; '.join(issues) or '-'}"
            )
        report.append('')

    return '\n'.join(report).rstrip() + '\n'


def write_report(text: str):
    RUNTIME.report_path.parent.mkdir(parents=True, exist_ok=True)
    RUNTIME.report_path.write_text(text)


def cmd_scan(args):
    cfg = load_json(RUNTIME.config_path)
    guild_id = args.guild_id or find_default_guild(cfg)
    if 'discord' in parse_csv(args.platforms) and not guild_id:
        raise RuntimeError('No Discord guild found; pass --guild-id explicitly')
    roles, items, platforms = generate_draft(cfg, guild_id or '', args.roles, args.platforms)
    message = [
        'Draft generated',
        f'- guild_id: {guild_id or "-"}',
        f'- platforms: {", ".join(platforms)}',
        f'- roles: {", ".join(role["role"] for role in roles)}',
        f'- targets discovered: {len(items)}',
        f'- draft: {RUNTIME.draft_path}',
    ]
    text = '\n'.join(message) + '\n'
    write_report(text)
    print(text)


def cmd_explain(args):
    if not RUNTIME.draft_path.exists():
        raise RuntimeError(f'Draft not found: {RUNTIME.draft_path}')
    text = f"""Draft file: {RUNTIME.draft_path}

How to edit:
- Review ## Roles first and fill in any missing account ids or workspace paths.
- In ## Targets, set `enable` to yes/no.
- In `roles`, use comma-separated role ids from ## Roles.
- `platform` supports `discord` and `feishu`.
- `require_mention=true` means the bot only replies when mentioned.
- `custom_models` format: role=model;role=model
- Feishu `peer_id` may be `oc_xxx` or `chat:oc_xxx`.
- After editing, run: python3 {RUNTIME.skill_dir / "scripts/discord_team_bootstrap.py"} apply --validate
"""
    write_report(text)
    print(text)


def cmd_apply(args):
    cfg = load_json(RUNTIME.config_path)
    backup = RUNTIME.config_path.with_suffix(RUNTIME.config_path.suffix + '.bak.discord-team-bootstrap')
    backup.write_text(RUNTIME.config_path.read_text())
    try:
        new_cfg, roles, managed_targets, guild_id = apply_from_draft(cfg)
        report = [
            'Draft parsed',
            f'- guild_id: {guild_id or "-"}',
            f'- roles: {", ".join(role["role"] for role in roles)}',
            f'- managed_targets: {len(managed_targets)}',
            '- planned_changes:',
        ]
        for row in managed_targets:
            report.append(
                f"  - [{row.get('platform', '')}] {row.get('target_name', '')} ({row.get('peer_id', '')}): roles={','.join(row.get('roles', []))} require_mention={row.get('require_mention', '')} custom_models={row.get('custom_models', '') or '-'}"
            )

        if args.dry_run:
            text = '\n'.join(report + ['- dry_run: true', f'- backup: {backup}']) + '\n'
            write_report(text)
            print(text)
            return

        save_json(RUNTIME.config_path, new_cfg)
        reloaded_cfg = load_json(RUNTIME.config_path)
        model_issues = verify_configured_models(reloaded_cfg, roles, managed_targets)
        if model_issues:
            raise RuntimeError('configured model sync failed:\n- ' + '\n- '.join(model_issues))

        report[0] = 'Draft applied'
        report.append('- model_sync: ok')
        if args.validate:
            if not RUNTIME.validate_script or not RUNTIME.validate_script.exists():
                raise RuntimeError(
                    'Validation requested, but validate script was not found. '
                    'Set --validate-script or OPENCLAW_TEAM_BOOTSTRAP_VALIDATE_SCRIPT.'
                )
            validate = run(['python3', str(RUNTIME.validate_script)], check=False)
            if validate.returncode != 0:
                raise RuntimeError(f'config validate failed:\n{validate.stdout}\n{validate.stderr}')
            health = run([RUNTIME.openclaw_bin, 'gateway', 'health'], check=False)
            report.append('- validate: ok')
            report.append(f'- gateway_health_code: {health.returncode}')
            if health.stdout.strip():
                report.append('- gateway_health_output:')
                report.append(health.stdout.strip())
        report.append(f'- backup: {backup}')
        text = '\n'.join(report) + '\n'
        write_report(text)
        print(text)
    except Exception:
        RUNTIME.config_path.write_text(backup.read_text())
        raise


def cmd_inspect(args):
    cfg = load_json(RUNTIME.config_path)
    text = build_inspect_report(cfg)
    write_report(text)
    print(text)


def cmd_check_target(args):
    platform = (args.platform or '').strip().lower()
    if platform not in SUPPORTED_PLATFORMS:
        raise RuntimeError('check-target requires --platform discord|feishu')
    if not (args.peer_id or '').strip():
        raise RuntimeError('check-target requires --peer-id')
    cfg = load_json(RUNTIME.config_path)
    text = build_target_check_report(cfg, platform, args.peer_id.strip())
    write_report(text)
    print(text)


def main():
    parser = argparse.ArgumentParser(description='Discord / Feishu multi-agent bootstrap skill')
    parser.add_argument('--skill-dir', default='', help='Skill directory used to resolve default draft/report paths.')
    parser.add_argument('--draft-path', default='', help='Path to the editable draft markdown file.')
    parser.add_argument('--report-path', default='', help='Path to the generated report file.')
    parser.add_argument('--config-path', default='', help='Path to openclaw.json.')
    parser.add_argument('--validate-script', default='', help='Path to validate-openclaw-config.py.')
    parser.add_argument('--openclaw-bin', default='', help='OpenClaw CLI executable name or path.')
    parser.add_argument('--fallback-workspace', default='', help='Fallback workspace for generated agents.')
    sub = parser.add_subparsers(dest='cmd', required=True)

    scan = sub.add_parser('scan')
    scan.add_argument('--guild-id', default='')
    scan.add_argument('--roles', default='', help='Comma-separated base role ids. Defaults to auto-detecting non-generated agents.')
    scan.add_argument('--platforms', default='discord,feishu')
    scan.set_defaults(func=cmd_scan)

    explain = sub.add_parser('explain')
    explain.set_defaults(func=cmd_explain)

    apply = sub.add_parser('apply')
    apply.add_argument('--validate', action='store_true')
    apply.add_argument('--dry-run', action='store_true')
    apply.set_defaults(func=cmd_apply)

    inspect = sub.add_parser('inspect')
    inspect.set_defaults(func=cmd_inspect)

    check_target = sub.add_parser('check-target')
    check_target.add_argument('--platform', required=True, help='Target platform: discord or feishu')
    check_target.add_argument('--peer-id', required=True, help='Target channel id or Feishu group id')
    check_target.set_defaults(func=cmd_check_target)

    args = parser.parse_args()
    global RUNTIME
    RUNTIME = build_runtime(args)
    RUNTIME.skill_dir.mkdir(parents=True, exist_ok=True)
    args.func(args)


if __name__ == '__main__':
    main()
