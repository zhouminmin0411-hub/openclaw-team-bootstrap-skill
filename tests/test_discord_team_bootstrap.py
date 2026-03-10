import copy
import importlib.util
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / 'scripts' / 'discord_team_bootstrap.py'
SPEC = importlib.util.spec_from_file_location('discord_team_bootstrap', MODULE_PATH)
bootstrap = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = bootstrap
SPEC.loader.exec_module(bootstrap)


class DiscordTeamBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        temp_path = Path(self.tempdir.name)
        self.original_runtime = bootstrap.RUNTIME
        bootstrap.RUNTIME = bootstrap.RuntimeConfig(
            skill_dir=temp_path,
            draft_path=temp_path / 'team-setup.draft.md',
            report_path=temp_path / 'team-setup.report.md',
            config_path=temp_path / 'openclaw.json',
            validate_script=temp_path / 'validate-openclaw-config.py',
            openclaw_bin='openclaw',
            fallback_workspace='/fallback/workspace',
        )

    def tearDown(self):
        bootstrap.RUNTIME = self.original_runtime
        self.tempdir.cleanup()

    def sample_config(self):
        return {
            'agents': {
                'list': [
                    {
                        'id': 'alpha',
                        'workspace': '/work/alpha',
                        'model': {'primary': 'gpt-4.1'},
                        'groupChat': {'mentionPatterns': ['@alpha', '<@111>']},
                    },
                    {
                        'id': 'beta',
                        'workspace': '/work/beta',
                        'model': {'primary': 'gpt-4.1-mini'},
                    },
                    {
                        'id': 'alpha_ch_123',
                        'workspace': '/work/alpha',
                        'model': {'primary': 'gpt-4.1'},
                    },
                ]
            },
            'channels': {
                'discord': {
                    'accounts': {
                        'alpha': {
                            'guilds': {
                                'guild-1': {
                                    'channels': {
                                        '123': {'allow': True, 'requireMention': False},
                                    }
                                }
                            }
                        }
                    }
                },
                'feishu': {
                    'accounts': {
                        'beta': {
                            'groups': {
                                'oc_existing': {'requireMention': True},
                            }
                        }
                    }
                },
            },
            'bindings': [
                {
                    'agentId': 'alpha_ch_123',
                    'match': {
                        'channel': 'discord',
                        'accountId': 'alpha',
                        'peer': {'kind': 'channel', 'id': '123'},
                    },
                }
            ],
        }

    def write_draft(self, text: str):
        bootstrap.RUNTIME.draft_path.write_text(textwrap.dedent(text).strip() + '\n')

    def test_detect_roles_auto_detects_base_agents(self):
        roles = bootstrap.detect_roles(self.sample_config(), [])
        self.assertEqual([role['role'] for role in roles], ['alpha', 'beta'])
        self.assertEqual(roles[0]['discord_account'], 'alpha')
        self.assertEqual(roles[1]['feishu_account'], 'beta')
        self.assertEqual(roles[0]['workspace'], '/work/alpha')
        self.assertEqual(roles[0]['discord_mentions'], '@alpha;<@111>')

    def test_apply_from_draft_creates_public_reusable_config(self):
        config = self.sample_config()
        self.write_draft(
            """
            # Team Setup Draft

            - generated_at: 2026-03-10T10:00+00:00
            - mode: draft
            - guild_id: guild-1

            ## Roles
            | role | base_agent_id | discord_account | feishu_account | default_model | workspace | discord_mentions |
            |------|---------------|-----------------|----------------|---------------|-----------|------------------|
            | alpha | alpha | alpha |  | gpt-4.1 | /custom/alpha | @alpha;<@111> |
            | beta | beta |  | beta | gpt-4.1-mini | /custom/beta |  |

            ## Targets
            | platform | peer_id | target_name | enable | roles | require_mention | custom_models | notes |
            |----------|---------|-------------|--------|-------|-----------------|--------------|------|
            | discord | 456 | Ops | yes | alpha | false | alpha=gpt-5 | |
            | feishu | chat:oc_team | Team Chat | yes | beta | true |  | |
            """
        )

        new_cfg, roles, targets, guild_id = bootstrap.apply_from_draft(copy.deepcopy(config))

        self.assertEqual(guild_id, 'guild-1')
        self.assertEqual([role['role'] for role in roles], ['alpha', 'beta'])
        self.assertEqual(len(targets), 2)

        generated = {agent['id']: agent for agent in new_cfg['agents']['list']}
        self.assertIn('alpha_ch_456', generated)
        self.assertIn('beta_fg_oc_team', generated)
        self.assertEqual(generated['alpha_ch_456']['workspace'], '/custom/alpha')
        self.assertEqual(generated['alpha_ch_456']['model']['primary'], 'gpt-5')
        self.assertEqual(generated['alpha_ch_456']['groupChat']['mentionPatterns'], ['@alpha', '<@111>'])
        self.assertEqual(generated['beta_fg_oc_team']['workspace'], '/custom/beta')

        bindings = {(binding['agentId'], binding['match']['channel'], binding['match']['peer']['id']) for binding in new_cfg['bindings']}
        self.assertIn(('alpha_ch_456', 'discord', '456'), bindings)
        self.assertIn(('beta_fg_oc_team', 'feishu', 'oc_team'), bindings)
        self.assertEqual(
            new_cfg['channels']['discord']['accounts']['alpha']['guilds']['guild-1']['channels']['456']['requireMention'],
            False,
        )
        self.assertEqual(
            new_cfg['channels']['feishu']['accounts']['beta']['groups']['oc_team']['requireMention'],
            True,
        )

    def test_validate_draft_rejects_missing_platform_account(self):
        roles = [
            {
                'role': 'alpha',
                'base_agent_id': 'alpha',
                'discord_account': '',
                'feishu_account': '',
                'default_model': 'gpt-4.1',
                'workspace': '/work/alpha',
                'discord_mentions': '',
            }
        ]
        managed_targets = [
            {
                'platform': 'discord',
                'peer_id': '123',
                'target_name': 'general',
                'roles': ['alpha'],
                'require_mention': 'true',
                'custom_models': '',
            }
        ]

        with self.assertRaisesRegex(RuntimeError, 'without discord_account'):
            bootstrap.validate_draft(roles, managed_targets)

    def test_build_inspect_report_flags_drift(self):
        config = self.sample_config()
        self.write_draft(
            """
            # Team Setup Draft

            - generated_at: 2026-03-10T10:00+00:00
            - mode: draft
            - guild_id: guild-1

            ## Roles
            | role | base_agent_id | discord_account | feishu_account | default_model | workspace | discord_mentions |
            |------|---------------|-----------------|----------------|---------------|-----------|------------------|
            | alpha | alpha | alpha |  | gpt-4.1 | /work/alpha | @alpha |

            ## Targets
            | platform | peer_id | target_name | enable | roles | require_mention | custom_models | notes |
            |----------|---------|-------------|--------|-------|-----------------|--------------|------|
            | discord | 999 | Missing Channel | yes | alpha | false |  | |
            """
        )

        report = bootstrap.build_inspect_report(config)
        self.assertIn('status=mismatch', report)
        self.assertIn('agent missing', report)
        self.assertIn('binding missing', report)

    def test_split_md_row_supports_escaped_pipes(self):
        cells = bootstrap.split_md_row(r'| discord | 123 | name with \| pipe | yes | alpha | true |  | note \| here |')
        self.assertEqual(cells[2], 'name with | pipe')
        self.assertEqual(cells[7], 'note | here')


if __name__ == '__main__':
    unittest.main()
