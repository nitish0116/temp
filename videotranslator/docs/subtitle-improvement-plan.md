# Subtitle quality improvement plan

## Purpose

This document preserves the findings from the first full subtitle run and the
ordered plan for improving subtitle quality. Work should proceed one verified
step at a time. Existing transcription, alignment, diarization, and translation
artifacts should be reused whenever possible so that expensive stages are not
repeated without evidence that they are the source of a failure.

The workflow must remain deterministic and suitable for headless execution. A
failed quality gate must retain its best candidate and diagnostics; it must not
silently promote a rejected subtitle or require a coding assistant to decide how
to continue.

## Regression baseline

Source video:

`sample Data/Duty First, Kiss Later (2026) Episode 1 English Subbed - MyAsiantv.mp4`

Baseline report:

`outputs/duty-first-kiss-later-episode-1-subtitles/subtitle-pipeline-report.json`

The automatic workflow rejected all three recovery profiles and selected attempt
2, the balanced profile, as the strongest candidate.

| Metric | Baseline | Required |
| --- | ---: | ---: |
| Subtitle cues | 443 | N/A |
| Source event coverage | 99.72% | 98% |
| Source time coverage | 91.32% | 95% |
| Diarized turn coverage | 89.61% | 90% |
| Diarized time coverage | 91.29% | 90% |
| Cues shorter than 0.5 seconds | 19 | 0 |
| Cues above 20 characters/second | 25 | 0 |
| Cues longer than 12 seconds | 1 | 0 |
| Longest cue | 35.756 seconds | 12 seconds maximum |
| Maximum reading speed | 73.17 characters/second | 20 maximum |

The event coverage shows that transcription found almost all speech events. The
main problems are subtitle timing reconstruction, readability, and preservation
of the full source speech envelope. The first improvements therefore belong in
repair and reconciliation, not in a more expensive full transcription.

## Target pipeline shape

The long-term processing order is:

```text
Speech detection
  -> word-level transcription
  -> speaker assignment
  -> sentence reconstruction
  -> translation
  -> subtitle segmentation
  -> timing optimization
  -> independent QA
```

Whisper segments are evidence for speech and word timing; they should not be
treated as final subtitle boundaries.

## Ordered implementation plan

### 1. Freeze the baseline

- Retain the selected attempt, QA report, and pipeline report as regression data.
- Add a compact fixture containing representative long, short, fast, and
  unmatched cues rather than committing the complete copyrighted episode.
- Record the baseline values above in automated tests or test metadata.

Exit condition: tests can demonstrate the existing failures before repair logic
is changed.

### 2. Add timing regression tests

Add focused tests for:

- splitting a cue longer than 12 seconds;
- merging or extending a cue shorter than 0.5 seconds;
- reducing a cue above 20 characters per second;
- preventing overlaps and negative durations;
- preserving chronological order and text content;
- preserving or improving source and diarization coverage;
- terminating iterative repair even when no valid improvement exists.

Exit condition: the new tests fail for the intended reasons and do not require
models, network access, or a GPU.

### 3. Split excessively long cues

Implement deterministic splitting in `commands/repair_subtitles.py` using the
best available boundary evidence in this order:

1. word timestamps and sufficiently large pauses;
2. sentence-ending punctuation;
3. clause punctuation;
4. balanced word or character boundaries as a final fallback.

Every produced cue must remain within the parent cue's time envelope, retain all
text in order, avoid overlap, and stay below the configured duration and line
length limits.

Exit condition: the 35.756-second baseline cue is divided into readable cues and
no new timing violations are introduced.

### 4. Repair very short cues

For cues shorter than 0.5 seconds:

1. extend into adjacent silence when sufficient room exists;
2. otherwise merge with a neighboring cue when speaker, gap, duration, reading
   speed, and line-length constraints remain valid;
3. prefer the neighbor from the same source segment and speaker;
4. retain the cue and report an unresolved issue if neither operation is safe.

