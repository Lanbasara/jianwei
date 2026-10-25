"""Offline CLI behavior and discovery contracts; no financial question/answer fixtures."""
from __future__ import annotations

import contextlib
import io
import json
import re
import shlex
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'tanyuan'))
from tanyuan import cli, config, registry
from tanyuan.envelope import Result
from tanyuan.errors import Code, ERROR_GUIDANCE, TanyuanError, bad_arg
from tanyuan.interface import build_parser, topic_help

registry.load_tools()


class CliTests(unittest.TestCase):
    def invoke(self, args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(args)
        self.assertEqual(err.getvalue(), '', args)
        return code, json.loads(out.getvalue())

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='ty boundary ')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.network = patch('requests.sessions.Session.request', side_effect=AssertionError('network forbidden'))
        self.network.start()
        self.addCleanup(self.network.stop)

    def test_help_tree_never_reads_credentials_or_invokes_operations(self):
        _, parsers = build_parser()
        with patch.object(config, 'secret', side_effect=AssertionError('credentials read')), \
                patch.object(registry, 'dispatch', side_effect=AssertionError('tool executed')), \
                patch.object(cli, '_cache_command', side_effect=AssertionError('cache changed')), \
                patch.object(cli, '_garden_command', side_effect=AssertionError('garden executed')), \
                patch.object(cli, '_doctor', side_effect=AssertionError('doctor executed')):
            for path in parsers:
                for args in ([*path, '--help'], ['help', *path]):
                    with self.subTest(args=args):
                        code, result = self.invoke(args)
                        self.assertEqual(code, 0)
                        self.assertTrue(result['ok'])
                        self.assertIn('usage', result['data'])
            for topic in ('config', 'response', 'errors'):
                self.assertEqual(self.invoke(['help', topic])[0], 0)
            self.assertEqual(self.invoke([])[0], 0)
            self.assertEqual(self.invoke(['call', 'market.quote', '--json', 'invalid', '--help'])[0], 0)

    def test_tool_help_and_describe_use_the_same_definition(self):
        for name, spec in registry.REGISTRY.items():
            with self.subTest(tool=name):
                _, d = self.invoke(['describe', name])
                _, h = self.invoke(['call', name, '--help'])
                self.assertEqual(d['data'], spec.describe())
                self.assertEqual(h['data']['tool'], d['data'])
                for example in d['data']['examples']:
                    registry.validate_args(spec, example['args'])
                    tokens = shlex.split(example['cmd'])
                    self.assertEqual(tokens[:3], ['ty', 'call', name])
                    sample = json.loads(tokens[tokens.index('--json')+1])
                    self.assertEqual(sample, example['args'])
                    registry.validate_args(spec, sample)

    def test_invalid_cli_input_stops_before_dispatch_or_mutation(self):
        cases = [
            ['tools', '--bogus'], ['tools', 'extra'], ['describe', 'market.quote', 'extra'],
            ['doctor', '--bogus'], ['--version', 'tools'], ['version', 'extra'],
            ['call'], ['call', 'market.quote', '--json'],
            ['call', 'market.quote', '--jso', '{}'],
            ['call', 'market.quote', '--json', '{"code":"a","code":"b"}'],
            ['call', 'market.quote', '--json', '{"code":"a"}', '--arg', 'code=b'],
            ['call', 'market.quote', '--arg', 'code=a', '--arg', 'code=b'],
            ['call', 'market.quote', '--arg', '=value'],
            ['call', 'market.quote', '--arg', 'code'],
            ['call', 'market.quote', '--arg', 'codes=[broken'],
            ['call', 'market.quote', '--json', '{}', '--json', '{}'],
            ['call', 'market.quote', '--json', '[]'],
            ['call', 'market.quote', '--json', '{"code":NaN}'],
            ['call', 'market.quote', '--json', '{"code":1e999}'],
            ['cache', 'clear'], ['cache', 'clear', '--all'],
            ['cache', 'clear', '--all', '--yes', '--kv'],
            ['cache', 'clear', '--all', '--yes', '--bogus'],
            ['cache', 'stats', '--days', '2'],
            ['cache', 'prune', '--days', '-1'], ['cache', 'prune', '--days', 'nan'],
            ['cache', 'prune', '--days', 'inf'], ['cache', 'prune', '--days'],
            ['cache', 'prune', '--days', '1', '--days', '2'],
            ['garden', 'check', '--workers', '0'], ['garden', 'check', '--workers', '33'],
            ['garden', 'check', '--workers', 'x'], ['garden', 'status', '--out', 'unexpected'],
            ['garden', 'apply', '--plan', 'file'], ['garden', 'restore', '--yes'],
        ]
        with patch.object(registry, 'dispatch') as dispatch, \
                patch('tanyuan.cache.clear_all') as clear, patch('tanyuan.cache.prune') as prune, \
                patch('tanyuan.garden.check') as check, patch.object(cli, '_doctor') as doctor:
            for args in cases:
                with self.subTest(args=args):
                    code, result = self.invoke(args)
                    self.assertEqual(code, 2)
                    self.assertEqual(result['error']['code'], 'BAD_ARGUMENT')
                    self.assertTrue(result['error']['hint'])
            for operation in (dispatch, clear, prune, check, doctor):
                operation.assert_not_called()

    def test_feedback_supports_correction_without_running_bad_requests(self):
        handler = Mock(return_value=Result(data={'accepted': True}))
        spec = registry.ToolSpec('test.measure', 'synthetic', handler,
                                args_schema={'count': {'type': 'integer', 'required': True, 'min': 1, 'max': 4}})
        with patch.dict(registry.REGISTRY, {spec.name: spec}), patch.object(registry, '_log'):
            code, result = self.invoke(['call', spec.name, '--json', '{"count":99}'])
            self.assertEqual(code, 2)
            handler.assert_not_called()
            details = result['error']['details']
            self.assertEqual(details['expected'], spec.describe()['args']['count'])
            repaired = {details['field']: details['expected']['max']}
            code, result = self.invoke(['call', spec.name, '--json', json.dumps(repaired)])
            self.assertEqual(code, 0)
            self.assertTrue(result['data']['accepted'])
            handler.assert_called_once_with({'count': 4})

    def test_schema_invalid_values_and_conflicts_do_not_reach_tools(self):
        cases = [
            ('market.quote', {}), ('market.quote', {'codes': []}),
            ('market.quote', {'codes': ['a']*61}), ('market.quote', {'code': ' '}),
            ('market.quote', {'code': 'a', 'codes': ['b']}),
            ('market.quote_global', {'codes': ['']}),
            ('news.feed', {'sector': 'ai', 'sectors': ['semi']}),
            ('news.feed', {'since': '7d', 'since_hours': 24}),
            ('market.kline', {'code': 'x', 'count': True}),
            ('market.kline', {'code': 'x', 'count': None}),
            ('market.kline', {'code': 'x', 'period': 'wrong'}),
            ('industry.ranking', {'date': '2025-02-30'}),
            ('industry.ranking', {'date': '2025-01-01garbage'}),
            ('industry.ranking', {'date': '202501-01'}),
        ]
        for name, args in cases:
            spec = registry.REGISTRY[name]
            with self.subTest(name=name, args=args), patch.object(spec, 'func') as handler:
                code, result = self.invoke(['call', name, '--json', json.dumps(args)])
                self.assertEqual(code, 2)
                self.assertTrue(result['error']['details'])
                handler.assert_not_called()

    def test_numeric_coercion_rejects_non_finite_values(self):
        spec = registry.ToolSpec('test.number', 'synthetic', lambda _: None,
                                 args_schema={'value': {'type': 'number'}})
        for value in (float('nan'), float('inf'), '-Infinity', '1e999'):
            with self.subTest(value=value), self.assertRaises(TanyuanError):
                registry.validate_args(spec, {'value': value})

    def test_date_normalization_is_explicit_and_preserves_codes(self):
        spec = registry.REGISTRY['fundamental.earnings']
        clean = registry.validate_args(spec, {'code': '000858', 'start_date': '20240229'})
        self.assertEqual(clean['code'], '000858')
        self.assertEqual(clean['start_date'], '2024-02-29')
        self.assertNotIn('format', registry.REGISTRY['macro.indicator'].args_schema['start'])

    def test_explicit_cache_selection_and_garden_approval_reach_exact_operations(self):
        with patch('tanyuan.cache.clear_all', return_value={'cleared': True}) as clear:
            self.assertEqual(self.invoke(['cache', 'clear', '--kv'])[0], 0)
            clear.assert_called_once_with(kv=True, blobs=False, usage=False, reference=False)
        with patch('tanyuan.cache.clear_all', return_value={'cleared': True}) as clear:
            self.assertEqual(self.invoke(['cache', 'clear', '--all', '--yes'])[0], 0)
            clear.assert_called_once_with(kv=True, blobs=True, usage=True, reference=True)
        with patch('tanyuan.garden.apply_plan', return_value={'applied': []}) as apply:
            self.assertEqual(self.invoke(['garden', 'apply', '--plan', 'reviewed.json', '--yes'])[0], 0)
            apply.assert_called_once_with('reviewed.json', yes=True)

    def test_rejected_values_are_not_echoed(self):
        secret = 'PRIVATE_SENTINEL_MUST_NOT_APPEAR'
        cases = [ ['tools', secret], ['call', 'market.quote', '--unknown', secret],
                  ['call', 'market.kline', '--json', json.dumps({'code': 'x', 'count': secret})],
                  ['call', 'market.quote', '--json', json.dumps({'token': secret})],
                  ['call', 'market.quote', '--json', '{"code":"'+secret+'",}'] ]
        for args in cases:
            code, result = self.invoke(args)
            self.assertNotEqual(code, 0)
            self.assertNotIn(secret, json.dumps(result))

    def test_flag_typo_and_unknown_tool_offer_navigation(self):
        _, result = self.invoke(['tools', '--al'])
        self.assertIn('--all', result['error']['details']['suggested_options'])
        _, result = self.invoke(['call', 'market.quotez'])
        self.assertIn('market.quote', [a['tool'] for a in result['error']['alternatives']])

    def test_error_help_matches_runtime_defaults_and_preserves_failure_classes(self):
        documented = topic_help('errors')['codes']
        handler = Mock()
        spec = registry.ToolSpec('test.failure', 'synthetic', handler)
        with patch.dict(registry.REGISTRY, {spec.name: spec}), patch.object(registry, '_log'):
            for code in Code:
                handler.side_effect = TanyuanError(code, 'synthetic failure')
                status, result = self.invoke(['call', spec.name])
                self.assertEqual(status, documented[code.value]['exit_code'])
                self.assertEqual(result['error']['hint'], ERROR_GUIDANCE[code])
                self.assertEqual(result['error']['code'], code.value)
                self.assertEqual(result['error']['retryable'], documented[code.value]['retryable'])

    def test_doctor_failure_has_nonzero_status(self):
        with patch.object(cli, '_doctor', return_value={'ok': False, 'checks': []}):
            self.assertNotEqual(self.invoke(['doctor'])[0], 0)

    def test_garden_output_collision_is_rejected_before_network(self):
        output = self.base / 'user report.md'
        output.write_text('keep', encoding='utf-8')
        with patch('tanyuan.garden.check') as check:
            code, _ = self.invoke(['garden', 'check', '--report', str(output)])
            self.assertEqual(code, 2)
            check.assert_not_called()
        self.assertEqual(output.read_text(), 'keep')

    def test_skill_discovery_commands_and_reference_targets_exist(self):
        skill = (ROOT / 'tanyuan/SKILL.md').read_text()
        for reference in re.findall(r'`(references/[^`]+)`', skill):
            self.assertTrue((ROOT / 'tanyuan' / reference).is_file(), reference)
        # Literal command pointers are executable offline; placeholder invocations are demonstrated via describe.
        for text in re.findall(r'`(ty [^`]+)`', skill):
            if '<' in text:
                continue
            code, result = self.invoke(shlex.split(text)[1:])
            self.assertEqual(code, 0, text)
            self.assertTrue(result['ok'])
        self.assertEqual(self.invoke(['describe', 'market.quote'])[0], 0)


if __name__ == '__main__':
    unittest.main()
