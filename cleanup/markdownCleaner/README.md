# markdownCleaner

`markdownCleaner` prepares OCR- or PDF-extracted novel Markdown for
text-to-speech. It preserves Markdown structure, applies conservative text
repairs, and writes audit records for processor edits, aggregate
whole-document transformations, and review findings.

The cleaner never performs a blanket whitespace join or a global `rn -> m`
replacement. Broken-word merges require deterministic rules, dictionary
evidence, corpus-frequency evidence, or an explicitly approved custom term.

## Install and run

Python 3.11 or newer is required.

Run commands from the directory that contains the `markdownCleaner` package:

```powershell
cd C:\Users\z005537p\NitishWork\HM\temp\cleanup
python -m pip install -r markdownCleaner\requirements.txt
python -m markdownCleaner "books\Volume 01.md"
```

`python -m markdownCleaner` is the canonical entry point. The refactored
package also retains these compatibility entry points:

```powershell
python -m markdownCleaner.cli --help
python -m markdownCleaner.pipeline --help
python markdownCleaner\runner.py --help
```

In a source checkout, run the module forms from `cleanup`, the directory
containing the `markdownCleaner` package. The direct `runner.py` form also
bootstraps that package parent when it is invoked by path from another working
directory.

Choose an output directory or another configuration:

```powershell
python -m markdownCleaner "books\Volume 01.md" -o cleaned
python -m markdownCleaner "books\Volume 01.md" --config my_config.yaml
```

Process every Markdown file directly in a folder:

```powershell
python -m markdownCleaner books -o cleaned
```

Folder mode cleans up to four files concurrently in separate processes by
default. Control the process count with `--file-workers 1|2|3|4`; use
`--file-workers 1` for the original sequential behavior or reduce the count
when memory is constrained.

Include subfolders and continue after individual failures:

```powershell
python -m markdownCleaner books -o cleaned --recursive --continue-on-error
```

Folder mode discovers files before processing and preserves relative
subfolders. Within each target directory in one batch run, cleaned names are
compared case-insensitively; generated-name collisions receive numeric
suffixes such as ` (2)`, and their per-file report folders derive corresponding
unique readable names. When recursion is enabled, configured output and backup
subtrees inside the input folder are excluded, so prior generated Markdown is
not reprocessed. An output or backup directory cannot equal the input folder.
Exit code `0` means success, `1` means no Markdown files were found, and `2`
means at least one file or pipeline stage failed.

## Pipeline

The stages run in this order:

1. Create a timestamped source backup when backup is enabled.
2. `PageArtifacts` detects repeated headers, footers, and page numbers. The
   default mode reports them without removal.
3. `DocumentCleanup` removes configured non-narrative material, handles
   picture OCR, converts Setext headings to ATX Markdown, normalizes recognized
   narrative headings to one `#`, reconstructs wrapped prose, strips emphasis
   when configured, and reports suspicious OCR lines.
4. `Unicode` repairs reversible mojibake and normalizes Unicode, invisible
   characters, ligatures, whitespace, and punctuation while preserving dash
   semantics.
5. `RegexOCR` applies deterministic character, broken-word, and
   repeated-character rules under the global mutation policy.
6. `VocabularyCandidates` reports repeated unknown terms for review without
   changing the document or glossary.
7. `SymSpell` validates line-break dehyphenation, merges reviewed or
   dictionary-supported OCR word splits, and applies high-confidence spelling
   corrections.
8. `ContextualRealWords` reports likely known-word substitutions without
   mutating text.
9. `TTSValidation` reports possible TTS or SSML problems without changing text.
10. The exporter writes cleaned Markdown and enabled companion reports.

Visible text in paragraphs, headings, tables, lists, block quotes, footnotes,
and link/image labels is eligible for cleanup while Markdown control syntax is
retained. Fenced/indented code, raw HTML, YAML front matter, horizontal rules,
URLs, and reference identifiers stay protected. Within editable text, inline
code, inline HTML, autolinks, reference
identifiers, and link destinations—including destinations with balanced
parentheses—remain literal. Visible labels in explicit inline links and full
reference links embedded in ordinary prose remain eligible for cleanup;
collapsed and shortcut reference identifiers remain protected.

