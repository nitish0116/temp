# Three-sample subtitle release review

## Outcome

**Release status: blocked on semantic quality.** All three cached runs pass the
timing, layout, coverage, lineage, integrity, and export checks. They do not yet
pass a human semantic spot-check, so these SRT files must be treated as diagnostic
outputs rather than publishable subtitles.

## Review performed

On 2026-08-14, 24 evenly distributed cues were inspected from each final canonical
artifact (72 cues total). The review covered names and places, honorific-like forms,
numbers, repetition, cue transitions, empty source display continuations, and
obvious model chatter. The MP4 files contain video and audio streams only: there is
no selectable subtitle stream. Where burned English dialogue was visible, a frame
at the cue timestamp was compared with the generated English.

Structural results:

| Sample | Cues | Pipeline | Source event/time coverage | Diarized turn/time coverage |
| --- | ---: | --- | --- | --- |
| Duty First, Kiss Later | 290 | passed | 100% / 99.84% | 99.71% / 99.01% |
| Korean Episode 1 | 149 | passed | 100% / 98.30% | 100% / 98.29% |
| Linglong's Ferry Episode 24 | 169 | passed | 100% / 100% | 99.49% / 99.34% |

Verified semantic defects are stored in
`tests/fixtures/three_sample_release_review.json`. Examples include `cute` becoming
`freak`, `Seoul` becoming `Seattle`, and a Treaty of Shimonoseki line becoming an
unrelated statement containing `22,000`. The Japanese sample also contains visibly
incomplete ASR source text and implausible translations, although its sampled
opening frame showed credits rather than an English dialogue reference.

## Reusable release checks

`tests/test_three_sample_release.py` is an offline, three-sample end-to-end smoke
test. When cached outputs exist, it validates canonical schema, cue timing, QA,
pipeline status, cue counts, and SRT parity without loading a model or using the
network. The same fixture freezes the manually verified semantic defects as release
blockers until new outputs replace them and the fixture is deliberately reviewed.

Run it from the repository root:

```powershell
D:\Git\Projects\.venv\Scripts\python.exe -m pytest `
  videotranslator\tests\test_three_sample_release.py -q
```

## Environment and final workflow

- Shared virtual environment: `D:\Git\Projects\.venv`
- Shared model root: `D:\PythonCaches`
- Hugging Face cache: `D:\PythonCaches\huggingface`
- Torch cache: `D:\PythonCaches\torch`
- Required shared FFmpeg: Gyan shared build 8.1.2 (needed by TorchCodec)
- Current contextual model: `Qwen/Qwen2.5-0.5B-Instruct`

The final workflow is: run the headless pipeline, require structural QA to pass,
run the cached three-sample smoke test, perform a deterministic semantic sample,
record confirmed discrepancies in the fixture, and publish only when both
structural and semantic gates are clean. The next corrective action is to replace
the 0.5B translation model (or add a reference-aware semantic judge), refresh the
translation caches, rerun translation onward, and repeat this review. ASR must also
be revisited for samples whose source-language text is already corrupt.

No push should be made for the current subtitle outputs because this review is not
clean. Code and documentation may be committed locally for review.
