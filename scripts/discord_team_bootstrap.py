#!/usr/bin/env python3
import argparse
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

WORKSPACE = Path('/root/clawd/skills/discord-team-bootstrap')
DRAFT_PATH = WORKSPACE / 'discord-team-setup.draft.md'
REPORT_PATH = WORKSPACE / 'discord-team-setup.report.md'
CONFIG_PATH = Path('/root/.openclaw/openclaw.json')
VALIDATE_SCRIPT = '/root/.openclaw/scripts/validate-openclaw-config.py'
DEFAULT_ROLES = ['trouble', 'friday']
SUPPORTED_PLATFORMS = {'discord', 'feishu'}


def run(cmd: List[str], check: bool = True) -> subprocess.CompletedProcess:
    p = subprocess.run(cmd, capture_output=True, text=True)
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
    return datetime.now().strftime('%Y-%m-%d %H:%M Asia/Shanghai')


def normalize_peer_id(platform: str, raw: str) -> str:
    v = (raw or '').strip()
    if platform == 'feishu' and v.startswith('chat:'):
        return v.split(':', 1)[1].strip()
    return v


def infer_platform_from_id(raw: str) -> str:
    v = (raw or '').strip()
    if v.isdigit():
        return 'discord'
    if v.startswith('oc_') or v.startswith('chat:oc_'):
        return 'feishu'
    return 'unknown'


def peer_kind_for(platform: str) -> str:
    return 'channel' if platform == 'discord' else 'group'


def agent_prefix_for(platform: str) -> str:
    return 'ch' if platform == 'discord' else 'fg'


def make_agent_id(role: str, platform: str, peer_id: str) -> str:
    return f"{role}_{agent_prefix_for(platform)}_{peer_id}"


def detect_workspace(cfg: dict, role: str, platform: str, fallback: str) -> str:
    prefix = f"{role}_{agent_prefix_for(platform)}_"
    for a in cfg.get('agents', {}).get('list', []):
        if a.get('id', '').startswith(prefix) and a.get('workspace'):
            return a['workspace']
    for a in cfg.get('agents', {}).get('list', []):
        if a.get('id') == role and a.get('workspace'):
            return a['workspace']
    return fallback


def find_default_guild(cfg: dict) -> Optional[str]:
    discord = cfg.get('channels', {}).get('discord', {})
    guilds = set()
    for acc_cfg in discord.get('accounts', {}).values():
        if not isinstance(acc_cfg, dict):
            continue
        for gid in (acc_cfg.get('guilds') or {}).keys():
            guilds.add(gid)
    if guilds:
        return sorted(guilds)[0]
    return None


def list_discord_channels(guild_id: str) -> List[dict]:
    cmd = ['openclaw', 'message', 'channel', 'list', '--channel', 'discord', '--guild-id', guild_id, '--json']
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        return []
    text = p.stdout.strip()
    try:
        data = json.loads(text)
    except Exception:
        m = re.search(r'\{[\s\S]*\}\s*$', text)
        if not m:
            return []
        try:
            data = json.loads(m.group(0))
        except Exception:
            return []
    channels = []
    if isinstance(data, dict):
        channels = (data.get('payload', {}) or {}).get('channels') or data.get('channels') or []
    out = []
    for c in channels:
        out.append(
            {
                'platform': 'discord',
                'id': str(c.get('id', '')),
                'name': c.get('name', ''),
                'type': c.get('type'),
                'parent_id': c.get('parent_id'),
            }
        )
    return [x for x in out if x['id']]


