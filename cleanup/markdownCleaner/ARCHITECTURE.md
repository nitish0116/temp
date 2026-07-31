# markdownCleaner architecture

## Design goals

The package requires Python 3.11 or newer. Its cleanup policy is intentionally
conservative:

- preserve source order and Markdown structure;
- separate deterministic cleanup from evidence-based correction;
- make every accepted edit auditable;
- keep review-only findings distinct from silent mutation;
- roll back an entire stage when it fails;
- resolve configuration paths predictably; and
- keep CLI orchestration separate from text-processing policy.

## Package map

| Area | Responsibility |
|---|---|
| `__main__.py`, `cli.py` | Canonical command entry point and compatibility facade |
| `commands/` | Argument parsing, review actions, batch execution, and aggregate reports |
| `pipeline.py` | End-to-end stage orchestration, backup, logging, and export |
| `modules/core/` | Configuration, context, logging, stage lifecycle, and processor contracts |
| `modules/markdown/` | Typed block model, parser, reconstruction, and segment metadata |
| `modules/cleanup/` | Whole-document cleanup, picture OCR, legacy markup cleanup, and TTS validation |
| `modules/unicode/` | Unicode normalization processors |
| `modules/regex/` | Deterministic OCR rules and processors |
| `modules/symspell/` | Dictionary loading, word-boundary evidence, spelling, and vocabulary review |
| `modules/report/` | Backups, change records, per-file summaries, and artifact export |

The old `runner.py`, `NovelCleanupStage`, and package-specific processor base
classes remain thin compatibility surfaces. New behavior belongs in the
canonical command, core lifecycle, or focused policy modules.

## Execution flow

```text
source Markdown
    |
    +-- timestamped backup
    |
    +-- ProcessingContext + MarkdownDocument
            |
            +-- DocumentCleanup       whole-document mutation
            +-- Unicode              segment processors
            +-- RegexOCR             segment processors
            +-- VocabularyCandidates report only
            +-- SymSpell             word merges + spelling
            +-- TTSValidation        report only
            |
            `-- ReportExporter       Markdown + enabled reports
