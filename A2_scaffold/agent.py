"""
PE6201 · A2 scaffold — THE AGENT LOOP  (D1)
====================================================================
    thought -> action -> observation -> repeat -> final

That is the whole of ReAct, and it is hand-rolled here on purpose. No
framework owns your loop: when it misbehaves you need to be able to
read the twelve lines that did it.

WHAT MAKES THIS AN AGENT RATHER THAN A WORKFLOW: the number of steps is
decided by the DATA, not by you. A one-line claim with a live policy is
a short run. A four-line claim with a pre-authorisation to chase is a
long one. You did not write that branch - the record did.

--------------------------------------------------------------------
INSTRUMENTATION IS NOT OPTIONAL

Every run records turns, tokens, cost, every tool call and every
guardrail event. D6's cost model and D7's loop failure both need
numbers that were captured WHILE THE RUN HAPPENED. A team that adds
instrumentation afterwards has to run the whole battery again.

You cannot report a failure you had no way of noticing.
====================================================================
"""
import time
import json
import uuid
from pathlib import Path

import config
import prompt
import tools
from backends import make_backend
from guardrails import Guardrails, GuardrailStop
from booking_safety import BookingSafety
from d2b_contracts import observation, descriptors


def run_case(case_id, problem=None, approve=None, verbose=False, *, version='v2',
             backend_override=None, log_path=None):
    """Run ONE case from a clean state and return the decision record.

    ISOLATION (D4): everything this function needs is created inside it.
    No case may depend on a previous one having run - so no module-level
    counters, no shared guardrail object, no leftover transcript.
    """
    problem = problem or config.PROBLEM
    started = time.time()

    guards = Guardrails(config.MAX_TURNS, config.MAX_TOKENS_PER_RUN,
                        config.AUTONOMY)
    # WHAT THE MODEL IS TOLD. On the scripted backend these are ignored -
    # the moves are pre-written, so no prompt is ever sent. On the live
    # backend this IS the experiment D2(b) measures: the descriptors and
    # the routing rules, assembled by prompt.build_system_prompt().
    #     python3 run_eval.py --prompt      to see the exact text
    contracts = descriptors(tools.DESCRIPTORS, version) if problem == 'B' else tools.DESCRIPTORS
    backend = backend_override or make_backend(
        case_id,
        tool_descriptors=[contracts[n] for n in tools.REGISTRY[problem]
                          if n in tools.DESCRIPTORS],
        system_prompt=prompt.build_system_prompt(problem, version))

    transcript = []      # what the model would see
    evidence = []        # every tool actually called, in order
    trace = []
    usage_records = []
    safety = BookingSafety(case_id, guards, log_path or
        Path(__file__).parent / '.a2_runs' / uuid.uuid4().hex / 'decisions.jsonl') if problem == 'B' else None

    # TURNS ARE TOOL-CALLING TURNS. The concluding move - where the agent
    # writes its decision record - is bookkeeping, not a turn. This is the
    # same convention Appendix A uses: CLM-8842 is "turns": 4 with EIGHT
    # tool calls, because the gated action is a turn like any other and
    # the write-up afterwards is not. Count them any other way and your
    # D2(c) arithmetic stops agreeing with the brief.
    turns = 0
    iterations = 0       # loop-safety only; never reported
    tokens_in = tokens_out = 0
    stopped_by = None

    # On the scripted backend the gate auto-approves so the run stays
    # deterministic. The RECORD still shows the gate was reached and
    # passed, which is what a marker looks for.
    if approve is None and backend.name == 'scripted':
        approve = lambda action, payload: True

    try:
        while True:
            iterations += 1
            if iterations > config.MAX_TURNS + 2:
                raise GuardrailStop("step_cap", "loop did not terminate")
            # Reserve the last available response for a conclusion. Any extra
            # requested tools are refused below before they execute.
            if tokens_in + tokens_out >= config.MAX_TOKENS_PER_RUN:
                raise GuardrailStop('budget_ceiling', 'no budget remains for another model request')

            move = backend.next_move(transcript)
            ti, to = backend.token_estimate(transcript)
            tokens_in, tokens_out = tokens_in + ti, tokens_out + to
            usage_records.append({'input_tokens': ti, 'output_tokens': to,
                                  'source': 'api_usage' if backend.name == 'live' else 'scripted_estimate'})
            guards.check_budget(tokens_in + tokens_out)
            if not isinstance(move, dict):
                raise ValueError('model move must be an object')

            if verbose:
                label = ("conclude" if "final" in move else "turn %d" % (turns + 1))
                print("  %-9s · %s" % (label, move.get("thought", "")[:88]))

            # ---- conclude -------------------------------------------
            if "final" in move:
                if not isinstance(move['final'], dict) or move['final'].get('decision') not in (
                        'book', 'request_information', 'escalate', 'approve_in_principle', 'request_document'):
                    raise ValueError('invalid final decision')
                record = dict(move["final"])
                if safety:
                    safety.validate_final(record)
                break

            # ---- act: one turn may carry SEVERAL calls ---------------
            turns += 1
            guards.check_turns(turns)

            # Only calls INDEPENDENT of each other belong in one turn.
            # A dependency chain cannot be shortened by running things at
            # once - that is why Problem B saves less than Problem A.
            calls = move.get("calls") or [(move["tool"], move["args"])]
            if not isinstance(calls, list) or not calls or len(calls) > 16:
                raise ValueError('calls must be a nonempty list of at most 16 tool calls')
            observations = []

            for name, args in calls:
                if not isinstance(name, str) or not isinstance(args, dict):
                    raise ValueError('each call needs a tool name and argument object')
                guards.check_duplicate(name, args)

                # THE GATE goes in front of the irreversible step only.
                if name == tools.GATED_ACTION.get(problem):
                    if safety:
                        safety.validate(args, trace)
                    if not guards.gate(name, args, approve):
                        raise GuardrailStop(
                            "gate_held",    
                            "%s awaits human approval (autonomy=%s)"
                            % (name, config.AUTONOMY))

                result = tools.call(problem, name, args)
                if problem == 'B':
                    result = observation(name, result, version)
                if safety and name == 'book_slot':
                    safety.record(args, trace, config.AUTONOMY, turns)
                evidence.append(name)
                observations.append({"tool": name, "args": args,
                                     "observation": result})
                if verbose:
                    print("       %-26s -> %s" % (name, _short(result)))

            # Full action arguments must be visible on the next model turn.
            trace.extend(observations)
            transcript.append({"role": "assistant",
                               "content": json.dumps(move, ensure_ascii=False)})
            transcript.append({"role": "user",
                               "content": json.dumps({'tool_observations': observations}, ensure_ascii=False)})

    except GuardrailStop as stop:
        # A LOUD STOP. The record says what halted the run and where, so
        # this never looks like a quiet wrong answer.
        stopped_by = stop.reason
        record = {"decision": "escalate",
                  "reason": "halted by the %s guardrail - %s"
                            % (stop.reason, stop.detail)}
    except (ValueError, KeyError, TypeError) as error:
        stopped_by = 'invalid_tool_or_output'
        guards._fire(stopped_by, str(error))
        record = {'decision': 'escalate', 'reason': str(error)}

    cost = (tokens_in / 1e6) * config.PRICE_IN + (tokens_out / 1e6) * config.PRICE_OUT

    record.update({
        "case_id": case_id,
        "evidence": evidence,
        "turns": turns,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost_usd": round(cost, 6),
        "seconds": round(time.time() - started, 3),
        "guardrails_fired": guards.fired,
        "stopped_by": stopped_by,
        "backend": backend.name,
        "prompt_version": version,
        "autonomy": config.AUTONOMY,
        "trace": trace,
        "usage_records": usage_records,
        "measurement_kind": 'api_usage' if backend.name == 'live' else 'scripted_estimate',
        "actions": safety.actions if safety else [],
        "action_log": str(safety.log_path) if safety and safety.actions else None,
    })
    return record


def _short(value, n=64):
    s = repr(value)
    return s if len(s) <= n else s[:n - 1] + "…"
