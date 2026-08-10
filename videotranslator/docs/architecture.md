# Video Translator Architecture

The module uses standalone stage commands connected by versioned JSON artifacts.
Executable scripts remain at the module root so commands work from a source checkout
without installing a Python package. Supporting files are grouped by purpose.

## Directory layout

```text
videotranslator/
|-- pipeline.py                 # resumable legacy orchestrator
|-- *_audio.py, transcribe.py   # audio and transcription commands
|-- force_align.py              # word-level alignment
|-- diarize_*.py                # speaker identity
|-- match_speaker_voices.py     # persistent acoustic voice matching
|-- translate_constrained.py    # duration-aware translation
|-- synthesize_constrained.py   # measured, bounded TTS
|-- prepare_speaker_references.py # automatic clean voice-cloning references
|-- synthesize_xtts.py          # optional expressive cloned-voice TTS
|-- align_active_speaker.py     # visual speaker/onset alignment
|-- qa_*.py                     # transcript, dubbing, and media gates
|-- assemble_dub.py             # native-tempo mix and export
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