`DocumentCleanup` may still deliberately normalize headings or remove
configured front matter, footnotes, glossary notes, metadata, and explicitly
excluded sections. Literal blocks are restored exactly as represented in the
loaded document when retained; a protected block located inside an
intentionally removed region is removed with that region. A failed stage is
rolled back before later stages run, so partial edits and partial audit records
are not exported.

Each audit record has an `applied` flag. A record may be an applied edit, a
suppressed low-confidence proposal, or a report-only finding.

## Outputs

With the default configuration, paths are resolved relative to
`markdownCleaner\config.yaml`:

```text
cleanup/
|-- backup/
|   `-- <timestamp>/
|       |-- <original file>.md
|       `-- metadata.json
|-- logs/
|   `-- pipeline.log
`-- output/
    |-- <title> - Cleaned.md
    `-- reports/
        |-- changes.json
        |-- summary.md
        `-- glossary_candidates.json
```

A folder run also writes:

```text
<output>\reports\batch_summary.md
<output>\reports\glossary_candidates.json
```

Folder runs place each file's reports beneath its preserved relative output
folder, for example
`<output>\subfolder\reports\<cleaned-stem>\summary.md`. The two paths above are
the aggregate batch reports.

Each change record contains stage and working-document location, before and
after text, confidence, reason, timestamp, `applied`, and an optional
`broken_word`.
Location fields describe the document as it existed when that stage recorded
the event; they are not immutable coordinates in the original source.
Whole-document transformations may summarize multiple edits in one line-`0`
record. OCR merge records populate `broken_word` with the exact source
fragments and intervening whitespace, such as `ener gy`.

The exporter refuses to overwrite the input Markdown itself. Stable generated
paths may replace artifacts from an earlier run; automatic numeric suffixes
prevent collisions among files discovered in the same folder run, not with
files already present on disk.

## Custom words and review

Known names and phrases belong in `data\custom_words.json`. Prefer the CLI so
input is validated and duplicates are handled case-insensitively:

```powershell
python -m markdownCleaner --approve-words "Arthur Leywin" "Jarrod Redner"
```

Multiword entries protect both the full phrase and its tokens. This lets a
source such as `Arthur Ley win` use the approved `Leywin` evidence while also
preventing the correct name from being replaced by an unrelated suggestion.
Protected tokens are honored by SymSpell and repeated-character cleanup.
Valid uppercase Roman numerals are also preserved, so a name such as
`Ivsaar III` is not reduced to `Ivsaar I`.

Other review workflows are:

```powershell
python -m markdownCleaner --learn-words "sitrep" "noncoms"
python -m markdownCleaner --reject-words "candidateToSuppress"
python -m markdownCleaner --simplify-candidates output\reports\glossary_candidates.json
```

- Approved words are book/domain terms used as protected dictionary evidence.
- Learned words are explicitly reviewed protected terms.
- Rejected words are omitted from future vocabulary-candidate reports; they do
  not become correction targets.
- Simplifying a candidate report produces only `word`, `occurrences`, and
  `suggested_correction`, leaving the master report unchanged.

The vocabulary-candidate stage never writes any of these files automatically.

Reviewed word-boundary decisions belong in
`data\broken_word_decisions.json`:

```json
{
  "accepted": {"Ley win": "Leywin", "placat ingly": "placatingly"},
  "rejected": ["to one", "no one"]
}
```

Accepted entries override lexical heuristics; rejected entries prevent a join.
Keys are whitespace- and case-insensitive. An accepted value may also be an
object with `replacement`, `blocked_previous`, and `blocked_following` lists;
the default `be cause` decision uses these fields to preserve valid phrases
such as `could be cause for concern`. Keep known-word confusion rules in
`data\contextual_word_rules.json`; those suggestions are report-only.

You do not need to edit the boundary-decision JSON by hand. Build a cached,
library-wide review from the folder containing Markdown sources:

```powershell
python -m markdownCleaner `
    --build-broken-word-review "..\Library\cleaned"