Do not move a cue across another cue, consume known speech assigned elsewhere, or
change dialogue order.

Exit condition: all safely repairable short cues pass while irreparable cases
remain explicitly reported.

### 5. Make reading-speed repair translation-aware

For translated text above the configured characters-per-second limit:

1. borrow unused silence before or after the cue;
2. split the translation at grammatical boundaries and distribute the available
   speech envelope;
3. rebalance adjacent cues when their combined envelope permits it;
4. optionally request constrained translation only when timing operations cannot
   produce a readable result.

Automatic text deletion or arbitrary QA-threshold relaxation is not permitted.

Exit condition: repaired cues meet the reading-speed target without lost text,
overlap, or timing drift.

### 6. Preserve source speech envelopes

Improve missing-speech recovery so recovered text retains the complete supported
speech region rather than shrinking timing to only high-confidence recognized
words. Use VAD, aligned word timing, neighboring source events, and diarization
turn boundaries as independent evidence.

Aggressive recovery should run only where VAD and/or diarization support speech.
Low-confidence repetition, hallucination, music, and isolated noise must remain
rejectable.

Exit condition: source time coverage reaches at least 95% without materially
reducing event precision or producing overlapping cues.

### 7. Reconcile unmatched diarization turns

Attach a small unmatched speaker turn to a nearby cue only when:

- the temporal gap is below a configured bound;
- no conflicting subtitle occupies the interval;
- the speaker is compatible with the neighboring cue; and
- independent speech evidence supports the turn.

Exit condition: diarized turn coverage reaches at least 90% while diarized time
coverage remains at or above 90%.

### 8. Add bounded iterative repair

Run the split, extend, merge, and reading-speed operations in a stable order.
Repeat only while the objective quality score improves, with a small configured
maximum number of passes. Record every mutation with the rule, cue identifiers,
old timing, and new timing.

Exit condition: repeated execution is idempotent, convergence is bounded, and
the repair provenance explains every changed cue.

### 9. Reprocess existing artifacts

Run only repair and QA against the selected balanced attempt first. Do not rerun
audio extraction, transcription, alignment, diarization, recovery, or translation
unless the resulting metrics show that an upstream artifact prevents the target
from being reached.

Exit condition: a new report compares the candidate against the frozen baseline.

### 10. Escalate upstream only with evidence

If repair alone cannot meet coverage gates:

- run the improved recovery/reconciliation stages using existing audio and
  transcription;
- rerun translation only for changed source cues;
- rerun full transcription only if missing speech is confirmed in the canonical
  transcript itself.

Exit condition: every expensive rerun has a recorded metric-based reason.

### 11. Finalize documentation and headless operation

Document command examples, configuration values, QA thresholds, fallback order,
artifact reuse, exit codes, and failure reports. Tests and execution must not
depend on interactive input or coding-assistant intervention.

Exit condition: a clean headless process can either produce `final.srt` or retain
`rejected.srt` with enough diagnostics for the next scheduled run or a human
reviewer.

## Definition of done

The improvement is complete when the representative regression fixture and the
episode run satisfy all of the following without manually editing subtitles:

- no overlaps or invalid timestamps;
- no cue shorter than 0.5 seconds unless marked objectively irreparable;
- no cue longer than 12 seconds;
- no cue above 20 characters per second;
- source event coverage at least 98%;
- source time coverage at least 95%;
- diarized turn coverage at least 90%;
- diarized time coverage at least 90%;
- every fallback and repair appears in machine-readable provenance;
- a rejected result is never named or promoted as `final.srt`.

## Implementation discipline

- Complete and verify one numbered step before beginning the next.
- Prefer deterministic local transformations over another model invocation.
- Do not weaken a QA threshold merely to make a run pass.
- Preserve existing artifacts until their replacements have passed QA.
- Keep credentials in environment variables; never write tokens into reports,
  configuration, logs, tests, or source control.
- Keep model/device fallbacks bounded and record the selected configuration.

