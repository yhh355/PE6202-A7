"""Official 10 scenarios, including 3 attempted hostile bookings; both versions."""
from test_guardrails import run_checks

if __name__ == '__main__':
    raise SystemExit(0 if all(row['passed'] for row in run_checks()) else 1)
