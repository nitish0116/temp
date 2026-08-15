# Step 25 dedicated text-MT qualification

Last update: 2026-08-15. Both candidates remain unqualified and the production
translation default is unchanged.

The repeatable command is `python -m videotranslator qualify-text-translation`.
It uses each model's native protocol, float16 CUDA weights, deterministic beam
generation, per-model/source-language/text caches, portable report paths, and
sequential unloading between source languages.

| Candidate | `cute` | `Seoul` | Shimonoseki | Decision |
| --- | --- | --- | --- | --- |
| `google/madlad400-3b-mt` | “It's cute, isn't it?” | preserved | “Treaty of Macau” | reject |
| `facebook/nllb-200-3.3B` | “Isn't that lovely?” | preserved | “Customs Treaty” | reject |

MADLAD passed two of three strict terminology fixtures; NLLB passed one. Neither
was promoted and a full episode-wide benchmark was deliberately skipped because
the bounded release gate had already failed. Machine-readable probe evidence is
in `text-translation-qualification.json` and
`text-translation-qualification-nllb.json`.

Next: qualify a stronger dedicated MT candidate or a general terminology-aware
mechanism that is source-grounded and not episode-specific. Do not weaken the
Shimonoseki gate or silently replace it with aggregate similarity.