def list_feishu_groups_from_config(cfg: dict) -> List[dict]:
    feishu = cfg.get('channels', {}).get('feishu', {})
    seen: Dict[str, dict] = {}

    def add(group_id: str, name: str = ''):
        gid = normalize_peer_id('feishu', group_id)
        if not gid or not gid.startswith('oc_'):
            return
        if gid not in seen:
            seen[gid] = {
                'platform': 'feishu',
                'id': gid,
                'name': name or '',
            }
        elif name and not seen[gid].get('name'):
            seen[gid]['name'] = name

    for gid in (feishu.get('groups') or {}).keys():
        add(str(gid))

    for acc_cfg in (feishu.get('accounts') or {}).values():
        if not isinstance(acc_cfg, dict):
            continue
        for gid in (acc_cfg.get('groups') or {}).keys():
            add(str(gid))
        for gid in (acc_cfg.get('groupAllowFrom') or []):
            add(str(gid))

    for b in cfg.get('bindings', []):
        match = b.get('match', {})
        peer = match.get('peer', {}) if isinstance(match.get('peer', {}), dict) else {}
        if match.get('channel') == 'feishu' and peer.get('kind') == 'group' and peer.get('id'):
            add(str(peer.get('id')))

    return [seen[k] for k in sorted(seen.keys())]


