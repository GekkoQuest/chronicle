# Evaluation

The public dataset was evaluated on Windows with CPython 3.11.16 on November 5,
2026. All five snapshots were ingested chronologically. The supplied scorer ran
without modification and with its optional external judge disabled.

## Results

| Measure | Supplied starter | Chronicle |
| --- | ---: | ---: |
| Weighted QA score | 0.0930 | 0.9419 |
| Stale-trap hits | 0 | 0 |
| Runtime errors | 0 | 0 |
| Numeric consistency score | 1.0000 | 1.0000 |
| p50 answer latency | 0.10 s | 0.01 s |
| p95 answer latency | 0.12 s | 0.10 s |
| Ingestion | 0.0 s | 5.7 s |

These are single-run measurements. The scorer's answer timing includes scoring
overhead. The starter retains page references during ingestion; Chronicle also
parses and indexes the corpus. Aggregate results are in [evaluation.json](evaluation.json).

35 of 40 questions received full item credit. The stale-trap, temporal-as-of,
tier-disambiguation, current-value, should-refuse, and conflict-resolution question
categories received full credit.

The default public run does not score `conflicts()` without a conflict gold file.
The reported result therefore excludes that pillar. Hidden-set accuracy and
conflict precision/recall have not been measured.

## Remaining deductions

- One certificate-list question has no required tokens or expected source URLs.
  The default scorer assigns it zero correctness. Its weight is 2 out of 64.5,
  placing the default run's theoretical ceiling at approximately 96.90%.
- Three answers contain the required facts but cite supporting pages outside the
  scorer's expected URLs: one routing-number answer and two branch-hour answers.
- One hours question requires a literal `noon` token that is absent from the
  selected schedules.

The consistency metric compares numeric sets, and many public paraphrase groups
contain a single item. The independent paraphrase tests provide additional coverage.
Where reference wording differs from the supplied source, answers retain the source's
published values and reporting years.

## Independent checks

47 tests pass on Python 3.11 and 3.12 on Windows and Linux. The fixtures use invented
documents and values. Coverage includes:

- Historical isolation, future effective dates, expiry, removed pages, and snapshot replay.
- Rate/APY distinctions, balance boundaries, vehicle years, certificate tiers, and
  introductory versus standard card APRs.
- Authoritative-source selection, equal-rank abstention, historical change records,
  and answers that respond to mutations in source values.
- Unknown products and branches, fee tables, persistence, and concurrent queries.
- Archive path validation, operation without network access, and HTTP route boundaries.

A separate check matched 169 returned citation excerpts to their identified
snapshot and URL, with no missing or non-verbatim fragments. This establishes
provenance, not a general proof of semantic entailment.

Browser checks covered current and historical answers, source expansion, conflict
filters, snapshot changes, abstention, and JSON export. Desktop and mobile layouts
were inspected, with no horizontal page overflow, browser errors, or external
requests observed.

## Reproduction

From the project directory:

```sh
python -m unittest discover -v
python scripts/audit_release.py
```

From the participant kit directory:

```sh
python harness/run_eval.py --submission /path/to/chronicle --eval eval/public_eval.jsonl --out /path/to/results
python harness/run_eval.py --submission starter --eval eval/public_eval.jsonl --out /path/to/baseline
```
