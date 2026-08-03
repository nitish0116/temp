# Broken-word classifier

This module owns the human-reviewed dataset and, in the future, the training
and evaluation code for a supervised broken-word classifier. MarkdownCleaner
collects examples here, but normal cleaning never trains or changes a model.

## Current files

- `data/broken_word_training.json` contains transformer-scored candidates and
  user labels.
- Training and evaluation commands have not been implemented yet.

## End-to-end workflow

```text
Run MarkdownCleaner
        |
        v
Lexical rules find a suspicious boundary
        |
        v
Create spaced and joined contextual variants
        |
        v
Transformer scores both variants
        |
        v
Merge the example into broken_word_training.json
        |
        v
User assigns user_label and review_status
        |
        v
Future classifier training uses reviewed examples only
```

## Collection prerequisites

Both switches in `cleanup/markdownCleaner/config.yaml` must be enabled:

```yaml
context_validator:
  enabled: true
  model: "distilbert/distilroberta-base"
  local_files_only: true

classifier_dataset:
  enabled: true
  output: "../../classifier/data/broken_word_training.json"
```

The output path is resolved relative to `cleanup/markdownCleaner/config.yaml`.
The configured value therefore points to this module's `data` directory.

With `local_files_only: true`, the model must already be present in the Hugging
Face cache at `D:\PythonCaches\huggingface`. Set it to `false` for one run if
the model needs to be downloaded, then restore it to `true` for offline use.

Install the optional Torch and Transformers dependencies with:

```powershell
powershell -ExecutionPolicy Bypass -File `
  ".\cleanup\markdownCleaner\install-transformer.ps1"
```

## Collecting examples

From `D:\Git\Projects\nitish0116\temp`, run MarkdownCleaner normally:

```powershell
$env:PYTHONPATH="$PWD\cleanup"

python -m markdownCleaner `
  "Library\TBAtE\output\o\The Beginning After the End - Volume 07.md"
```

Folder input works as well. Add `--recursive` when nested folders should be
processed.

Collection happens only after the normal lexical rules propose a boundary.
The transformer does not inspect every pair of adjacent words. Proposals can
come from dictionaries, word-frequency evidence, OCR boundary rules, or
`ocr_boundary_candidates.json`.

For a proposal such as `sol diers`, the validator builds two versions:

```text
The sol diers entered the city.
The soldiers entered the city.
```

It scores the target tokens in both contexts. The decision margin is:

```text
transformer_joined_score - transformer_spaced_score
```

If the margin meets `context_validator.merge_margin`, `transformer_label` is
`join`; otherwise it is `keep_spaced`. This is advisory evidence, not a trusted
training label.

## Dataset schema

The top-level document is:

```json
{
  "schema_version": 1,
  "examples": []
}
```

A collected example has this shape:

```json
{
  "id": "stable-sha256-hash",
  "source_file": "Volume 01.md",
  "context": "The sol diers entered the city.",
  "spaced_text": "The sol diers entered the city.",
  "joined_text": "The soldiers entered the city.",
  "left": "sol",
  "right": "diers",
  "replacement": "soldiers",
  "transformer_label": "join",
  "transformer_spaced_score": -8.42,
  "transformer_joined_score": -2.17,
  "transformer_margin": 6.25,
  "user_label": null,
  "review_status": "pending",
  "reviewed_at": null,
  "user_notes": "",
  "evidence": "wordfreq"
}
```

| Field | Meaning |
| --- | --- |
| `id` | Stable hash used to update an example without duplicating it |
| `source_file` | Name of the Markdown document that produced the example |
| `context` / `spaced_text` | Original local context with the boundary retained |
| `joined_text` | Same local context with the proposed replacement applied |
| `left`, `right`, `replacement` | Boundary fragments and proposed joined form |
| `transformer_label` | Model suggestion: `join` or `keep_spaced` |
| Transformer scores and margin | Advisory masked-language-model evidence |
| `user_label` | Trusted human decision; initially `null` |
| `review_status` | `pending` until reviewed by a user |
| `reviewed_at` | Optional ISO-8601 review timestamp |
| `user_notes` | Optional explanation or source-check note |
| `evidence` | Lexical rule that originally proposed the candidate |