def detect_roles(cfg: dict, requested_roles: List[str]) -> List[dict]:
    agents = cfg.get('agents', {}).get('list', [])
    discord_accounts = cfg.get('channels', {}).get('discord', {}).get('accounts', {})
    feishu_accounts = cfg.get('channels', {}).get('feishu', {}).get('accounts', {})
    out = []
    for role in requested_roles:
        agent = next((a for a in agents if a.get('id') == role), None)
        if not agent:
            continue
        out.append(
            {
                'role': role,
                'base_agent_id': role,
                'discord_account': role if role in discord_accounts else '',
                'feishu_account': role if role in feishu_accounts else '',
                'default_model': agent.get('model', {}).get('primary', ''),
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
            acc = role.get('discord_account', '')
            if not acc:
                continue
            guild_cfg = (
                (((channel_root.get('accounts', {}) or {}).get(acc, {}) or {}).get('guilds', {}) or {})
            ).get(guild_id, {})
            channels_cfg = guild_cfg.get('channels', {}) or {}
            for b in bindings:
                m = b.get('match', {})
                peer = m.get('peer', {}) if isinstance(m.get('peer', {}), dict) else {}
                if m.get('channel') != 'discord' or m.get('accountId') != acc:
                    continue
                if peer.get('kind') != 'channel' or not peer.get('id'):
                    continue
                cid = str(peer['id'])
                state.setdefault(cid, {'roles': {}, 'bound_roles': []})
                state[cid]['bound_roles'].append(role_id)
                state[cid]['roles'][role_id] = {
                    'agentId': b.get('agentId', ''),
                    'requireMention': channels_cfg.get(cid, {}).get('requireMention', True),
                    'allow': channels_cfg.get(cid, {}).get('allow', True),
                }
    elif platform == 'feishu':
        channel_root = cfg.get('channels', {}).get('feishu', {})
        for role in roles:
            role_id = role['role']
            acc = role.get('feishu_account', '')
            if not acc:
                continue
            acc_cfg = ((channel_root.get('accounts', {}) or {}).get(acc, {}) or {})
            groups_cfg = acc_cfg.get('groups', {}) or {}
            for b in bindings:
                m = b.get('match', {})
                peer = m.get('peer', {}) if isinstance(m.get('peer', {}), dict) else {}
                if m.get('channel') != 'feishu' or m.get('accountId') != acc:
                    continue
                if peer.get('kind') != 'group' or not peer.get('id'):
                    continue
                cid = str(peer['id'])
                state.setdefault(cid, {'roles': {}, 'bound_roles': []})
                state[cid]['bound_roles'].append(role_id)
                state[cid]['roles'][role_id] = {
                    'agentId': b.get('agentId', ''),
                    'requireMention': groups_cfg.get(cid, {}).get('requireMention', True),
                    'allow': True,
                }
    return state


def suggest_setup(platform: str, name: str, item_type: int, currently_enabled: bool):
    n = (name or '').lower()
    if platform == 'discord':
        if item_type in (4, 2):
            return 'no', 'trouble,friday', 'true', '容器/语音频道，默认不启用'
        if '小助理' in name:
            return 'yes', 'trouble', 'false', '个人助理频道，建议 Trouble 免 mention'
        if '记账' in name or '收纳' in name or 'flomo' in name:
            return 'yes', 'trouble', 'true', '工具型频道，建议单 Agent + mention 触发'
        if '运维' in name or 'backup' in n or 'hippocore' in n:
            return 'yes', 'friday', 'false', '工程/运维频道，建议 Friday 主负责'
        if 'research' in n:
            return 'yes', 'trouble,friday', 'trouble=false;friday=true', '研究频道，Trouble 前台，Friday 被叫起执行'
        if '灵魂对话' in name or '大总管' in name:
            return 'yes', 'trouble,friday', 'true', '双 Agent 协作主场，建议都需 mention'
        if currently_enabled:
            return 'yes', 'trouble,friday', 'true', '已存在绑定，建议先保持现状'
        return 'no', 'trouble,friday', 'true', '默认未启用；按需开启'

    if '记账' in name or '收纳' in name or 'flomo' in name:
        return 'yes', 'trouble', 'true', '工具型飞书群，建议单 Agent + mention 触发'
    if '情报' in name or 'report' in n:
        return 'yes', 'trouble', 'true', '收报/同步群，建议 mention 触发避免刷屏'
    if currently_enabled:
        return 'yes', 'trouble,friday', 'true', '已存在绑定，建议先保持现状'
    return 'no', 'trouble,friday', 'true', '默认未启用；按需开启'


def generate_draft(cfg: dict, guild_id: str, roles_csv: str, platforms_csv: str):
    roles = [x.strip() for x in roles_csv.split(',') if x.strip()] or DEFAULT_ROLES
    platforms = [x.strip() for x in platforms_csv.split(',') if x.strip()] or ['discord', 'feishu']
    platforms = [p for p in platforms if p in SUPPORTED_PLATFORMS]
    if not platforms:
        raise RuntimeError('No supported platforms requested')

    role_specs = detect_roles(cfg, roles)
    if not role_specs:
        raise RuntimeError('No matching base roles found in agents.list')

    discord_channels = list_discord_channels(guild_id) if 'discord' in platforms else []
    feishu_groups = list_feishu_groups_from_config(cfg) if 'feishu' in platforms else []

    discord_map = {c['id']: c for c in discord_channels}
    feishu_map = {c['id']: c for c in feishu_groups}
    discord_state = existing_platform_state(cfg, 'discord', role_specs, guild_id)
    feishu_state = existing_platform_state(cfg, 'feishu', role_specs)

    known_items: List[dict] = []
    for cid in sorted(set(discord_map.keys()) | set(discord_state.keys())):
        item = dict(discord_map.get(cid, {'platform': 'discord', 'id': cid, 'name': 'unknown', 'type': 0}))
        known_items.append(item)
    for gid in sorted(set(feishu_map.keys()) | set(feishu_state.keys())):
        item = dict(feishu_map.get(gid, {'platform': 'feishu', 'id': gid, 'name': ''}))
        known_items.append(item)

    lines = []
    lines.append('# Team Setup Draft')
    lines.append('')
    lines.append(f'- generated_at: {now_str()}')
    lines.append('- mode: draft')
    lines.append(f'- guild_id: {guild_id}')
    lines.append(f'- platforms: {",".join(platforms)}')
    lines.append('')
    lines.append('## Global Policies')
    lines.append('- default_require_mention: true')
    lines.append('- allow_bot_to_bot: true')
    lines.append('- create_per_group_agents: true')
    lines.append('- isolate_group_context: true')
    lines.append('- naming_pattern_discord: {role}_ch_{peer_id}')
    lines.append('- naming_pattern_feishu: {role}_fg_{peer_id}')
    lines.append('')
    lines.append('## Roles')
    lines.append('| role | base_agent_id | discord_account | feishu_account | default_model |')
    lines.append('|------|---------------|-----------------|----------------|---------------|')
    for r in role_specs:
        lines.append(
            f"| {r['role']} | {r['base_agent_id']} | {r['discord_account']} | {r['feishu_account']} | {r['default_model']} |"
        )
    lines.append('')
    lines.append('## Targets')
    lines.append('| platform | peer_id | target_name | enable | roles | require_mention | custom_models | notes |')
    lines.append('|----------|---------|-------------|--------|-------|-----------------|--------------|------|')

    for item in known_items:
        platform = item['platform']
        pid = item['id']
        state = discord_state if platform == 'discord' else feishu_state
        st = state.get(pid, {})
        roles_here = st.get('bound_roles', [])
        if roles_here:
            enable = 'yes'
            role_text = ','.join(roles_here)
            vals = []
            per_role = []
            for role in roles_here:
                rv = str(st.get('roles', {}).get(role, {}).get('requireMention', True)).lower()
                vals.append(rv)
                per_role.append(f"{role}={rv}")
            req = vals[0] if len(set(vals)) == 1 else ';'.join(per_role)
            notes = '已存在绑定，按当前线上配置生成'
        else:
            enable, role_text, req, notes = suggest_setup(platform, item.get('name', 'unknown'), item.get('type', 0), False)
        lines.append(
            f"| {platform} | {pid} | {item.get('name','')} | {enable} | {role_text} | {req} |  | {notes} |"
        )

    lines.append('')
    lines.append('## How to Apply')
    lines.append('1. Edit the Targets table: mark enable=yes/no, choose roles, set require_mention.')
    lines.append('2. `require_mention` supports either a single value like `true` / `false`, or per-role values like `trouble=false;friday=true`.')
    lines.append('3. `custom_models` format: `trouble=rightcode/gpt-5.4;friday=rightcode/gpt-5.4-codex`.')
    lines.append('4. `platform` currently supports `discord` and `feishu`.')
    lines.append('5. For Feishu, `peer_id` supports either `oc_xxx` or `chat:oc_xxx`; it will be normalized on apply.')
    lines.append('6. Keep table headers unchanged.')
    lines.append(
        f'7. Run: `python3 {WORKSPACE}/scripts/discord_team_bootstrap.py apply --validate`'
    )
    DRAFT_PATH.write_text('\n'.join(lines) + '\n')
    return role_specs, known_items, platforms


def parse_md_table(lines: List[str], section_name: str) -> List[Dict[str, str]]:
    start = None
    for i, line in enumerate(lines):
        if line.strip() == section_name:
            start = i + 1
            break
    if start is None:
        return []
    table = []
    headers = None
    for line in lines[start:]:
        s = line.strip()
        if not s:
            if headers:
                break
            continue
        if not s.startswith('|'):
            if headers:
                break
            continue
        parts = [p.strip() for p in s.strip('|').split('|')]
        if headers is None:
            headers = parts
            continue
        if all(re.fullmatch(r'-+', p.replace(':', '').strip()) for p in parts):
            continue
        row = {headers[i]: parts[i] if i < len(parts) else '' for i in range(len(headers))}
        table.append(row)
    return table


def parse_bool(v: str, default: bool = True) -> bool:
    t = (v or '').strip().lower()
    if t in {'true', 'yes', 'y', '1'}:
        return True
    if t in {'false', 'no', 'n', '0'}:
        return False
    return default


def parse_custom_models(text: str) -> Dict[str, str]:
    out = {}
    for part in [x.strip() for x in (text or '').split(';') if x.strip()]:
        if '=' in part:
            k, v = part.split('=', 1)
            out[k.strip()] = v.strip()
    return out


def parse_require_mention_map(text: str, roles_here: List[str], default: bool = True) -> Dict[str, bool]:
    raw = (text or '').strip()
    if not raw:
        return {r: default for r in roles_here}
    low = raw.lower()
    if low in {'true', 'false', 'yes', 'no', 'y', 'n', '1', '0'}:
        val = parse_bool(low, default)
        return {r: val for r in roles_here}

    out = {r: default for r in roles_here}
    for part in [x.strip() for x in raw.split(';') if x.strip()]:
        if '=' not in part:
            continue
        k, v = part.split('=', 1)
        role = k.strip()
        if role in out:
            out[role] = parse_bool(v.strip(), default)
    return out


def mention_patterns_for_role(role: str) -> List[str]:
    rid = role.lower()
    patterns = [rf'(?<!\\d)@?{re.escape(rid)}(?!\\d)']
    if rid == 'friday':
        patterns.append(r'<@!?1473334758372016128>')
    if rid == 'trouble':
        patterns.append(r'<@!?1469167632165900465>')
    return patterns


def ensure_feishu_group_allowlisted(feishu_node: dict, group_id: str):
    if feishu_node.get('groupPolicy') == 'allowlist':
        cur = feishu_node.get('groupAllowFrom')
        if not isinstance(cur, list):
            feishu_node['groupAllowFrom'] = []
            cur = feishu_node['groupAllowFrom']
        if group_id not in cur:
            cur.append(group_id)


def parse_draft(cfg: dict) -> Tuple[List[dict], List[dict], str]:
    if not DRAFT_PATH.exists():
        raise RuntimeError(f'Draft not found: {DRAFT_PATH}')
    lines = DRAFT_PATH.read_text().splitlines()
    role_rows = parse_md_table(lines, '## Roles')
    target_rows = parse_md_table(lines, '## Targets')
    guild_match = re.search(r'^- guild_id: (.+)$', DRAFT_PATH.read_text(), re.M)
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
            }
        )
    roles = [r for r in roles if r['role'] and r['base_agent_id']]
    if not roles:
        raise RuntimeError('No valid roles parsed from draft')

    managed_targets = []
    for row in target_rows:
        platform = row.get('platform', '').strip().lower()
        if platform not in SUPPORTED_PLATFORMS:
            continue
        if not parse_bool(row.get('enable', 'no'), False):
            continue
        pid = normalize_peer_id(platform, row.get('peer_id', '').strip())
        if not pid:
            continue
        managed_targets.append(
            {
                'platform': platform,
                'peer_id': pid,
                'target_name': row.get('target_name', '').strip(),
                'roles': [x.strip() for x in row.get('roles', '').split(',') if x.strip()],
                'require_mention': row.get('require_mention', '').strip(),
                'custom_models': row.get('custom_models', '').strip(),
            }
        )

    return roles, managed_targets, guild_id


