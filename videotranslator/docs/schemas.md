# JSON schema and artifact guide

Schemas use JSON Schema Draft 2020-12 and live under `videotranslator/schemas`.
`schema_version` identifies the project contract version; it is separate from the
JSON Schema draft. Producers and consumers must migrate together when a required
field or its meaning changes.

## Catalog

| Schema | Producer / consumer | Purpose |
| --- | --- | --- |
| `pipeline-config.schema.json` | `pipeline.load_config` | User orchestration settings. |
| `manifest.schema.json` | pipeline orchestrator | Resumable stages, commands, artifacts, errors. |
| `transcript.schema.json` | ASR/legacy commands | Compatibility timed transcript. |
| `canonical-timed-text.schema.json` | canonical subtitle stages | Lossless text, timing, speaker, lineage handoff. |
| `semantic-reference.schema.json` | reviewer / subtitle promotion | Optional timestamped required/forbidden semantic terms. |
| `approved-script.schema.json` | quality gate / dubbing | Automatically approved target script. |
| `dub-manifest.schema.json` | TTS / assembly | Voices and generated clip inventory. |
| `dubbing-pipeline-qa.schema.json` | cross-stage QA | Pre-assembly promotion decision. |
| `final-qa.schema.json` | final-media QA | Encoded-media status, checks, issues, warnings. |
| `speech-translation.schema.json` | speech-to-English evidence | Per-group audio-derived English, cache keys, and ASR-suspicion flags. |

## Canonical timed text

This is the preferred internal subtitle representation. SRT and ASS are exports,
not pipeline handoffs. A minimal cue is:

```json
{
  "id": "semantic-0001.display-01",
  "semantic_group_id": "semantic-0001",
  "source_cue_ids": [1],
  "start": 1.25,
  "end": 3.8,
  "source_text": "こんにちは",
  "translated_text": "Hello.",
  "speaker": "speaker-01",
  "words": [],
  "confidence": {},
  "provenance": [
    {"stage": "contextual-translation", "method": "bounded-dialogue-window"}
  ]
}
```

Top-level `stage` is `raw_asr`, `clean_transcript`, `canonical_source`, or
`translated`. `translated_text` may be null before translation. Use speaker
`unknown` instead of inventing identity. Times are seconds and must be ordered,
positive-duration, and non-overlapping before export.

`source_cue_ids`, `words`, and `provenance` preserve lineage. Stages append
provenance and must not silently replace stable IDs. `metadata` contains stage
reports and cache/model details outside the core cue contract.

## Manifest lifecycle

A stage moves from `pending` to `running`, then `completed` or `failed`. Completed
states list output artifacts. Failed states retain command, timestamps, and error.
A stage is skipped only if its state is completed and expected artifacts exist.

## Optional semantic-reference sidecar

When independently reviewed subtitle text is available, pass a sidecar to the
automatic subtitle command. Each reference locates a canonical cue by timestamp
and declares terms that must appear or must not appear:

```json
{
  "$schema": "../schemas/semantic-reference.schema.json",
  "schema_version": 1,
  "video": "episode-01.mp4",
  "references": [
    {
      "timestamp_seconds": 508.8,
      "required_terms": ["Seoul"],
      "forbidden_terms": ["Seattle"]
    }
  ]
}
```

A missing cue, missing required term, or present forbidden term blocks promotion
and is written to `semantic-reference-qa.json`. This is an optional regression and
release gate; it does not claim to replace general semantic evaluation for unseen
dialogue.

## Validation practice

`validate_canonical_timed_text` runs at canonical stage boundaries and enforces
invariants JSON Schema cannot conveniently express, including end-after-start,
chronological order, non-overlap, and lineage. Tests should cover both serialized
shape and behavioral invariants.

When adding a field:

1. Assign stage ownership and required/optional status.
2. Update the schema and compatibility adapter.
3. Preserve it through downstream transforms.
4. Add a round-trip or lineage test.
5. Update this guide and user configuration docs when applicable.
