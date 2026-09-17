"""D3: validate simulated bookings against records, not model claims.

This is a local teaching fixture system, not a clinical decision system.
The English hostile-text patterns cover tested attacks, not every injection.
"""
import json
import re
from datetime import date, timedelta
from pathlib import Path

import tools
from guardrails import GuardrailStop


def hostile_text(text):
    text = ' '.join(text.casefold().split())
    patterns = (r'ignore.{0,50}(rules|instructions|protocol|checks)',
                r'(system|developer)\s*(message|override|:)',
                r'(tool|check_referral_criteria).{0,35}(output|result|:)',
                r'(operator|human|nurse).{0,30}(approved|approval granted)',
                r'(skip|bypass).{0,40}(checks|approval|rules)',
                r'book.{0,25}(regardless|without approval)')
    return any(re.search(p, text) for p in patterns)


class BookingSafety:
    def __init__(self, case_id, guards, log_path):
        self.case_id, self.guards = case_id, guards
        self.log_path = Path(log_path)
        self.actions = []

    def stop(self, reason, detail):
        self.guards._fire(reason, detail)
        raise GuardrailStop(reason, detail)

    def validate(self, args, trace):
        if args.get('referral_id') != self.case_id:
            self.stop('case_mismatch', 'cannot book a different referral')
        if self.actions:
            self.stop('duplicate_booking', 'this referral already has a booking in this isolated run')
        ref = tools.get_referral(self.case_id)
        if not ref:
            self.stop('invalid_record', 'referral does not exist')
        if hostile_text(ref.get('clinical_summary', '')):
            self.stop('hostile_text', 'request text cannot issue tool commands or authorize a write')
        required = [
            ('get_referral', {'referral_id': self.case_id}),
            ('check_referral_criteria', {'specialty': ref['specialty'], 'referral_id': self.case_id}),
            ('lookup_patient', {'patient_id': ref['patient_id']}),
        ]
        for name, expected in required:
            if not any(t['tool'] == name and t['args'] == expected for t in trace):
                self.stop('missing_evidence', 'required completed lookup: ' + name)
        criteria = tools.check_referral_criteria(ref['specialty'], self.case_id)
        if not criteria or criteria['red_flag_term'] or not criteria['right_department'] or criteria['missing_tests']:
            self.stop('unsafe_booking', 'red flag, wrong department or mandatory tests missing')
        person = tools.lookup_patient(ref['patient_id'])
        if not person:
            self.stop('invalid_record', 'patient does not exist')
        clock = date.fromisoformat(tools.as_of())
        if any(a['specialty'] == ref['specialty'] and date.fromisoformat(a['date']) > clock
               for a in person['patient'].get('existing_appointments', [])):
            self.stop('unsafe_booking', 'future same-specialty appointment exists')
        hi = clock + timedelta(weeks=criteria['window_weeks'])
        legal = tools.get_clinic_slots(ref['specialty'], criteria['band'],
                                      **{'from': clock.isoformat(), 'to': hi.isoformat()})
        chosen = {k: args.get(k) for k in ('clinic', 'date', 'time')}
        if not any(all(s[k] == chosen[k] for k in chosen) for s in legal):
            self.stop('illegal_slot', 'slot absent, full, wrong band/specialty or outside allowed window')
        if not any(t['tool'] == 'get_clinic_slots' and isinstance(t['observation'], list)
                   and any(all(s.get(k) == chosen[k] for k in chosen) for s in t['observation'])
                   for t in trace):
            self.stop('missing_evidence', 'selected slot was not returned by a completed query')

    def record(self, args, trace, autonomy, turn):
        event = {'case_id': self.case_id, 'decision': 'book',
                 'booked': {k: args[k] for k in ('clinic', 'date', 'time')},
                 'reason': 'Record checks and slot validation passed before the autonomy gate.',
                 'evidence': [{'tool': t['tool'], 'args': t['args']} for t in trace],
                 'autonomy': autonomy, 'gate': 'approved' if autonomy == 'confirm' else 'act',
                 'turn': turn}
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open('a', encoding='utf-8') as f:
            f.write(json.dumps(event, ensure_ascii=False) + '\n')
        self.actions.append(event)

    def validate_final(self, final):
        if final.get('decision') == 'book':
            if not self.actions or final.get('booked') != self.actions[0]['booked']:
                self.stop('unbacked_final', 'claimed booking does not match an executed gated action')
        elif self.actions:
            self.stop('inconsistent_final', 'booking already executed; final cannot conceal it')
