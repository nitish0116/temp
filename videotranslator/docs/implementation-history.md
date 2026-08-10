# Implementation History and Current State

This document records the functional changes made while evolving the original
video translator into an automatic multilingual dubbing pipeline.

## Implemented stages

1. Added configuration-driven orchestration, versioned JSON artifacts, schemas,
   resumable stage state, and automatic transcript/final-media QA.
2. Added automatic source-language detection and English as the default target;
   callers may choose another target language.
3. Separated vocals and accompaniment with Demucs so transcription sees clearer
   speech and final dubbing retains music/effects without the original dialogue.
4. Strengthened transcription with Whisper large-v3 and relaxed VAD.
5. Added word-level forced alignment and reconciliation against a reference
   transcript, followed by multilingual model routing and Whisper-word fallback
   for unsupported or low-confidence languages.
6. Added shared utterance-aware segmentation using acoustic pauses, multilingual
   punctuation, readability limits, word-level diarized speaker changes, and
   conservative same-speaker fragment repair.
7. Added pyannote diarization, persistent speaker identities, and acoustic voice
   matching. Pitch is only an acoustic feature and is not treated as gender.
8. Added duration-constrained NLLB translation and native TTS retries rather than
   applying abrupt post-generation tempo filters.
9. Added active-speaker/lip-motion onset alignment for multi-face scenes.
10. Added cross-stage QA for source-speech coverage, speaker reassignment, tempo,
   missing clips, and active-speaker onset offsets.
10. Added recovery of diarized speech absent from canonical cues, including a
    no-VAD large-v3 pass and fallback retention of strong-ASR words.
11. Added XTTS-v2 as an optional expressive, cross-lingual, persistent-speaker
    backend for this non-commercial project. Clean reference clips are selected
    automatically from the vocal stem.
12. Added resumable XTTS rendering so accepted clips are reused while failed
    cues can be regenerated.

## Episode 1 artifacts and findings

The original strong pipeline produced 135 source cues and 131 translated cues.
Missing-speech recovery produced 185 source cues and 173 English cues. The XTTS
run generated audio for all 173 cues, but achieving 173/173 by accepting arbitrary
native-duration overflow exposed a critical quality flaw: neighboring clips can
overlap and sound mushed.

The reviewed failure around 03:31 and 04:33 established two upstream causes:

- malformed/truncated translations can make XTTS generate pathological durations;
- a long source span containing several utterances can collapse into one short,
  inaccurate English sentence.

Therefore, `173/173 files exist` is not equivalent to `173/173 valid dialogues`.
The current full XTTS video is a diagnostic artifact, not an approved final dub.

## Invariants already enforced

- No human review state is required by the pipeline.
- Input language is detected unless explicitly supplied.
- Speaker identity persists across cues.
- Missing generated files and failed clips are blocking QA failures.
- Strong-ASR word coverage and diarized-time coverage are measured independently
  of generated clips.
- Final assembly can preserve native TTS tempo.

## Known limitations

- Routed alignment models still require corpus-level accuracy validation for each
  supported language and content domain.
- NLLB output is not yet semantically validated against source utterance count,
  punctuation, or information density.
- Very short cues can be unsuitable for expressive TTS without merging or
  retranslation.
- Native-tempo overflow can collide with a following cue.
- Reference-turn energy ranking cannot guarantee that every selected turn is
  free from leakage or overlapping speakers.
- The legacy `pipeline.py` does not orchestrate every newer quality stage.

See [future-approach.md](future-approach.md) for the corrective design and
[cli.md](cli.md) for executable stage commands.