```

The defaults write `data\broken_word_review.json`,
`data\broken_word_review_ambiguous.json`, and the ignored portable cache
`data\.broken_word_review_cache.json.gz`. Cache keys are relative POSIX paths
plus content hashes, so a clone on another machine can reuse the cache; only
new or modified Markdown files are rescanned. Use
`--rebuild-broken-word-cache` to force a complete scan.

Pairs the live SymSpell evaluator already handles, and pairs already present in
the permanent decision store, are omitted. Strong corpus/lexical results go to
the main review. Automatic decisions require at least three occurrences of the
supporting form. Acceptance uses joined-form evidence; rejection requires the
spaced form to outnumber the joined form by at least three to one. Candidates
where neither form occurs at least three times are omitted and counted as
`insufficient_evidence_skipped`. Uncertain but sufficiently observed results
go to the ambiguous file with
`status: "review"`; change that status to `"accepted"` or `"rejected"` after
inspection.

For the optimized transformer workflow, import both generated files into the
non-authoritative candidate store:

```powershell
python -m markdownCleaner `
    --import-broken-word-candidates `
    "markdownCleaner\data\broken_word_review.json"

python -m markdownCleaner `
    --import-broken-word-candidates `
    "markdownCleaner\data\broken_word_review_ambiguous.json"
```

Accepted and unresolved proposals become transformer candidates. Corpus-level
rejections become prefilter suppressions, so obviously legitimate spaced forms
do not consume model inference. This import updates
`context_validator.candidate_file`; it never grants trusted-decision status.

Use `--promote-broken-word-review` only on explicitly human-reviewed files.

When `context_validator.local_files_only` is `true` and the model is missing
from local cache, `context_validator.fail_open` controls behavior:

- `true` (default): continue SymSpell without transformer context validation;
- `false`: treat missing model/cache initialization as a stage failure.
Promotion validates the complete decision schema, preserves optional context
blockers, and updates the configuration-relative
`symspell.broken_word_decisions` file. Reviewed decisions bypass the model;
ordinary cleaning never writes either store.

### Optional transformer context validation

For boundaries where a dictionary alone is unsafe—such as `log in` versus
`login`—enable the hybrid context validator. Install the optional dependencies
first:

```powershell
python -m pip install -r markdownCleaner\requirements-transformer.txt
```

On Windows, the managed setup command keeps these dependencies isolated and
prefetches the model separately from package installation:

```powershell
.\markdownCleaner\install-transformer.ps1
.\markdownCleaner\install-transformer.ps1 -Offline
```

The environment is created once as the ignored workspace-root
`ocrTransformerEnv` and refreshed only when the requirements fingerprint or
Python minor version changes. The first command installs packages and downloads
`distilbert/distilroberta-base` (Apache-2.0); the second verifies that both the
environment and weights are ready without network access. Model files use the
active workstation's `PYTHON_CACHE_HOME`, falling back to the workspace-root
`.model-cache`; `HF_HOME` and `TORCH_HOME` are honored when already configured.
Use `-SkipPrefetch` when only the environment should be prepared.

Then configure:

```yaml
context_validator:
  enabled: true
  model: "distilbert/distilroberta-base"
  candidate_file: "data/ocr_boundary_candidates.json"
  batch_size: 16
  max_length: 128
  context_characters: 600
  merge_margin: 0.35
  device: "auto"
  local_files_only: false