## Human review

For a confirmed join, edit the record to:

```json
"user_label": "join",
"review_status": "reviewed",
"reviewed_at": "2026-08-03T12:00:00+05:30",
"user_notes": "Confirmed against the source PDF."
```

For a legitimate space:

```json
"user_label": "keep_spaced",
"review_status": "reviewed"
```

For an unsuitable or unclear training example:

```json
"user_label": "skip",
"review_status": "reviewed"
```

Allowed user labels are `join`, `keep_spaced`, and `skip`. Future supervised
training must use only records where `review_status` is `reviewed` and
`user_label` is `join` or `keep_spaced`. Transformer suggestions must never be
treated as human ground truth.

## Repeated cleaner runs

The collector hashes the spaced context, joined context, and broken-word pair.
Encountering the same example again refreshes generated fields such as model
scores instead of appending a duplicate.

These reviewer-controlled fields are always preserved:

- `user_label`
- `review_status`
- `reviewed_at`
- `user_notes`

The complete JSON is replaced atomically through a temporary file, reducing
the risk of leaving a partially written dataset.

## Why the dataset may remain empty

No example is written when:

- either required configuration switch is disabled;
- the document produces no suspicious boundary candidates;
- candidates are already trusted reviewed decisions and bypass the model;
- the relevant text is protected Markdown rather than editable prose;
- required Torch/Transformers packages are unavailable;
- `local_files_only` is true but the configured model is not cached; or
- transformer scoring fails before the collector is reached.

An empty dataset is therefore not necessarily an error; it can mean the input
contained no unresolved transformer-scored boundaries.

## Training

The supervised training flow is:

```text
broken_word_training.json
        |
        v
Keep trusted human-reviewed examples
        |
        v
Build marked spaced/joined model input
        |
        v
Split complete source documents
        |
        v
Fine-tune a two-label transformer
        |
        v
Select and save the best validation checkpoint
        |
        v
Evaluate on documents excluded from training
```

### 1. Select trusted examples

`dataset.py` includes a record only when:

```json
"review_status": "reviewed",
"user_label": "join"
```

or:

```json
"review_status": "reviewed",
"user_label": "keep_spaced"
```

Pending records, `user_label: null`, and `user_label: "skip"` are excluded.
The advisory `transformer_label` is never used as the supervised target.
Reviewed records with unknown labels, duplicate IDs, missing source filenames,
or missing variant text stop training with a validation error.

Labels are encoded as:

```text
0 = keep_spaced
1 = join
```

### 2. Build classifier input

The exact candidate boundary is marked, and both alternatives are provided to
the model. A record for `sol diers` becomes:

```text
[SPACED] The sol <BOUNDARY> diers entered the city.
[JOINED] The soldiers entered the city.
```

`<BOUNDARY>`, `[SPACED]`, and `[JOINED]` are registered as additional tokenizer
tokens. The configured `model.maximum_length` limits the final tokenized input.

### 3. Split by source document

Individual examples are not randomly mixed across data partitions. All
examples from one `source_file` remain together in exactly one of:

```text
train
validation
test
```

This prevents similar sentences from one book leaking into both training and
evaluation. The default ratios are 80% training, 10% validation, and 10% test.
The exact result is reproducible through `training.random_seed` and is written
to `data/split_manifest.json`.

Training requires reviewed records from at least three source files so every
partition can exist. The training partition must contain both labels.

### 4. Fine-tune DistilRoBERTa

The current implementation loads:

```python
AutoModelForSequenceClassification.from_pretrained(
    "distilbert/distilroberta-base",
    num_labels=2,
)
```

