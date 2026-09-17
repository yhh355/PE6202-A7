"""Reproducible D2(b) runner. Offline by default; live requires explicit options."""
import argparse
import getpass
import hashlib
import json
from pathlib import Path
import statistics

import config
import harness
import prompt
from agent import run_case


def save(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding='utf-8')


def source_hashes():
    root = Path(__file__).resolve().parents[1]
    paths = list((root/'A2_scaffold').glob('*.py'))
    paths += list(Path(config.data_root()).rglob('*.json'))
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(paths)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--version', choices=['v1', 'v2', 'both'], default='both')
    parser.add_argument('--model')
    parser.add_argument('--price-in', type=float)
    parser.add_argument('--price-out', type=float)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--case', help='Optional smoke test only; omit for full battery')
    parser.add_argument('--max-cost-usd', type=float, default=0.50)
    parser.add_argument('--approve-simulated-bookings', action='store_true',
        help='Explicit operator preapproval for validated local fixture bookings only')
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Output already exists; choose a new directory to preserve evidence.')
    if args.live:
        if not args.model or args.price_in is None or args.price_out is None:
            parser.error('Live requires --model --price-in --price-out (USD per million tokens).')
        if min(args.price_in, args.price_out) < 0:
            parser.error('Prices must be nonnegative.')
        config.BACKEND, config.MODEL = 'live', args.model
        config.PRICE_IN, config.PRICE_OUT = args.price_in, args.price_out
        if not config.API_KEY:
            config.API_KEY = getpass.getpass('OpenRouter API key (hidden, never saved): ')
        if not config.API_KEY:
            parser.error('API key is required for live execution.')
    else:
        config.BACKEND = 'scripted'
    config.PROBLEM, config.AUTONOMY = 'B', 'confirm'
    args.output.mkdir(parents=True)
    if args.live:
        if args.max_cost_usd <= 0:
            parser.error('Budget must be positive')
        from live_budget import LiveBudget
        config.LIVE_BUDGET = LiveBudget(args.max_cost_usd,config.PRICE_IN,config.PRICE_OUT,args.output/'api_usage.jsonl')
    versions = ('v1', 'v2') if args.version == 'both' else (args.version,)
    cases, key = harness.load_cases(), harness.load_key()
    if args.case:
        if args.case not in cases:
            parser.error('Unknown case ID')
        cases = [args.case]
    manifest = {'base_commit': 'b3efcdf (uploaded ZIP unpacked over 92616d6)', 'backend': config.BACKEND,
        'max_cost_usd': args.max_cost_usd if args.live else None,
        'approval_policy': 'operator_preapproved_validated_simulation' if args.approve_simulated_bookings else 'per_action',
        'model': config.MODEL if args.live else None,
        'versions': versions, 'case_ids': cases, 'source_hashes': source_hashes(),
        'prices_per_million': {'input': config.PRICE_IN, 'output': config.PRICE_OUT},
        'cost_note': 'Token-price calculation, not an invoice; scripted costs are estimates.',
        'autonomy': 'confirm', 'max_turns': config.MAX_TURNS,
        'max_tokens_per_run': config.MAX_TOKENS_PER_RUN,
        'status': 'running', 'human_judgement': 'pending'}
    save(args.output/'manifest.json', manifest)
    summaries = {}
    try:
        for version in versions:
            folder = args.output/version
            folder.mkdir()
            (folder/'system_prompt.txt').write_text(prompt.build_system_prompt('B', version), encoding='utf-8')
            rows, queue, returns = [], [], []
            for cid in cases:
                if cid not in key:
                    raise ValueError('Missing expected outcome: ' + cid)
                for trial in range(1, (3 if harness._is_negative(key[cid]) else 1)+1):
                    def approve(action, payload):
                        if args.approve_simulated_bookings:
                            return action == 'book_slot'
                        print('Confirm simulated local action:', action, json.dumps(payload))
                        return input('Type yes to approve; anything else rejects: ').strip().lower() == 'yes'
                    record = run_case(cid, version=version,
                        approve=approve if args.live else None,
                        log_path=folder/'actions'/f'{cid}_{trial}.jsonl')
                    if record['action_log']:
                        record['action_log'] = str(Path(record['action_log']).relative_to(args.output))
                    passed, fails = harness.code_check(record, key[cid])
                    # A final claim alone is never evidence of a successful write.
                    if record['decision'] == 'book' and len(record['actions']) != 1:
                        fails.append('booking must have exactly one persisted action')
                    passed = passed and not fails
                    row = dict(case_id=cid, trial=trial, passed=passed, fails=fails, record=record)
                    rows.append(row)
                    with (folder/'trials.jsonl').open('a', encoding='utf-8') as f:
                        f.write(json.dumps(row, ensure_ascii=False)+'\n')
                    judge = harness.prepare_judgement_check(record, key[cid])
                    judge['trial'] = trial
                    queue.append(judge)
                    for event in record['trace']:
                        if event['tool'] == 'check_referral_criteria':
                            returns.append({'case_id': cid, 'trial': trial,
                                'returned_json': json.dumps(event['observation'], ensure_ascii=False)})
                    print(version, cid, trial, 'PASS' if passed else 'FAIL', flush=True)
            summary = {'trials': len(rows), 'code_passed': sum(r['passed'] for r in rows),
                'code_pass_rate': sum(r['passed'] for r in rows)/len(rows),
                'median_turns': statistics.median(r['record']['turns'] for r in rows),
                'tokens_in': sum(r['record']['tokens_in'] for r in rows),
                'tokens_out': sum(r['record']['tokens_out'] for r in rows),
                'cost_usd': sum(r['record']['cost_usd'] for r in rows),
                'measurement_kind': 'api_usage' if args.live else 'scripted_estimate',
                'judgement_status': 'pending; code pass is not overall pass',
                'criteria_calls': len(returns)}
            save(folder/'results.json', {'summary': summary, 'results': rows, 'judgement_queue': queue})
            save(folder/'returned_observations.json', returns)
            summaries[version] = summary
        manifest['status'] = 'completed'
    except BaseException as error:
        manifest['status'] = 'interrupted_or_failed'
        # Do not serialize exception text: provider errors might echo credentials.
        manifest['error_type'] = type(error).__name__
        manifest['note'] = 'Completed trials retained. An incomplete billed request may be unaccounted for; inspect before retrying.'
        raise
    finally:
        save(args.output/'manifest.json', manifest)
        save(args.output/'comparison.json', summaries)


if __name__ == '__main__':
    main()
