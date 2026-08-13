# Future Automatic Quality Approach

The next iteration must improve correctness without adding user review. A full
dub may be assembled only when every source speech region has a semantically
valid, non-overlapping target cue and an accepted synthesized clip.

## 1. Multilingual forced-alignment routing (implemented baseline)

Detect the source language once, map it to a tested alignment model, and record
the selected model and fallback in the report. Cover English, French, German,
Spanish, Hindi, Japanese, Chinese, Arabic, Korean, and an explicit multilingual
fallback. Unsupported or low-confidence alignment triggers Whisper word
timestamps rather than silently using the Korean model. Model routing and fallback
reporting are implemented; corpus-level accuracy validation remains future work.

## 2. Utterance-aware source segmentation (implemented baseline)

Split long ASR spans using word pauses, punctuation, diarization changes, and
speaker turns. Merge only fragments that are too short to stand alone and share
the same persistent speaker. Never merge across a speaker boundary.

Pause, punctuation, readability, speaker-turn splitting, and conservative repair
of incomplete/ultra-short fragments are implemented. A learned multilingual
semantic-completeness score remains a possible later enhancement; the current
rules deliberately prefer leaving an uncertain cue separate over a bad merge.

## 3. Canonical timed text and context-aware translation

Maintain raw ASR, a clean semantic transcript, and display-oriented timed cues as
separate versioned representations. Stable IDs must map every target cue back to
its source words, semantic group, timing, speaker, confidence, and provenance.
SRT and ASS are exports from this canonical data rather than internal hand-offs.

Translate coherent semantic groups with a bounded window of surrounding dialogue,
not each display cue independently. Translate directly from the source language
to the target when the model supports the pair, then map target-language text back
onto suitable display windows using pauses, speaker turns, duration, line length,
and reading speed. Preserve all metadata through translation and repair.

## 4. Translation integrity gate

Before TTS, score every translation using:

- source/target information-density and duration ratios;
- terminal punctuation and malformed-ending checks;
- source utterance count versus target sentence count;
- round-trip or multilingual semantic similarity;
- named-entity and number preservation;
- repeated-token and hallucination detection.

Failed cues are automatically retried with an explicit duration and semantic
prompt/model. If retry still fails, re-segment the source and translate its
sub-utterances independently. QA must reject a cue such as a 6.5-second,
multi-utterance source becoming one unrelated 1.5-second target sentence.

## 5. Duration-constrained expressive synthesis

Estimate each persistent XTTS speaker's natural speaking rate from accepted
probes. Translate to that speaker-specific budget. Permit a small bounded native
rate adjustment, but never use arbitrary tolerance to declare a large overflow
valid. Regenerate, shorten semantically, or re-segment instead.

## 6. Non-overlap scheduler

For every generated clip, calculate the hard interval ending at the next cue or
speaker turn. A clip that exceeds it is rejected before assembly. Small tails may
use genuine silence; spoken samples may not overlap another dialogue unless the
source itself contains verified simultaneous speakers represented as separate
tracks.

## 7. Better voice references

Rank reference candidates with speaker-embedding consistency, vocal leakage,
signal-to-noise ratio, clipping, and overlap checks—not RMS alone. Cache several
clean references per speaker and reject an internally inconsistent reference set.

## 8. Full automatic QA and promotion

Promotion requires all of the following:

- source ASR-word and diarized-speech coverage thresholds pass;
- every source utterance maps to target text;
- translation-integrity checks pass;
- every persistent speaker keeps one cloned identity;
- all clips exist, pass duration bounds, and do not overlap illegally;
- active-speaker onset offsets remain within threshold;
- final audio/video duration, streams, loudness, and leakage checks pass.

If any check fails, the pipeline must retain reports and intermediate artifacts,
exit nonzero, and avoid labeling the video final.

## 9. Orchestrator migration

Extend `pipeline.py` to include separation, strong transcription, alignment-model
routing, diarization, recovery, translation integrity, XTTS references/synthesis,
active-speaker alignment, strict QA, and final assembly. Each stage should retain
the current standalone CLI for debugging and reproducibility.