```

The first online run downloads and caches the selected Hugging Face model.
Set `local_files_only: true` after the model exists locally, or on machines
where network access is prohibited. `device: auto` uses CUDA when available
and otherwise uses the CPU.

The validator does not run on every word. Regex first handles deterministic OCR
patterns; SymSpell and `data\ocr_boundary_candidates.json` identify plausible
boundaries; then the model scores only those candidates. For each candidate it
creates spaced and joined local-context variants, masks each target subtoken,
and compares their mean target-token log probabilities in batches. A join is
applied only when its score beats the spaced form by `merge_margin`.

Candidate-file entries are proposals, not approvals. Its `suppressed` list
contains corpus-supported spaced boundaries that are skipped before model
scoring. Candidate proposals are inert when the validator is disabled.
Reviewed decisions and protected names bypass model scoring; model-rejected
proposals remain unchanged and are logged with the
spaced score, joined score, observed margin, required margin, and exact
`broken_word`. This keeps the model subordinate to explicit human decisions.

## Configuration

Edit `config.yaml` or pass `--config`. Relative path values stored in the
selected configuration—including output, backup, logging, dictionaries, and
reviewed-word files—are resolved from that configuration file's directory.
The one logging exception is a bare `logging.file` filename, which is placed
under the resolved `logging.directory`.
Relative paths passed explicitly as CLI arguments, such as `--output` or
`--glossary-file`, are resolved from the current shell directory.
Boolean settings must use YAML `true` or `false`; quoted strings such as
`"false"` are rejected instead of being treated as truthy.

Important groups are:

| Group | Purpose |
|---|---|
| `paths` | Cleaned Markdown output directory |
| `backup` | Backup enablement and destination |
| `cleanup` | Picture OCR, metadata, excluded sections, footnotes, emphasis, and OCR-noise reporting |
| `mutation` | Global minimum confidence and whole-pipeline report-only mode |
| `page_artifacts` | Repeated header/footer/page-number detection and optional removal |
| `unicode.fixes` | Individual Unicode processor switches |
| `regex.corrections` | Individual deterministic OCR correction switches |
| `symspell` | Dictionaries, confidence/frequency bounds, protected terms, and merge limits |
| `vocabulary_candidates` | Repeated-term threshold, report limit, and rejected-word file |
| `contextual_real_words` | Report-only known-word confusion rules |
| `context_validator` | Optional batched transformer validation of suspicious word boundaries |
| `tts_validation` | Report-only TTS chunk checks |
| `report` | JSON/summary exports, confidence filtering, and review threshold |
| `logging` | Log level and destination |

`cleanup.picture_ocr_mode` accepts `safe`, `keep`, or `remove`. In `safe` mode,
language-like picture text that passes conservative readability checks is
retained while likely OCR noise is removed. Use `keep` when short labels or
two-word captions must always survive.

`cleanup.excluded_sections` is a list of explicitly removable section names.
Removal stops at the next recognized structural heading; the cleaner does not
discard everything after an assumed book position. Set the list to `[]` to
disable this explicit section-name policy; front-matter, footnote, promotional,
publisher-tail, and other enabled cleanup policies still apply.

Line-break hyphens retain their boundary until `SymSpell` compares the joined
form with the genuine compound. For example, `inter-\nnational` may become
`international`, while `well-\nbeing` remains `well-being`. Disabling
`regex.enabled` or `regex.corrections.broken_hyphen_words.enabled` disables
that validation.

`mutation.minimum_confidence` is the minimum confidence required for an edit.
Suppressed proposals remain in reports with `applied: false`. Set
`mutation.report_only: true` for a dry run in which all proposed edits are
reported and mutation stages leave the exported Markdown unchanged.

The repository defaults preserve front matter, afterwords, character profiles,
footnotes, emphasis, picture text, and publisher tails. Enable a removal policy
only when it matches the intended output. `page_artifacts.mode` defaults to
`report_only`; change it to `remove` after validating findings for a corpus.

When `report.enabled` is false, only cleaned Markdown is exported and folder
mode writes no aggregate reports. When reports are enabled:

- `report.export_json` controls each file's `changes.json`.
- `report.export_summary` controls each file's `summary.md` and the aggregate
  `batch_summary.md`.
- `glossary_candidates.json` is always written per file and, in folder mode,
  as an aggregate report.
- setting `report.include_low_confidence` to false filters change records at
  `report.review_threshold` in the per-file change reports and aggregate batch
  summary. It does not alter glossary candidates, the in-memory audit log, or
  the full change total printed by the CLI.

The only environment override is:

```powershell
$env:OCR_OUTPUT_DIR = "D:\cleaned-books"
```

## Python API

```python
from markdownCleaner.pipeline import OCRPipeline

pipeline = OCRPipeline("markdownCleaner/config.yaml")
result = pipeline.run("books/Volume 01.md")

print(result["output"]["markdown"])
print(pipeline.context.total_changes)
```

One `OCRPipeline` instance may process multiple files sequentially. Every call
to `run()` creates a fresh processing context and stage list. If those calls
share an output directory, pass a distinct relative `report_subdirectory` for
each file; otherwise later calls replace the earlier `changes.json`,
`summary.md`, and `glossary_candidates.json`. Also choose distinct
`output_name` values when generated Markdown names could collide. Folder mode
does both automatically within a run.

```python
pipeline.run(
    "books/Volume 02.md",
    report_subdirectory="reports/volume-02",
)
```

## Development

Architecture, invariants, and extension guidance are in
[ARCHITECTURE.md](ARCHITECTURE.md).

Run the cleaner tests from `cleanup`:

```powershell
python -m pytest markdownCleaner\tests -q
python -m pytest markdownCleaner\tests --cov=markdownCleaner
```
