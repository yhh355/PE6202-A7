"""Offline regression of version boundaries and fake API transport, not live evidence."""
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import backends
import config
import tools
from agent import run_case
from d2b_contracts import descriptors, observation, TARGET
from test_guardrails import AttemptBackend


class IntegrationTests(unittest.TestCase):
    def test_only_one_descriptor_changes(self):
        a, b = descriptors(tools.DESCRIPTORS, 'v1'), descriptors(tools.DESCRIPTORS, 'v2')
        self.assertEqual([TARGET], [name for name in a if a[name] != b[name]])

    def test_early_stop_removes_scheduling_fields_only(self):
        raw = tools.check_referral_criteria('OPH', 'REF-5590')
        old = copy.deepcopy(raw)
        result = observation(TARGET, raw, 'v2')
        self.assertEqual(old, raw)
        self.assertNotIn('band', result)
        self.assertNotIn('window_weeks', result)
        self.assertEqual({k:v for k,v in raw.items() if k not in ('band', 'window_weeks')}, result)
        self.assertEqual(raw, observation(TARGET, raw, 'v1'))

    def test_eligible_and_other_returns_unchanged(self):
        raw = tools.check_referral_criteria('OPH', 'REF-5602')
        self.assertEqual(raw, observation(TARGET, raw, 'v2'))
        for name in tools.REGISTRY['B']:
            if name != TARGET:
                value = '2026-09-09' if name == 'as_of' else {'sentinel':1}
                self.assertEqual(value, observation(name, value, 'v2'))

    def test_all_tools_have_six_fields_in_both_versions(self):
        for version in ('v1','v2'):
            contracts = descriptors(tools.DESCRIPTORS,version)
            for name in tools.REGISTRY['B']:
                for field in ('name','signature','purpose','args','returns','size_bound','failure','irreversible'):
                    self.assertIn(field,contracts[name])
                self.assertTrue(contracts[name]['size_bound'])

    def test_common_size_limit_rejects_oversize_without_truncation(self):
        for version in ('v1','v2'):
            with self.assertRaises(ValueError):
                observation('get_referral',{'clinical_summary':'x'*17000},version)

    def test_budget_stops_before_transport(self):
        from live_budget import LiveBudget
        budget=LiveBudget(0.00001,0.15,0.60,Path('/unused-test-path'))
        with patch.object(config,'API_KEY','test-placeholder'), patch.object(config,'LIVE_BUDGET',budget,create=True), patch.object(backends.urllib.request,'urlopen') as transport:
            with self.assertRaises(RuntimeError):
                backends._live_call([{'role':'user','content':'test'}])
            transport.assert_not_called()

    def test_response_bound_is_enforced(self):
        raw = tools.check_referral_criteria('OPH', 'REF-5590')
        raw['red_flag_term'] = 'x'*201
        with self.assertRaises(ValueError):
            observation(TARGET, raw, 'v2')

    def test_usage_and_case_id(self):
        b = backends.LiveBackend('REF-5602', [], 'instructions')
        with patch.object(backends, '_live_call', return_value=(
            '{"final":{"decision":"escalate"}}', (101, 23))) as api:
            b.next_move([])
            self.assertIn('REF-5602', api.call_args[0][0][1]['content'])
            self.assertEqual((101, 23), b.token_estimate([]))
            self.assertEqual((0, 0), b.token_estimate([]))

    def test_missing_usage_raises(self):
        response = {'choices':[{'message':{'content':'{}'}}]}
        with patch.object(config, 'API_KEY', 'test-placeholder'), patch.object(
                backends.urllib.request, 'urlopen', return_value=io.BytesIO(json.dumps(response).encode())):
            with self.assertRaises(RuntimeError):
                backends._live_call([])

    def test_live_requires_actual_approval(self):
        fake = AttemptBackend(backends.SCRIPTS['REF-5602'])
        fake.name = 'live'
        with tempfile.TemporaryDirectory() as tmp, patch.object(config, 'AUTONOMY', 'confirm'):
            result = run_case('REF-5602', backend_override=fake, log_path=Path(tmp)/'log.jsonl')
            self.assertEqual('gate_held', result['stopped_by'])
            self.assertEqual([], result['actions'])


if __name__ == '__main__':
    unittest.main()