```

`OCRPipeline.stage_types` is the single ordered stage registry. A pipeline
instance rebuilds its context and stage objects for every `run()`.

## Document and context model

`MarkdownParser` converts source text into ordered `MarkdownBlock` objects.
Narrative paragraphs are editable; headings, fenced or indented code, HTML,
tables, lists, block quotes, standalone links and images, footnotes, front
matter, and horizontal rules are protected from segment processors.

Whole-document cleanup uses collision-free placeholders to keep literal blocks
and inline Markdown exact while it reconstructs prose and applies structural
policy. It may intentionally normalize headings or remove configured front
matter, footnotes, glossary notes, metadata, and excluded sections. A literal
block retained by that policy is restored exactly; one inside an intentionally
removed region is removed with the region.

Within editable prose, shared protected-span traversal excludes inline code,
inline HTML, autolinks, reference identifiers, and link destinations. Link
destination scanning supports balanced parentheses. Visible inline-link labels
and full reference-link labels remain editable; collapsed and shortcut
reference identifiers remain protected.

The parser must round-trip the exact presence or absence of a terminal newline.
An unclosed construct must not consume the rest of a document: unclosed YAML is
treated as a horizontal rule plus prose, and unclosed HTML is bounded
conservatively.

`ProcessingContext` owns:

- the original and current Markdown strings;
- the parsed document and editable segments;
- the ordered `ChangeLog`;
- statistics and report metadata; and
- source/output paths.

Segment processors mutate `segment.current_text`. Whole-document stages call
`context.replace_markdown(text)`, which reparses and rebuilds segments.
`context.update_markdown()` is the synchronization boundary from segments back
to the document.

## Atomic stage lifecycle

Enabled stages run through the transactional portion of
`PipelineStage.execute()`:

1. Commit prior segment state and capture a context checkpoint.
2. Initialize context-dependent resources.
3. Run the stage.
4. On success, synchronize segment edits into current Markdown.
5. On an unsuccessful result or exception, restore Markdown, audit records,
   statistics, and metadata from the checkpoint.
6. Record timestamps and the committed stage count.

A disabled stage returns a zero-change result before checkpointing and does
not add lifecycle timestamps or a context statistic.

A failed result always reports zero committed changes. The pipeline may
continue to later stages, but it never exports a failed stage's partial state.

`SegmentProcessingStage` centralizes ordered processor construction, traversal,
protected inline spans, empty-segment policy, and audit-count calculation for
Unicode, regex, and legacy cleanup stages.

## Audit model

Every `ChangeRecord` has:

- stage;
- block, segment, and working-document line location;
- before and after text;
- confidence and reason;
- UTC timestamp; and
- optional `broken_word`.

Processors should call `record_change()` only for material edits. Report-only
stages add records directly because their `before` and `after` values are
intentionally equal. Consequently, `StageResult.changes` means committed audit
records; it does not always mean mutated text. Locations are captured from the
working document at the time a stage records the event and must not be
interpreted as immutable original-source coordinates. Document-level records
may use zero for block, segment, or line fields, and one document-level record
may summarize multiple applications of the same transformation.

Broken-word corrections must retain the exact source fragments in
`broken_word`, including internal spaces or tabs. This field is the compact
answer to what was joined; `before` and `after` retain surrounding context.

## Word-boundary strategy

Word cleanup is split into two layers:

1. `RegexOCR` handles a bounded set of deterministic patterns.
2. `SymSpell` evaluates ambiguous joins with dictionary, custom glossary, and
   `wordfreq` evidence.

The SymSpell package separates:

- immutable settings (`settings.py`);
- shared token definitions (`tokens.py`);
- dictionary and reviewed vocabulary persistence;
- the delete-index lookup engine;
- candidate scoring and spelling policy;
- typed merge evidence and merge traversal; and
- report-only vocabulary discovery/classification.

`BrokenWordEvaluator` makes one decision for a candidate pair.
`BrokenWordMerger` finds candidates, resolves overlaps, preserves exact
whitespace evidence, and applies accepted decisions. Cross-block merges are
allowed only between adjacent editable paragraphs with a safe boundary.

Custom multiword entries are tokenized as well as stored as phrases. For
example, approving `Arthur Leywin` protects `Arthur`, `Leywin`, and the complete
phrase, allowing `Ley win` to be recognized without a title-specific regex.

## Configuration and paths

`PipelineConfig` provides dot-separated reads and typed settings classes parse
subsystem-specific values once. Relative paths are resolved against the
configuration file's directory. This applies consistently to output, backup,
logging, dictionaries, and reviewed-word files. A bare `logging.file` filename
is deliberately placed beneath the resolved `logging.directory`.
Boolean settings are strict YAML booleans; string lookalikes are rejected.

That rule applies to values read from configuration. Explicit relative CLI
paths are resolved from the caller's working directory, and a relative
`output_directory` supplied directly through the Python API follows normal
`pathlib` working-directory semantics.

Avoid adding a key to `config.yaml` without wiring it into behavior and a test.
The sample configuration is the supported surface, not a wish list.

## Reports

`ReportExporter` always writes cleaned Markdown. `ReportOptions` controls
companion report creation:

- `enabled`;
- `export_json`;
- `export_summary`;
- `include_low_confidence`; and
- `review_threshold`.

Confidence filtering creates a report view and never mutates the context's
original `ChangeLog`. Summary rendering is separate from filesystem I/O and
uses escaped table cells plus adaptive code fences for untrusted OCR text.

When reporting is enabled, vocabulary candidates are always exported and are
not controlled by `export_json`. In folder mode, `export_summary` also controls
the aggregate batch summary, while aggregate vocabulary candidates are always
written. Disabling reporting suppresses all per-file companion reports and all
aggregate reports. Low-confidence filtering applies to change records in
per-file reports and the aggregate summary, not to vocabulary candidates, the
context log, or the CLI's full change count.

Batch reports are rendered in `commands/reports.py`; the CLI facade retains
legacy helper names for callers that imported them directly. Folder execution
preserves relative source directories. Within one target directory and batch
run, generated Markdown names are de-duplicated case-insensitively with numeric
suffixes, and the unique output stem determines the per-file report directory.
The aggregate report name is restricted to a plain `.md` filename. Recursive
discovery excludes configured output and enabled-backup subtrees that are
inside the input root; either artifact root equal to the input folder is
rejected.

`ReportExporter` refuses to overwrite its source Markdown. Other stable output
paths can replace artifacts from an earlier run; filesystem contents are not
included in batch collision detection. Direct API callers reusing one output
directory must provide distinct `report_subdirectory` values to retain each
file's companion reports, and distinct `output_name` values where generated
Markdown names may collide. Each pipeline `run()` still creates a fresh
processing context and fresh stage instances.

The canonical command is `python -m markdownCleaner`. Compatibility entry
points `python -m markdownCleaner.cli`, `python -m markdownCleaner.pipeline`,
and direct execution of `markdownCleaner\runner.py` all delegate to the same
CLI. In a source checkout, the module forms require the package parent on
`sys.path` (normally by running them from `cleanup`); `runner.py` bootstraps
that parent for direct path execution.

## Adding a processor or stage

For a segment processor:

1. Subclass `SegmentProcessor` or the relevant compatibility subclass.
2. Implement `process(segment) -> bool`.
3. Change only `segment.current_text`.
4. Record the exact before/after edit and update any package statistic.
5. Add the processor to a `SegmentProcessingStage` in dependency order.
6. Add focused tests for configuration switches, protected spans, audit
   contents, and no-op behavior.

For a whole-document stage:

1. Subclass `PipelineStage`.
2. Read stage options through `get_config()`.
3. Apply pure transformations where practical.
4. Call `context.replace_markdown()` once with the final text.
5. Record every material transformation.
6. Return a `StageResult` whose count matches the records added.

For a report-only stage, do not call `replace_markdown()` or mutate segments.
Document that its count represents findings.

## Validation

The regression suite covers public compatibility helpers, parser round trips,
stage rollback, configuration-relative paths, protected Markdown spans,
processor switches, exact broken-word evidence, glossary phrases, report
filtering, and full-pipeline export.

Before merging a change, run:

```powershell
python -m pytest markdownCleaner\tests -q
python -m compileall -q markdownCleaner
git diff --check -- markdownCleaner
python -m markdownCleaner --help
```
