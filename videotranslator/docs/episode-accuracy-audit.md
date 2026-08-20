# Episode translation accuracy audit

## Statistical target

The accepted population contains 348 groups across three episodes. The audit
targets a one-sided statement that material translation accuracy is at least
95% with 95% confidence, conditional on observing zero material errors. The
conservative binomial calculation requires 59 independently selected reviews:

```text
ceil(log(1 - 0.95) / log(0.95)) = 59
```

This is a lower-bound reliability claim, not proof of 100% accuracy.

## Precommitted selection

The sample is proportionally allocated and pseudorandomly ranked by SHA-256 of a
published seed plus semantic group ID. Publishing the seed before decisions
prevents manual cherry-picking.

| Episode stratum | Accepted population | Audit sample |
| --- | ---: | ---: |
| Duty First, Kiss Later (Japanese) | 186 | 32 |
| Korean Episode 1 | 96 | 16 |
| Linglong's Ferry Episode 24 (Mandarin) | 66 | 11 |
| **Total** | **348** | **59** |

Seed namespace: `step27-reliability-95-95-v1/<sample-id>`.

The ignored workspace-local artifacts are under
`videotranslator/outputs/step27-reliability-audit`. They contain 59 review items
and 59 padded mono WAV clips. Every clip was independently checked with FFprobe
and matched its manifest SHA-256; no missing, unreadable, or mismatched artifact
was found.

## Current result

Accuracy is not yet measured because all 59 semantic decisions are pending.
Machine agreement cannot serve as its own ground truth. A defensible published
result requires bilingual source/English review, or must be labelled explicitly
as an independent-model estimate rather than proven accuracy.

If all 59 decisions contain no material error, the planned claim is: “at least
95% material translation accuracy at 95% one-sided confidence on the accepted
348-group population.” Any observed material error requires reporting the
measured error count and recomputing the confidence bound; the zero-error claim
must not be used.
