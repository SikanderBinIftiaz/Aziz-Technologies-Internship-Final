# DocuMind AI Evaluation Report

Generated: 2026-10-10T22:17:56  
Mode: **demo** (retrieval only, no generator model)  
Embeddings: `hash`, similarity threshold 0.15  
Grounding check: lexical  
Questions: 30 (20 answerable, 10 unanswerable)

## Results

| Metric | Result | Target | Status |
|---|---|---|---|
| Retrieval hit rate (right passage in top-k) | 90.0% | >= 85% | PASS |
| Retrieval MRR | 0.900 | n/a | n/a |
| Citation accuracy | 90.0% | n/a | n/a |
| Answer contains expected facts | 85.0% | n/a | n/a |
| Grounded answers | 70.0% | >= 90% | BELOW TARGET |
| Unanswerable questions refused | 80.0% | >= 90% | BELOW TARGET |
| Median latency | 3 ms | < 6000 ms | PASS |
| p95 latency | 4 ms | n/a | n/a |
| Tokens (in / out) | 0 / 0 | n/a | n/a |
| Estimated cost | $0.0 | n/a | n/a |

## Questions that need attention

| # | Type | Question | What happened |
|---|---|---|---|
| 7 | answerable | What is the daily meal reimbursement limit when traveling? | correct passage not retrieved |
| 9 | answerable | How long does the SkyScout X2 fly on a full battery? | correct passage not retrieved |
| 23 | unanswerable | How much does the SkyScout X2 cost? | answered instead of refusing |
| 24 | unanswerable | How many employees work at Northwind? | answered instead of refusing |
