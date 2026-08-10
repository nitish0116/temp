# Video Translator Architecture

The module uses standalone stage commands connected by versioned JSON artifacts.
Like Markdown Cleaner, the module root holds its primary orchestrator while stage
executables live in a dedicated `commands` package and resources are grouped by
purpose. Commands remain directly executable from a source checkout.

## Directory layout

```text
videotranslator/
|-- pipeline.py                 # resumable legacy orchestrator
|-- __main__.py                 # python -m videotranslator entry point
|-- commands/                   # executable pipeline stages and shared helpers
|   |-- *_audio.py, transcribe.py
|   |-- force_align.py, segment_utterances.py
|   |-- diarize_*.py, match_speaker_voices.py
|   |-- translate_*.py, synthesize_*.py
|   |-- qa_*.py, assemble_dub.py
|   `-- burn_subtitles.py, mux_subtitles.py
|-- config/                     # example runtime configuration
|-- docs/                       # architecture and API reference
|-- requirements.txt            # unified pinned runtime dependencies
|-- schemas/                    # JSON handoff contracts
|-- tests/                      # deterministic unit tests
|-- models/                     # ignored local model cache
`-- outputs/                    # ignored run artifacts
```

## Quality pipeline

```text
video
  -> isolated vocals and accompaniment
  -> strong multilingual transcription
  -> word-level forced alignment and cue reconciliation
  -> utterance segmentation by pause, punctuation, readability, and speaker turn
  -> blocking subtitle readability, integrity, timing, and source-coverage QA
  -> targeted recovery of uncovered speech evidence
  -> persistent speaker diarization
  -> acoustic voice matching
  -> duration-constrained translation
  -> constrained native TTS regeneration
  -> active-speaker and lip-motion onset alignment
  -> strict cross-stage QA
  -> native-tempo assembly
  -> encoded-media QA
```

Each quality stage is independently runnable and writes both its promoted artifact
and an audit report. A failed gate exits nonzero. Ambiguous decisions are resolved
automatically by retaining the safe prior artifact; there is no manual-review state.

## Dependency boundaries

`requirements.txt` is the single pinned environment definition. Comments group
the core runtime, pyannote diarization, OpenCV vision, and XTTS-v2 expressive TTS.
XTTS-v2 model weights use the Coqui Public Model License and are appropriate here
because this project is non-commercial. Transformers remains on the tested 4.x
release because XTTS imports APIs removed in Transformers 5.x.

Large public model files live under the ignored `models/` cache. Run products live
under `outputs/<project-id>/`; source code must never depend on a particular run.

## Compatibility rule

Root command filenames are stable public entry points. Internal refactoring must
preserve their CLI arguments and JSON handoffs unless the corresponding schema,
documentation, tests, and orchestrator command are migrated together.

## Operational documentation

- [cli.md](cli.md) is the stage-by-stage command runbook.
- [implementation-history.md](implementation-history.md) records completed work,
  current artifacts, and known limitations.
- [future-approach.md](future-approach.md) defines the automatic correction and
  promotion design required before the next final render.