Unlike the collection-time inference model, training calls `model.train()`,
calculates classification loss, performs backpropagation, and updates weights
with AdamW:

```text
loss.backward()
optimizer.step()
```

Default settings from `config.yaml` are:

```yaml
model:
  base_model: "distilbert/distilroberta-base"
  maximum_length: 256
  local_files_only: true

training:
  batch_size: 8
  learning_rate: 0.00002
  epochs: 3
  weight_decay: 0.01
  random_seed: 42
  device: "auto"
```

`device: auto` uses CUDA when available and otherwise uses the CPU. With
`local_files_only: true`, the base model must already exist in the configured
Hugging Face cache.

### 5. Consider label balance

Before trusting a model, inspect the `join` and `keep_spaced` counts recorded in
`training_metadata.json`. Accuracy can be misleading when one label dominates.
The current first version does not apply class-weighted loss or oversampling,
so heavily imbalanced data should be corrected by reviewing more examples from
the underrepresented class before training. Class weighting or balanced
batches can be added later if needed.

### 6. Train and select a checkpoint

Review `config.yaml`, then run from the repository root:

Review `config.yaml`, then run from the repository root:

```powershell
python -m classifier.train
```

Each epoch reports training loss, validation loss, accuracy, per-label
precision and recall, and a confusion matrix. Model selection prioritizes:

1. Higher validation precision for `join`.
2. Higher validation recall for `join` when precision is tied.
3. Lower validation loss when both are tied.

This emphasis is deliberate: an incorrect automatic join changes legitimate
text. The best checkpoint, tokenizer, and `training_metadata.json` are written
to:

```text
D:\MarkdownCleanerModels\broken-word-classifier
```

The metadata records the complete configuration, device, class/split counts,
per-epoch history, and best validation selection key.

## Evaluation

Evaluate the saved best checkpoint with:

```powershell
python -m classifier.evaluate
```

The evaluator loads the saved model and the test records from
`data/split_manifest.json`, calculates `P(join)`, and writes `evaluation.json`
beside the model. The report includes:

- accuracy;
- precision and recall for both labels;
- `tn`, `fp`, `fn`, and `tp` confusion counts; and
- conservative automatic join and keep-spaced thresholds.

Thresholds must meet `evaluation.minimum_join_precision` and
`evaluation.minimum_keep_precision`. Within that constraint, the evaluator
selects the threshold with the highest recall. A threshold is `null` when the
available examples cannot support the required precision.

The current evaluator derives these exploratory thresholds from the test
partition. Before production integration, threshold selection should move to
the validation partition and the test partition should be used once for an
untouched final measurement.

## Intended MarkdownCleaner integration

Training and evaluation do not yet change the cleaner. The intended future
decision path is:

```text
Lexical rules find candidate
        |
        v
Reviewed permanent decision exists? -- yes --> use reviewed decision
        |
        no
        v
Trained classifier predicts P(join)
        |
        +-- above safe join threshold --> join
        +-- below safe keep threshold --> preserve space
        +-- between thresholds --------> preserve and request review
```

Initially, the trained classifier and existing masked-language-model validator
should run side by side. Comparing both predictions against `user_label` makes
it possible to verify that the classifier is genuinely safer before granting
it authority to modify text.

## Dataset size guidance

A small offline experiment can start near 500 reviewed examples, but it should
not immediately control automatic cleaning. A more useful target is roughly
2,000–5,000 reviewed examples with:

- both labels well represented;
- multiple books and authors;
- genuine OCR splits such as `sol diers`;
- legitimate spaced phrases such as `log in`; and
- source documents reserved for final evaluation.

## Current scope

Collection, validation, document-safe splitting, fine-tuning, checkpoint
selection, and standalone evaluation are implemented. Class balancing,
validation-only threshold selection, confidence calibration, and using the
trained classifier inside MarkdownCleaner remain future integration steps.
Until then, cleaning continues to use the existing transformer context
validator.