def apply_from_draft(cfg: dict):
    roles, managed_targets, guild_id = parse_draft(cfg)

    keep_agents = []
    for a in cfg.get('agents', {}).get('list', []):
        aid = a.get('id', '')
        if any(aid.startswith(f"{role['role']}_ch_") or aid.startswith(f"{role['role']}_fg_") for role in roles):
            continue
        keep_agents.append(a)

    for target in managed_targets:
        platform = target['platform']
        custom = parse_custom_models(target['custom_models'])
        for role in roles:
            if role['role'] not in target['roles']:
                continue
            if platform == 'discord' and not role.get('discord_account'):
                continue
            if platform == 'feishu' and not role.get('feishu_account'):
                continue
            aid = make_agent_id(role['role'], platform, target['peer_id'])
            workspace = detect_workspace(
                cfg,
                role['role'],
                platform,
                '/root/clawd-agent2' if role['role'] == 'friday' else '/root/clawd',
            )
            agent = {
                'id': aid,
                'name': f"{role['role']}@{platform}#{target['peer_id']}",
                'workspace': workspace,
                'model': {'primary': custom.get(role['role'], role['default_model'])},
            }
            if platform == 'discord':
                agent['groupChat'] = {'mentionPatterns': mention_patterns_for_role(role['role'])}
            keep_agents.append(agent)

    for a in keep_agents:
        if a.get('id') in [r['base_agent_id'] for r in roles]:
            gc = a.setdefault('groupChat', {})
            cur = list(gc.get('mentionPatterns') or [])
            role = a.get('id')
            merged = []
            for x in cur + mention_patterns_for_role(role):
                if x not in merged:
                    merged.append(x)
            gc['mentionPatterns'] = merged

    cfg.setdefault('agents', {})['list'] = keep_agents

    new_bindings = []
    for b in cfg.get('bindings', []):
        m = b.get('match', {})
        peer = m.get('peer', {}) if isinstance(m.get('peer', {}), dict) else {}
        aid = b.get('agentId', '')
        if m.get('channel') == 'discord' and peer.get('kind') == 'channel' and any(
            aid.startswith(f"{role['role']}_ch_") for role in roles
        ):
            continue
        if m.get('channel') == 'feishu' and peer.get('kind') == 'group' and any(
            aid.startswith(f"{role['role']}_fg_") for role in roles
        ):
            continue
        new_bindings.append(b)

    discord = cfg.setdefault('channels', {}).setdefault('discord', {})
    feishu = cfg.setdefault('channels', {}).setdefault('feishu', {})
    discord_accounts = discord.setdefault('accounts', {})
    feishu_accounts = feishu.setdefault('accounts', {})

    for role in roles:
        if role.get('discord_account'):
            acc = discord_accounts.setdefault(role['discord_account'], {})
            acc['allowBots'] = True
            guilds = acc.setdefault('guilds', {})
            g = guilds.setdefault(guild_id, {})
            g['requireMention'] = True
            if not isinstance(g.get('channels'), dict):
                g['channels'] = {}
        if role.get('feishu_account'):
            acc = feishu_accounts.setdefault(role['feishu_account'], {})
            if not isinstance(acc.get('groups'), dict):
                acc['groups'] = {}

    for target in managed_targets:
        platform = target['platform']
        req_map = parse_require_mention_map(target['require_mention'], target['roles'], True)
        for role in roles:
            role_name = role['role']
            if role_name not in target['roles']:
                continue
            if platform == 'discord':
                account_id = role.get('discord_account', '')
                if not account_id:
                    continue
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
                    'requireMention': req_map.get(role_name, True),
                }
            elif platform == 'feishu':
                account_id = role.get('feishu_account', '')
                if not account_id:
                    continue
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
                acc = feishu_accounts[account_id]
                groups = acc.setdefault('groups', {})
                cur = groups.get(target['peer_id'], {}) if isinstance(groups.get(target['peer_id']), dict) else {}
                cur['requireMention'] = req_map.get(role_name, True)
                groups[target['peer_id']] = cur
                ensure_feishu_group_allowlisted(acc, target['peer_id'])
                ensure_feishu_group_allowlisted(feishu, target['peer_id'])

    discord.setdefault('guilds', {}).setdefault(guild_id, {})['channels'] = {
        '*': {'allow': True, 'requireMention': True}
    }
    cfg['bindings'] = new_bindings
    return cfg, roles, managed_targets, guild_id


