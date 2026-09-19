#!/usr/bin/env python3
"""Measure token counts for tool return payloads (D2b).

This script leaves the agent unchanged.  It temporarily wraps tools.call while
the normal harness runs, records each returned observation, and writes a JSON
file that can be compared between a v1 and v2 tool/descriptor experiment.

Examples
--------
    # One named case, with the normal number of trials for that case.
    python measure_tool_returns.py REF-5602

    # The entire evaluation set.  Check config.py first: this costs money when
    # BACKEND is "live".
    python measure_tool_returns.py --all

    # Measure a different tool and use an explicit output filename.
    python measure_tool_returns.py --all --target-tool get_clinic_slots \
        --output v2_tool_returns.json

Use exactly the same model, cases, trial policy, and target tool for the v1
and v2 runs.  The reported average is tokens in the tool's JSON return value,
not the model's completion tokens.
"""

import argparse
import json
import os
import sys

import config
import harness
import tools


def _make_counter(model_name):
    """Return (count_tokens, method_name).

    tiktoken gives a model-aware count for OpenAI models when installed.  The
    scaffold has no third-party dependency, so the fallback is deliberately
    labelled as a character/4 estimate rather than presented as an API count.
    """
    try:
        import tiktoken

        try:
            encoder = tiktoken.encoding_for_model(model_name)
        except KeyError:
            encoder = tiktoken.get_encoding("o200k_base")

        return lambda text: len(encoder.encode(text)), "tiktoken:%s" % encoder.name
    except ImportError:
        return lambda text: max(1, (len(text) + 3) // 4), "estimate:characters/4"


def _serialise(value):
    """Use a stable compact representation for comparable measurements."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=str)


def _install_capture(counter):
    """Wrap tools.call and return a mutable list of per-call measurements."""
    original_call = tools.call
    captured = []

    def measured_call(problem, name, args):
        result = original_call(problem, name, args)
        text = _serialise(result)
        captured.append({
            "tool": name,
            "args": args,
            "return_tokens": counter(text),
            "return_characters": len(text),
        })
        return result

    tools.call = measured_call
    return original_call, captured


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case_id", nargs="?", help="run one named case")
    parser.add_argument("--all", action="store_true",
                        help="run every case in the work queue")
    parser.add_argument("--target-tool", default="check_referral_criteria",
                        help="tool to summarise (default: check_referral_criteria)")
    parser.add_argument("--output", default="tool_return_measurements.json",
                        help="JSON output filename")
    args = parser.parse_args(argv)

    if args.case_id and args.all:
        parser.error("choose a case_id or --all, not both")

    if not args.case_id and not args.all:
        parser.error("name one case or pass --all; no evaluation was run")

    problem = config.PROBLEM
    if args.target_tool not in tools.REGISTRY[problem]:
        parser.error("%r is not a Problem %s tool. Available: %s" % (
            args.target_tool, problem, ", ".join(sorted(tools.REGISTRY[problem]))))

    if args.all:
        case_ids = harness.load_cases(problem)
    else:
        case_ids = [args.case_id]

    counter, method = _make_counter(config.MODEL)
    original_call, captured = _install_capture(counter)
    try:
        results, _ = harness.run_set(case_ids, problem=problem)
    finally:
        tools.call = original_call

    # Every run can make several calls.  Associate each captured call with its
    # case/trial by replaying the capture list in the order the loop ran.
    # The result file still keeps the complete raw call list for auditability.
    target = [item for item in captured if item["tool"] == args.target_tool]
    total = sum(item["return_tokens"] for item in target)

    output = {
        "config": config.summary(),
        "target_tool": args.target_tool,
        "measurement_method": method,
        "cases_requested": case_ids,
        "trial_count": len(results),
        "target_tool_calls": len(target),
        "target_tool_return_tokens_total": total,
        "target_tool_return_tokens_average": (total / len(target) if target else None),
        "all_tool_calls": captured,
        "evaluation_summary": harness.report(results),
    }

    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(output, fh, indent=2, ensure_ascii=False)

    print("\nD2(b) tool-return measurement")
    print("  target tool        %s" % args.target_tool)
    print("  target calls       %d" % len(target))
    print("  return tokens      %d total" % total)
    if target:
        print("  average per call   %.2f" % (total / len(target)))
    print("  method             %s" % method)
    print("  wrote              %s" % os.path.abspath(args.output))
    return 0


if __name__ == "__main__":
    sys.exit(main())
