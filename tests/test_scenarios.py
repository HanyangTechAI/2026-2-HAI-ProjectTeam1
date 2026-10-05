"""Validate dataset ground truth; these are not agent performance tests."""
import copy
from datetime import datetime
import json
from pathlib import Path
import unittest

from benchmark.evaluator import evaluate
from memory.schema import MemoryItem

DATA = json.loads((Path(__file__).resolve().parents[1] / 'benchmark/scenarios.json').read_text(encoding='utf-8'))
SCENARIOS = DATA['scenarios']


def oracle(s):
    counts = s.get('context_fixture', {}).get('tokenizer_counts_after_each_build')
    input_tokens = counts[-1] if counts and s['expected_pipeline_status'] == 'ok' else s['fixed_input_tokens'] + sum(
        m['token_count'] for m in s['memory_snapshot'] if m['memory_id'] in s['assertions']['selected_memory_ids'])
    return dict(strategy='proposed', status=s['expected_pipeline_status'],
                goal_completed=s['expected_pipeline_status'] == 'ok',
                tool_calls=copy.deepcopy(s['expected_tool_calls']),
                response=s.get('expected_answers', [None])[0],
                retrieved_memory_ids=s['target_memory_ids'],
                selected_memory_ids=s['assertions']['selected_memory_ids'],
                usage=dict(input_tokens=input_tokens, fixed_input_tokens=s['fixed_input_tokens']))


class ScenarioTests(unittest.TestCase):
    def test_complete_edge_case_coverage(self):
        covered = {tag for s in SCENARIOS for tag in s['coverage']}
        self.assertTrue({f'EC-{i:02}' for i in range(1, 13)} <= covered)
        self.assertTrue({'TV-01', 'TV-02'} <= covered)
        self.assertEqual(len(SCENARIOS), len({s['scenario_id'] for s in SCENARIOS}))

    def test_memory_and_ground_truth_references(self):
        for s in SCENARIOS:
            with self.subTest(s=s['scenario_id']):
                memories = {m['memory_id']: MemoryItem.from_dict(m) for m in s['memory_snapshot']}
                self.assertEqual(len(memories), len(s['memory_snapshot']))
                for field in ('target_memory_ids', 'required_state_ids', 'applicable_constraint_ids', 'wrong_version_memory_ids'):
                    self.assertTrue(set(s[field]) <= memories.keys())
                for m in memories.values():
                    if m.supersedes:
                        self.assertEqual(memories[m.supersedes].superseded_by, m.memory_id)
                    if m.superseded_by:
                        self.assertEqual(memories[m.superseded_by].supersedes, m.memory_id)
                self.assertEqual(set(s['required_state_ids']), {r['memory_id'] for r in s['required_states']})
                self.assertEqual(set(s['applicable_constraint_ids']), {r['constraint_id'] for r in s['constraint_rules']})

    def test_evaluator_accepts_ground_truth_and_budget_failures(self):
        for s in SCENARIOS:
            if s['validation_scope'] == 'store_consistency':
                continue
            with self.subTest(s=s['scenario_id']):
                result = evaluate(s, oracle(s))
                self.assertEqual(result.task_success, s['expected_pipeline_status'] == 'ok')

    def test_temporal_ground_truth_is_valid_at_requested_time(self):
        for s in SCENARIOS:
            target = s['query_context'].get('target_time')
            if not target:
                continue
            target = datetime.fromisoformat(target)
            memories = {m['memory_id']: MemoryItem.from_dict(m) for m in s['memory_snapshot']}
            for mid in s['target_memory_ids']:
                m = memories[mid]
                if m.memory_type.value != 'state' and s['scenario_id'] != 'S17':
                    continue
                with self.subTest(s=s['scenario_id'], mid=mid):
                    self.assertLessEqual(m.valid_from, target)
                    if m.valid_to is not None:
                        self.assertLess(target, m.valid_to)

    def test_wrong_state_and_unauthorized_send_fail(self):
        for s in SCENARIOS:
            if s['expected_pipeline_status'] != 'ok':
                continue
            for state in s['required_states']:
                run = oracle(s)
                for call in run['tool_calls']:
                    if all(call.get(k) == v for k, v in state['tool_call'].items()):
                        call['arguments'][state['argument']] = 'fabricated_value'
                with self.subTest(s=s['scenario_id'], state=state['memory_id']):
                    self.assertFalse(evaluate(s, run).task_success)
            if s['constraint_rules'] and any('approval' in r for r in s['constraint_rules']):
                run = oracle(s)
                run['tool_calls'].append(dict(tool_name='email', action='send_email', arguments={'approval_id': 'not_approved'}))
                self.assertFalse(evaluate(s, run).task_success)

    def test_formatting_fixtures_fit_selection_budget_and_remove_flexible_only(self):
        for s in SCENARIOS:
            if 'context_fixture' not in s:
                continue
            f = s['context_fixture']
            memories = {m['memory_id']: m for m in s['memory_snapshot']}
            self.assertLessEqual(s['fixed_input_tokens'] + sum(memories[mid]['token_count'] for mid in f['initial_selected_memory_ids']), s['context_budget'])
            self.assertFalse(set(f['flexible_removal_order']) & set(s['assertions']['mandatory_memory_ids']))
            self.assertEqual(len(f['tokenizer_counts_after_each_build']), len(f['flexible_removal_order']) + 1)
            remaining = set(f['initial_selected_memory_ids']) - set(f['flexible_removal_order'])
            if s['expected_pipeline_status'] == 'ok':
                self.assertEqual(remaining, set(s['assertions']['selected_memory_ids']))
                self.assertLessEqual(f['tokenizer_counts_after_each_build'][-1], s['context_budget'])
            else:
                self.assertGreater(f['tokenizer_counts_after_each_build'][-1], s['context_budget'])


if __name__ == '__main__':
    unittest.main()