def write_report(text: str):
    REPORT_PATH.write_text(text)


def cmd_scan(args):
    cfg = load_json(CONFIG_PATH)
    guild_id = args.guild_id or find_default_guild(cfg)
    if 'discord' in args.platforms.split(',') and not guild_id:
        raise RuntimeError('No Discord guild found; pass --guild-id explicitly')
    roles, items, platforms = generate_draft(cfg, guild_id or '', args.roles, args.platforms)
    msg = []
    msg.append('✅ Draft generated')
    msg.append(f'- guild_id: {guild_id or "-"}')
    msg.append(f'- platforms: {", ".join(platforms)}')
    msg.append(f'- roles: {", ".join(r["role"] for r in roles)}')
    msg.append(f'- targets discovered: {len(items)}')
    msg.append(f'- draft: {DRAFT_PATH}')
    out = '\n'.join(msg) + '\n'
    write_report(out)
    print(out)


def cmd_explain(args):
    if not DRAFT_PATH.exists():
        raise RuntimeError(f'Draft not found: {DRAFT_PATH}')
    text = f"""Draft file: {DRAFT_PATH}

How to edit:
- In ## Targets, set `enable` to yes/no.
- In `roles`, use comma-separated role ids from ## Roles.
- `platform` supports `discord` and `feishu`.
- `require_mention=true` means the bot only replies when mentioned.
- `custom_models` format: role=model;role=model
- Feishu `peer_id` may be `oc_xxx` or `chat:oc_xxx`.
- After editing, run: python3 {WORKSPACE}/scripts/discord_team_bootstrap.py apply --validate
"""
    write_report(text)
    print(text)


