"""Behavioral regression checks. Synthetic samples and expected answers stay here."""
from __future__ import annotations

import contextlib
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = (Path(__file__).resolve().parents[3] / "tanyuan")
sys.path.insert(0, str(ROOT))
from tanyuan import blobs, cache, cli, config, garden, registry
from tanyuan.envelope import Result, success
from tanyuan.errors import Code, TanyuanError, auth_failed, rate_limited, unavailable
from tanyuan.source_sets import registry_path, validate_registry
from tanyuan.tools import capital, events, history, industry, macro, newsfeed, search

registry.load_tools()


class LocalState(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.old_conn = cache._CONN
        self.patches = [patch.object(config, 'CACHE_DIR', self.base / 'cache'),
                        patch.object(cache, '_CONN', None)]
        for p in self.patches:
            p.start()
        self.addCleanup(self.restore_state)

    def restore_state(self):
        if cache._CONN is not None:
            cache._CONN.close()
        for p in reversed(self.patches):
            p.stop()


class EarningsTests(unittest.TestCase):
    def test_transport_failures_are_not_empty_results(self):
        for error in (auth_failed('denied'), unavailable('offline'), rate_limited('busy')):
            with self.subTest(code=error.code), patch.object(events.tushare, 'call', side_effect=error):
                env, _ = registry.dispatch('fundamental.earnings', {'code': '600100'}, use_cache=False)
                self.assertFalse(env['ok'])
                self.assertEqual(env['error']['code'], error.code.value)

    def test_partial_success_preserves_source_failure_and_units(self):
        row = {'ts_code': '600100.SH', 'ann_date': '20240220', 'end_date': '20231231',
               'revenue': 1234567.891, 'n_income': 76543.21, 'yoy_net_profit': 54321.0}
        with patch.object(events.tushare, 'call', side_effect=[auth_failed('forecast denied'), [row]]):
            result = events.fundamental_earnings({'code': '600100', 'kind': 'both'})
        self.assertFalse(result.data['complete'])
        self.assertEqual(result.data['failed_sources'], ['forecast'])
        event = result.data['events'][0]
        self.assertEqual(event['revenue'], row['revenue'])
        self.assertNotIn('revenue_wan', event)
        self.assertEqual(result.units['express']['revenue'], '元')
        self.assertEqual(result.units['express']['yoy_net_profit'], '元')
        self.assertIsNone(result.as_of)

    def test_limit_does_not_skip_second_source(self):
        old = {'ann_date': '20230301', 'end_date': '20221231', 'net_profit_min': 11}
        new = {'ann_date': '20240301', 'end_date': '20231231', 'revenue': 200}
        with patch.object(events.tushare, 'call', side_effect=[[old], [new]]) as upstream:
            result = events.fundamental_earnings({'code': '600100', 'kind': 'both', 'limit': 1})
        self.assertEqual(upstream.call_count, 2)
        self.assertEqual(result.data['events'][0]['kind'], 'express')
        self.assertEqual(result.data['total'], 2)
        self.assertTrue(result.data['truncated'])

    def test_empty_success_and_failure_is_failure(self):
        with patch.object(events.tushare, 'call', side_effect=[[], unavailable('offline')]):
            with self.assertRaises(TanyuanError) as raised:
                events.fundamental_earnings({'code': '600100', 'kind': 'both'})
        self.assertEqual(raised.exception.code, Code.SOURCE_UNAVAILABLE)


class ValueTests(unittest.TestCase):
    def test_industry_preserves_upstream_value(self):
        row = {'ts_code': '801010.SI', 'name': '行业', 'total_mv': 123456.789,
               'amount': 9876.543, 'pct_change': 0}
        with patch.object(industry.tushare, 'call', return_value=[row]), patch.object(industry, '_sw_levels', return_value={}):
            r = industry.industry_ranking({'date': '2024-03-01', 'level': 'all'})
        self.assertEqual(r.data['top'][0]['total_mv'], row['total_mv'])
        self.assertNotIn('total_mv_yi', r.data['top'][0])
        self.assertEqual(r.units['total_mv'], '万元')

    def test_northbound_different_metrics_stay_separate(self):
        rows = [dict(trade_date='20240816', north_money='-1200.5', hgt='-800', sgt='-400.5', south_money='900'),
                dict(trade_date='20240819', north_money='25000.125', hgt='10000', sgt='15000.125', south_money='901')]
        with patch.object(capital.tushare, 'call', return_value=rows):
            r = capital.market_northbound({'start_date': '2024-08-01', 'end_date': '2024-08-20'})
        current, old = r.data['series']
        self.assertEqual(current['north_turnover'], 25000.125)
        self.assertIsNone(current['north_net_buy'])
        self.assertEqual(old['north_net_buy'], -1200.5)
        self.assertIsNone(old['north_turnover'])
        self.assertEqual(current['raw']['south_money'], '901')
        self.assertIsNone(r.units['raw.south_money'])

    def test_unavailable_metric_is_not_substituted(self):
        with patch.object(capital.tushare, 'call') as upstream:
            with self.assertRaises(TanyuanError) as raised:
                capital.market_northbound({'start_date': '2025-01-01', 'end_date': '2025-02-01', 'metric': 'net_buy'})
            self.assertEqual(raised.exception.code, Code.UNSUPPORTED)
            upstream.assert_not_called()

    def test_margin_distinguishes_zero_missing_and_precision(self):
        row = {'trade_date': '20240301', 'rzye': None, 'rzmre': 0, 'rzche': 123456789.123, 'rqye': 0}
        with patch.object(capital.tushare, 'call', return_value=[row]):
            r = capital.market_margin({'code': '600100', 'end_date': '2024-03-01'})
        item = r.data['series'][0]
        self.assertIsNone(item['rzye_yi'])
        self.assertEqual(item['rzmre_yi'], 0)
        self.assertEqual(item['rqye_yi'], 0)
        self.assertEqual(item['raw']['rzche'], row['rzche'])
        self.assertAlmostEqual(item['rzche_yi'], 1.23456789123)


class TimeAndArgumentTests(unittest.TestCase):
    def test_unknown_asof_is_not_observation_time(self):
        r = Result(data={}, observed_at='2024-02-01T00:00:00+08:00')
        env = success('sample', {}, r, duration_ms=0)
        self.assertIsNone(env['provenance']['as_of'])
        self.assertEqual(env['provenance']['observed_at'], r.observed_at)

    def test_lpr_uses_source_date(self):
        payload = {'result': {'data': [{'TRADE_DATE': '2024-02-20 00:00:00', 'LPR1Y': 3.45, 'LPR5Y': 3.95}]}}
        with patch.object(macro.http, 'get_json', return_value=payload):
            r = macro.macro_lpr({})
        self.assertEqual(r.as_of, '2024-02-20')

    def test_historical_windows_are_relative_to_end(self):
        calls = [events.fundamental_earnings, events.market_flow, history._market_daily_one,
                 history.fundamental_valuation_history, capital.market_margin, capital.market_northbound]
        for func in calls:
            with self.subTest(func=func.__name__), patch('tanyuan.tushare.call', return_value=[]) as request:
                try:
                    func({'code': '600100', 'end_date': '2018-06-30'})
                except TanyuanError as exc:
                    self.assertEqual(exc.code, Code.NO_DATA)
                params = request.call_args.args[1]
                self.assertLess(params['start_date'], params['end_date'])
                self.assertEqual(params['end_date'], '20180630')

    def test_ambiguous_symbol_across_tools(self):
        for name in ['market.daily', 'market.quote', 'fundamental.earnings', 'market.margin', 'market.kline']:
            with self.subTest(name=name):
                env, _ = registry.dispatch(name, {'code': '000001'}, use_cache=False)
                self.assertEqual(env['error']['code'], 'AMBIGUOUS')


class CacheAndBlobTests(LocalState):
    def test_cache_io_failure_keeps_successful_result(self):
        spec = registry.REGISTRY['market.quote']
        with patch.object(spec, 'func', return_value=Result(data={'value': 17})), \
             patch.object(cache, 'get', side_effect=sqlite3.OperationalError('locked')), \
             patch.object(cache, 'put', side_effect=OSError('disk full')):
            env, code = registry.dispatch('market.quote', {'code': '600100'})
        self.assertEqual(code, 0)
        self.assertEqual(env['data']['value'], 17)
        self.assertEqual([w['operation'] for w in env['provenance']['warnings']], ['read', 'write'])

    def test_refresh_repeats_fetch_and_invalidates_other_views(self):
        url = 'https://example.org/report'
        with patch.object(search, '_fetch_page', side_effect=[('A' * 1600, 'a', 'upstream', []),
                                                            ('B' * 1600, 'b', 'upstream', []),
                                                            ('C' * 1600, 'c', 'upstream', [])]) as upstream:
            registry.dispatch('fetch.url', {'url': url, 'offset': 500})
            first, _ = registry.dispatch('fetch.url', {'url': url, 'refresh': True})
            second, _ = registry.dispatch('fetch.url', {'url': url, 'refresh': True})
            page, _ = registry.dispatch('fetch.url', {'url': url, 'offset': 500})
        self.assertEqual(upstream.call_count, 3)
        self.assertEqual(first['data']['content'][0], 'B')
        self.assertEqual(second['data']['content'][0], 'C')
        self.assertEqual(page['data']['content'][0], 'C')
        self.assertEqual(page['provenance']['cost']['amount'], 0)
        self.assertEqual(page['provenance']['observed_at'], second['provenance']['observed_at'])
        self.assertIsNone(page['provenance']['as_of'])

    def test_blob_storage_failure_does_not_promise_continuation(self):
        with patch.object(search, '_fetch_page', return_value=('X' * 1600, 'x', 'upstream', [])), \
             patch.object(blobs, 'store', return_value={'path': None, 'chars': 1600, 'sha256': 'abc'}):
            with self.assertRaises(TanyuanError) as raised:
                search.fetch_url({'url': 'https://example.org/report', 'max_chars': 500})
        self.assertEqual(raised.exception.code, Code.SOURCE_UNAVAILABLE)

    def test_cli_cache_failure_is_one_json(self):
        env = {**os.environ, 'TANYUAN_CACHE': '/dev/null', 'PYTHONDONTWRITEBYTECODE': '1'}
        proc = subprocess.run([sys.executable, str(ROOT / 'bin' / 'ty'), 'cache', 'stats'],
                              capture_output=True, text=True, env=env, check=False)
        self.assertEqual(proc.returncode, 1)
        parsed = json.loads(proc.stdout)
        self.assertEqual(parsed['error']['code'], 'INTERNAL')
        self.assertNotIn('Traceback', proc.stdout + proc.stderr)


class NewsTests(unittest.TestCase):
    def sources(self):
        return {'sectors': {'s': 'S'}, 'sources': [dict(name=f's{i}', sector='s', url=f'https://example.org/{i}') for i in range(65)]}

    @staticmethod
    def fetch(source, timeout):
        return source, [dict(title=source['name'], url=source['url'], published_ts=time.time(), summary='')], None

    def test_source_paging_discloses_and_recovers_omissions(self):
        with patch.object(newsfeed, 'load_sources', return_value=self.sources()), patch.object(newsfeed, '_fetch_one', side_effect=self.fetch):
            first = newsfeed.news_feed({'limit': 200})
            second = newsfeed.news_feed({'source_offset': 60, 'limit': 200})
        self.assertEqual(first.data['coverage']['available_sources'], 65)
        self.assertEqual(first.data['coverage']['selected_sources'], 60)
        self.assertEqual(first.data['coverage']['omitted_sources'], 5)
        self.assertEqual(first.data['coverage']['next_source_offset'], 60)
        self.assertEqual(second.data['coverage']['selected_sources'], 5)
        self.assertIsNone(second.data['coverage']['next_source_offset'])
        self.assertEqual(len({r['url'] for r in first.data['items'] + second.data['items']}), 65)

    def test_empty_match_keeps_partial_coverage(self):
        with patch.object(newsfeed, 'load_sources', return_value=self.sources()), patch.object(newsfeed, '_fetch_one', side_effect=self.fetch):
            result = newsfeed.news_feed({'keyword': 'absent'})
        self.assertEqual(result.data['items'], [])
        self.assertEqual(result.data['coverage']['omitted_sources'], 5)
        self.assertTrue(result.warnings)

    def test_item_limit_is_explicit(self):
        with patch.object(newsfeed, 'load_sources', return_value=self.sources()), patch.object(newsfeed, '_fetch_one', side_effect=self.fetch):
            result = newsfeed.news_feed({'limit': 3})
        self.assertEqual(result.data['coverage']['returned_items'], 3)
        self.assertEqual(result.data['coverage']['omitted_by_response_limit'], 57)


class SourcePathTests(LocalState):
    def test_names_and_external_symlinks_rejected(self):
        root = self.base / 'sets'
        root.mkdir()
        outside = self.base / 'outside.json'
        outside.write_text('{}')
        (root / 'escape.json').symlink_to(outside)
        for name in ['../outside', str(outside), 'escape', 'a/b']:
            with self.subTest(name=name), self.assertRaises(TanyuanError):
                registry_path(root, name)

    def test_restore_validates_before_overwrite_and_requires_confirmation(self):
        root = self.base / 'sets'
        root.mkdir()
        target = root / 'sample.json'
        original = '{"sources": [], "source_set": "sample"}'
        target.write_text(original)
        broken = self.base / 'broken.json'
        broken.write_text('{"sources": "not-a-list"}')
        with patch.object(garden, 'SOURCE_SETS', root):
            for yes in [False, True]:
                with self.assertRaises(TanyuanError):
                    garden.restore(str(broken), source_set='sample', yes=yes)
                self.assertEqual(target.read_text(), original)


class BoundaryTests(unittest.TestCase):
    def test_earnings_combines_code_and_announcement_date(self):
        with patch.object(events.tushare, 'call', return_value=[]) as transport:
            with self.assertRaises(TanyuanError) as error:
                events.fundamental_earnings({'code': '600100', 'ann_date': '2024-02-20'})
            self.assertEqual(error.exception.code, Code.NO_DATA)
            self.assertEqual(transport.call_args.args[1], {'ts_code': '600100.SH', 'ann_date': '20240220'})

    def test_invalid_source_date_cannot_choose_a_regime(self):
        for day in (None, '', '20249999'):
            with self.subTest(day=day), patch.object(capital.tushare, 'call', return_value=[{'trade_date': day}]):
                with self.assertRaises(TanyuanError) as error:
                    capital.market_northbound({'end_date': '2024-08-20'})
                self.assertEqual(error.exception.code, Code.SOURCE_SCHEMA_CHANGED)

    def test_oversized_blob_does_not_silently_clip(self):
        with patch.object(blobs, 'MAX_STORE_CHARS', 10), patch.object(blobs, 'blob_dir') as directory:
            with self.assertRaises(TanyuanError):
                blobs.store('x' * 11)
            directory.assert_not_called()


if __name__ == '__main__':
    unittest.main()
