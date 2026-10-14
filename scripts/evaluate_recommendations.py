#!/usr/bin/env python3
"""Small, predeclared evidence-routing checks, not a measured accuracy study."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server  # noqa: E402

# Labels were selected from the synthetic observations and product hypothesis
# before looking at this run's output. They are review expectations, not truth.
CASES = [
    ('question', 'Q016', 'maintain', 'all three channels mention target'),
    ('question', 'Q007', 'maintain', 'all mentioned even though owned citations are zero'),
    ('question', 'Q042', 'product_review', 'two negative mobile mentions'),
    ('question', 'Q044', 'product_review', 'negative mobile security mentions'),
    ('question', 'Q053', 'recollect', 'only one valid channel'),
    ('question', 'Q027', 'improve', 'zero mentions, two confirmed owned citations; review existing page'),
    ('question', 'Q023', 'improve', 'general information question, two valid gaps'),
    ('question', 'Q056', 'improve', 'competitor comparison with two valid gaps'),
    ('question', 'Q041', 'improve', 'one gap and one owned citation, no negative mention'),
    ('question', 'Q051', 'improve', 'two valid channels and one gap'),
    ('topic', 'automation', 'maintain', 'all valid observations mention target'),
    ('topic', 'mobile', 'product_review', 'five negative mentions across topic'),
    ('topic', 'scale', 'recollect', 'coverage 9/15, below 80 percent'),
    ('topic', 'security', 'improve', 'zero mentions, six owned citations; no absence claim'),
]

EXCLUDED = [
    ('Q028', 'legal correctness of answer cannot be judged from mentions/citations'),
    ('Q001', 'whether a recommended content action would actually be adopted is unobserved'),
]


def simple_nonmention_baseline(item):
    """Naive predeclared baseline: any valid nonmention => improve, else maintain."""
    return 'improve' if item['hits'] < item['validCount'] else 'maintain'


def main():
    # Keep evaluation cache writes out of the application's persistent DB.
    with tempfile.TemporaryDirectory(prefix='vivi-recommendation-eval-') as tmp:
        server.DB = Path(tmp) / 'eval.sqlite3'
        with server.connection() as db:
            rows = []
            for kind, identifier, expected, rationale in CASES:
                item = (server.analysis(server.QUESTION_BY_ID[identifier]) if kind == 'question'
                        else server.topic_analysis(identifier, db))
                baseline = simple_nonmention_baseline(item)
                actual = item['decision']
                rows.append((kind, identifier, expected, baseline, actual, actual == expected, rationale))
    print('scope | id | expected | baseline | actual | pass | evidence basis')
    for row in rows:
        print(' | '.join(map(str, row)))
    actual_ok = sum(row[5] for row in rows)
    baseline_ok = sum(row[3] == row[2] for row in rows)
    print(f'SCORED={len(rows)} ACTUAL_PASS={actual_ok} ACTUAL_FAIL={len(rows)-actual_ok} '
          f'BASELINE_PASS={baseline_ok} BASELINE_FAIL={len(rows)-baseline_ok} EXCLUDED={len(EXCLUDED)}')
    for identifier, why in EXCLUDED:
        print(f'EXCLUDED {identifier}: {why}')
    return 0 if actual_ok == len(rows) else 1


if __name__ == '__main__':
    raise SystemExit(main())