def cmd_apply(args):
    cfg = load_json(CONFIG_PATH)
    backup = CONFIG_PATH.with_suffix(CONFIG_PATH.suffix + '.bak.discord-team-bootstrap')
    backup.write_text(CONFIG_PATH.read_text())
    try:
        new_cfg, roles, managed_targets, guild_id = apply_from_draft(cfg)
        report = []
        report.append('✅ Draft parsed')
        report.append(f'- guild_id: {guild_id or "-"}')
        report.append(f'- roles: {", ".join(r["role"] for r in roles)}')
        report.append(f'- managed_targets: {len(managed_targets)}')
        report.append('- planned_changes:')
        for row in managed_targets:
            report.append(
                f"  - [{row.get('platform','')}] {row.get('target_name','')} ({row.get('peer_id','')}): roles={','.join(row.get('roles',[]))} require_mention={row.get('require_mention','')} custom_models={row.get('custom_models','') or '-'}"
            )

        if args.dry_run:
            text = '\n'.join(report + ['- dry_run: true', f'- backup: {backup}']) + '\n'
            write_report(text)
            print(text)
            return

        save_json(CONFIG_PATH, new_cfg)
        report[0] = '✅ Draft applied'
        if args.validate:
            v = run(['python3', VALIDATE_SCRIPT], check=False)
            if v.returncode != 0:
                raise RuntimeError(f'config validate failed:\n{v.stdout}\n{v.stderr}')
            h = run(['openclaw', 'gateway', 'health'], check=False)
            report.append('- validate: ok')
            report.append(f'- gateway_health_code: {h.returncode}')
            if h.stdout.strip():
                report.append('- gateway_health_output:')
                report.append(h.stdout.strip())
        report.append(f'- backup: {backup}')
        text = '\n'.join(report) + '\n'
        write_report(text)
        print(text)
    except Exception:
        CONFIG_PATH.write_text(backup.read_text())
        raise


