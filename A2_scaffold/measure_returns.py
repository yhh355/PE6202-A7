"""Count saved tool-return JSON with a fixed tokenizer; not billed API usage."""
import argparse
import json
from pathlib import Path
import statistics


def main():
    import tiktoken
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_directory', type=Path)
    args = parser.parse_args()
    encoding = tiktoken.get_encoding('cl100k_base')
    results = {}
    for version in ('v1', 'v2'):
        path = args.run_directory/version/'returned_observations.json'
        rows = json.loads(path.read_text())
        counts = [len(encoding.encode(r['returned_json'], disallowed_special=())) for r in rows]
        results[version] = {'calls': len(counts), 'mean_tokens_per_call': statistics.mean(counts) if counts else None,
                            'total_tokens': sum(counts), 'per_call': counts}
    output = {'tokenizer': 'cl100k_base', 'tiktoken_version': tiktoken.__version__,
              'scope': 'Exact local token count of returned JSON, excluding tool wrapper. Not API billing tokens.',
              'results': results}
    (args.run_directory/'return_token_comparison.json').write_text(json.dumps(output, indent=2))
    print(json.dumps(output, indent=2))


if __name__ == '__main__':
    main()
