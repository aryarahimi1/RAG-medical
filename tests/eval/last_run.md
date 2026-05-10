# RAG eval — 2026-05-10 17:19

Mode: **with-generation**
Pass rate: **13/18** (72%)

## Per-category

| Category | Passed | Total |
|----------|--------|-------|
| indication | 1 | 3 |
| interaction | 2 | 4 |
| multi-drug | 1 | 1 |
| pharmacokinetics | 2 | 3 |
| recall | 1 | 1 |
| refusal | 3 | 3 |
| warning | 3 | 3 |

## Latency (ms, median / p95)

| Stage    | median | p95  |
|----------|--------|------|
| redact | 13 | 20 |
| detect | 184 | 381 |
| retrieve | 88 | 105 |
| rerank | 126 | 175 |
| generate | 7257 | 13101 |

## Failures

| id | category | check | detail |
|----|----------|-------|--------|
| pk-ibuprofen-half-life | pharmacokinetics | citations | no [n] citation marker in answer |
| ddi-ibuprofen-lisinopril | interaction | retrieval_cover | no reranked chunks for: ['ibuprofen']; got: ['lisinopril'] |
| ddi-warfarin-aspirin | interaction | retrieval_cover | no reranked chunks for: ['aspirin']; got: ['warfarin'] |
| indication-atorvastatin | indication | phrases | missing phrases: ['cholesterol'] |
| indication-sertraline | indication | phrases | missing phrases: ['depression'] |

## Per-question (full)

| id | category | drug_detect | retrieval_cover | citations | idk | phrases | passed |
|----|----------|-------------|------------------|-----------|-----|---------|--------|
| pk-ibuprofen-half-life | pharmacokinetics | pass | pass | FAIL | — | pass | NO |
| pk-warfarin-half-life | pharmacokinetics | pass | pass | pass | — | pass | yes |
| pk-levothyroxine-duration | pharmacokinetics | pass | pass | pass | — | pass | yes |
| ddi-ibuprofen-lisinopril | interaction | pass | FAIL | pass | — | — | NO |
| ddi-warfarin-aspirin | interaction | pass | FAIL | pass | — | pass | NO |
| ddi-warfarin-amoxicillin | interaction | pass | pass | pass | — | — | yes |
| ddi-metformin-ciprofloxacin | interaction | pass | pass | pass | — | — | yes |
| indication-metformin | indication | pass | pass | pass | — | pass | yes |
| indication-atorvastatin | indication | pass | pass | pass | — | FAIL | NO |
| indication-sertraline | indication | pass | pass | pass | — | FAIL | NO |
| warning-prednisone | warning | pass | pass | pass | — | — | yes |
| warning-gabapentin | warning | pass | pass | pass | — | — | yes |
| warning-omeprazole | warning | pass | pass | pass | — | — | yes |
| refusal-price | refusal | pass | — | — | pass | — | yes |
| refusal-veterinary | refusal | pass | — | — | pass | — | yes |
| refusal-manufacturer | refusal | pass | — | — | pass | — | yes |
| multi-ibuprofen-naproxen | multi-drug | pass | pass | pass | — | — | yes |
| recall-ranitidine | recall | pass | pass | pass | — | — | yes |