def cmd_inspect(args):
    cfg = load_json(CONFIG_PATH)
    roles, managed_targets, guild_id = parse_draft(cfg)
    report = []
    report.append('# Inspect Report')
    report.append(f'- guild_id: {guild_id or "-"}')
    report.append(f'- roles: {", ".join(r["role"] for r in roles)}')
    report.append('')
    for row in managed_targets:
        report.append(
            f"- [{row.get('platform','')}] {row.get('peer_id','')} {row.get('target_name','')}: roles={','.join(row.get('roles',[]))} require_mention={row.get('require_mention','')}"
        )
    text = '\n'.join(report) + '\n'
    write_report(text)
    print(text)


def main():
    ap = argparse.ArgumentParser(description='Discord / Feishu multi-agent bootstrap skill')
    sub = ap.add_subparsers(dest='cmd', required=True)

    p1 = sub.add_parser('scan')
    p1.add_argument('--guild-id', default='')
    p1.add_argument('--roles', default='trouble,friday')
    p1.add_argument('--platforms', default='discord,feishu')
    p1.set_defaults(func=cmd_scan)

    p2 = sub.add_parser('explain')
    p2.set_defaults(func=cmd_explain)

    p3 = sub.add_parser('apply')
    p3.add_argument('--validate', action='store_true')
    p3.add_argument('--dry-run', action='store_true')
    p3.set_defaults(func=cmd_apply)

    p4 = sub.add_parser('inspect')
    p4.set_defaults(func=cmd_inspect)

    args = ap.parse_args()
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    args.func(args)


if __name__ == '__main__':
    main()
