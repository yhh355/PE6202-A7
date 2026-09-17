"""Per-process ledger and conservative preflight; actual provider cost when supplied."""
import json
from pathlib import Path


class LiveBudget:
    def __init__(self, limit, price_in, price_out, path):
        self.limit, self.price_in, self.price_out = limit, price_in, price_out
        self.spent = 0.0
        self.path = Path(path)

    def before(self, messages, max_output):
        # UTF-8 bytes upper-bound ordinary byte-pair input token counts;
        # include a deliberately large allowance for message framing.
        upper_input = sum(len(m['content'].encode('utf-8'))+128 for m in messages)+512
        upper_cost = (upper_input*self.price_in+max_output*self.price_out)/1e6
        if self.spent + upper_cost > self.limit:
            raise RuntimeError('Local live budget would be exceeded; stopped before next request.')

    def record(self, payload, usage):
        estimate = (usage['prompt_tokens']*self.price_in+usage['completion_tokens']*self.price_out)/1e6
        provider_cost = usage.get('cost')
        valid_cost = isinstance(provider_cost,(int,float)) and provider_cost >= 0
        self.spent += max(estimate, provider_cost if valid_cost else 0)
        row = {'request_id':payload.get('id'), 'model':payload.get('model'),
               'prompt_tokens':usage['prompt_tokens'], 'completion_tokens':usage['completion_tokens'],
               'provider_cost_usd':provider_cost if valid_cost else None,
               'standard_price_cost_usd':estimate, 'budget_accounted_usd':self.spent}
        with self.path.open('a',encoding='utf-8') as f:
            f.write(json.dumps(row)+'\n')
