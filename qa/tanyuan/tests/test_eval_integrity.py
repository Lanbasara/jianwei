"""Eval mechanics are tested separately from tool behavior and source semantics."""
import sys
import unittest
import json
import subprocess
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'evals'))
import score


class EvalIntegrityTests(unittest.TestCase):
    def test_runner_receives_question_but_not_truth_or_rubric(self):
        rec = {'qid': 'new', 'question': '用户问题', 'category': 'test', 'as_of': '2024-01-01',
               'expected': {'secret': 'PRIVATE_TRUTH'}, 'judges': [{'secret': 'PRIVATE_RUBRIC'}],
               'evidence': ['PRIVATE_PATH']}
        proc = SimpleNamespace(returncode=0, stdout='{"qid":"new","answer":"回答"}', stderr='')
        for mode in ('batch', 'per-question'):
            with patch.object(score.subprocess, 'run', return_value=proc) as run:
                score.run_runner('unused', [rec], mode, 1)
                payload = json.loads(run.call_args.kwargs['input'])
                self.assertEqual(set(payload), {'qid', 'question', 'category', 'as_of'})
                self.assertNotIn('PRIVATE_', run.call_args.kwargs['input'])

    def test_existing_scorer_never_claims_acceptance(self):
        summary = score.summarize([], [])
        self.assertTrue(summary['provisional'])
        self.assertFalse(summary['acceptance_valid'])
        self.assertTrue(summary['limitations'])

    def test_historical_answer_generation_entries_are_retired(self):
        base = Path(__file__).resolve().parents[1] / 'evals'
        for name in ('examples/ty_adapter.py', 'fetch_truth.py', 'build_golden.py'):
            with self.subTest(name=name), self.assertRaises(subprocess.CalledProcessError) as raised:
                subprocess.run([sys.executable, str(base / name)], check=True,
                               capture_output=True, text=True, timeout=10)
            self.assertEqual(raised.exception.stdout, '')
            self.assertTrue(raised.exception.stderr)

    def test_numeric_polarity_and_contradiction_across_values(self):
        for value in (42.75, 967.25, 2048.5):
            spec = {'type': 'numeric', 'label': 'price', 'weight': 1,
                    'keywords': ['价格'], 'accept': [{'value': value, 'unit': '元'}],
                    'tolerance': {'abs': 0.001}, 'unit_optional': False}
            for answer, expected in ((f'价格为 {value} 元', True),
                                     (f'价格不是 {value} 元而是 {value + 100} 元', False),
                                     (f'价格为 {value + 100} 元。参考数值 {value} 元', False),
                                     (f'价格不是 {value + 100} 元而是 {value} 元', True),
                                     (f'没有指标上下文,数值 {value} 元', False)):
                with self.subTest(value=value, answer=answer):
                    self.assertEqual(score.judge_numeric(spec, answer, {})[0], expected)
            rec = {'qid': 'synthetic', 'category': 'test', 'judges': [spec]}
            graded = score.grade_question(rec, {'answer': f'价格 {value} 元,另一值 {value + 100} 元'})
            self.assertTrue(graded['requires_review'])
            self.assertLess(graded['score'], 100)

    def test_negated_refusal_does_not_count(self):
        for marker in ('停牌', '休市', '无法提供'):
            spec = {'markers': [marker]}
            self.assertTrue(score.judge_refusal(spec, marker, {})[0])
            self.assertFalse(score.judge_refusal(spec, '并非' + marker, {})[0])

    def test_quarantine_excluded_and_not_sent_to_runner(self):
        rec = {'qid': 'isolated', 'category': 'test', 'validation_status': 'quarantined',
               'quarantine_reason': 'unverified source semantics'}
        result = score.grade_question(rec, {'answer': 'anything'})
        summary = score.summarize([result], [rec])
        self.assertEqual(summary['metrics']['n_questions'], 0)
        self.assertIsNone(summary['total_score'])
        self.assertEqual(len(summary['excluded_questions']), 1)
        with patch.object(score.subprocess, 'run') as run:
            self.assertEqual(score.run_runner('unused', [rec], 'batch', 1), {})
            self.assertEqual(score.run_runner('unused', [rec], 'per-question', 1), {})
            run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
