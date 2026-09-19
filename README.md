# PE6201 A2 — Outpatient Referral Coordination Agent

This repository contains our PE6201 A2 implementation for **Problem B**. It is a single-agent ReAct system that coordinates outpatient referrals using local fixture data. For each referral, it can:

- book a clinically valid appointment slot;
- request a named mandatory test; or
- escalate unsafe, incomplete, duplicate, misrouted, or untrusted referrals.

`book_slot` is simulated only. It does not contact a real clinical system or consume a real appointment; it is protected by a confirmation gate and records the simulated action.

## Quick start — reproducible scripted run

Requirements: Python 3. No external packages, API key, or network connection are required.

```powershell
git clone https://github.com/yhh355/PE6202-A7.git
cd PE6202-A7\A2_scaffold
python run_eval.py
```

The committed default is `BACKEND = "scripted"` in `config.py`. The scripted backend replays deterministic model moves while the tools perform real lookups over the fixture data in `A2_reference_data/`. The command writes `results.json` in `A2_scaffold/`.

To inspect one case turn by turn, run:

```powershell
python run_eval.py REF-5703
```

`REF-5703` is a negative case containing an instruction embedded in referral free text. The correct outcome is escalation with no `book_slot` call.

## Safety and evaluation commands

Run these commands from `A2_scaffold/`:

```powershell
# D3(b): ten deterministic guardrail tests
python D3_guardrail_tests.py

# D2(c): compare the same referral under the two call groupings
python run_d2c.py sequential
python run_d2c.py parallel

# Print the exact prompt assembled from the routing rules and tool descriptors
python run_eval.py --prompt
```

The D3 checklist is deliberately forced to the scripted backend, so it cannot spend API credits. It tests the step cap, token budget ceiling, action de-duplication, autonomy gate, and three hostile free-text cases.

## Live model battery

The live battery uses OpenRouter only when explicitly enabled. Do **not** commit an API key.

1. Set an environment variable for the current PowerShell session:

   ```powershell
   $env:OPENROUTER_API_KEY = "your-key-here"
   ```

2. In `A2_scaffold/config.py`, temporarily set `BACKEND = "live"`, choose `MODEL`, and set `PRICE_IN` / `PRICE_OUT` to that model's current OpenRouter prices per million tokens.
3. Run `python run_eval.py` from `A2_scaffold/`.
4. Restore `BACKEND = "scripted"` before committing.

The checked-in battery result files are at the repository root:

| Model | Trials | Code-check pass rate | Strict completion rate* |
|---|---:|---:|---:|
| `deepseek/deepseek-v3.2-exp` | 50 | 86.0% | 76.0% |
| `z-ai/glm-5.3` | 50 | 88.0% | 86.0% |
| `google/gemini-2.5-flash` | 50 | 90.0% | 68.0% |
| `openai/gpt-4o-mini` | 50 | 84.0% | 48.0% |

\*Strict completion rate additionally requires a `book` decision to include `book_slot` in its evidence trail. This excludes phantom bookings that name a slot without executing the confirmation-gated simulated write.

## Repository layout

```text
A2_reference_data/       Fixture data for Problem B
A2_scaffold/
  agent.py               Hand-rolled ReAct loop and per-run instrumentation
  tools.py               Referral tools and their six-field descriptors
  prompt.py              Routing rules, tool descriptors, and JSON contract
  guardrails.py          Step, budget, de-duplication, and autonomy controls
  backends.py            Scripted and vendor-neutral OpenRouter backends
  harness.py             Isolated evaluation runs, code checks, judgement queue
  run_eval.py            Main evaluation entry point
  D3_guardrail_tests.py  Ten-case deterministic guardrail checklist
  run_d2c.py             Sequential-versus-parallel D2(c) experiment
results_*.json           Saved live-battery outputs
```

## Guardrail design

The system applies four deterministic code-level controls:

1. A maximum-turn cap prevents unbounded tool loops.
2. A token budget ceiling stops expensive runs.
3. Identical tool calls with identical arguments are rejected.
4. `book_slot`, the only simulated write, is behind `AUTONOMY = "confirm"`.

The model can inspect records and recommend a booking, but an operator approval is required before the simulated appointment write occurs.

## Notes for marking

- Each evaluation run begins with fresh state; no appointment, tool history, or guardrail state is inherited.
- The harness performs deterministic checks on decision, escalation trigger, and booking clinic/date/time. It also produces a judgement queue for review of evidence-based reasoning.
- The reference-date fixture is intentionally fixed so that urgency windows and results remain reproducible.
- Add and maintain `CONTRIBUTIONS.md` before submission to record the team contribution history.
