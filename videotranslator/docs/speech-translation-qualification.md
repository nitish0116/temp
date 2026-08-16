# Step 24 speech-to-English qualification

Last update: 2026-08-15. Workstation A only. `--speech-translation` remains
opt-in. Do not enable it by default from this report.

## Environment

| Item | Workstation A measurement |
| --- | --- |
| Cache root | `D:\PythonCaches` (`HF_HOME` = `D:\PythonCaches\huggingface`) |
| Temp staging | `D:\PythonCaches\tmp` |
| GPU | NVIDIA GeForce GTX 1050, 4096 MiB |
| Resolved compute | `auto` -> CUDA architecture supported, then CPU for SeamlessM4T-v2 |
| Fallback reason | `insufficient-vram-for-seamless-m4t-v2` (needs about 10 GiB VRAM) |
| Model | `facebook/seamless-m4t-v2-large` |
| License | CC-BY-NC 4.0 (fits this non-commercial project) |
| Offline prefetch | Snapshot completed to `...\hub\models--facebook--seamless-m4t-v2-large\snapshots\5f8cc790b19fc3f67a61c105133b20b34e3dcb76` |

Workstation B must prefetch into its own `PYTHON_CACHE_HOME`
(`C:\Users\z005537p\NitishWork\HM\temp\.model-cache`). That cannot be done from
this machine. Its 16 GiB GPU is the first host that should try CUDA for this
checkpoint.

## What was run

A `--probe` pass decoded only reviewed defect groups (plus two Japanese opening
groups, because that sample has no fixture timestamps):

```powershell
python -m videotranslator qualify-speech-translation --device auto --probe
```

Machine-readable output: [speech-translation-qualification.json](speech-translation-qualification.json).

Full three-sample coverage was not completed. On this CPU path, warmed groups took
about 7–56 seconds and the first Korean group took 97 seconds. A 114–269 group
episode would be multi-hour and was not started after the probe showed the audio
English was not recovering the reviewed terms.

## Probe results

| Sample | Groups probed | Status | Audio English | Required terms | Notes |
| --- | ---: | --- | --- | --- | --- |
| Duty First (Japanese) | 2 | ok | independent, not the reviewed defects | n/a | Opening groups only; no Shimonoseki-class fixture |
| Korean Episode 1 @ 9.3s (`cute`) | 1 | ok | `I'm going to take a look at it.` | missing `cute` | Independent of `freak`, but not the burned reference |
| Korean Episode 1 @ 508.8s (`Seoul`) | 1 | ok | `[The first time I saw it, I was like, "Oh, my God!"]` | missing `Seoul` | Independent of `Seattle`, not a usable correction |
| Linglong @ 88.8s (Shimonoseki) | 1 | ok | Hallucinated “two-thousand…” loop; no Treaty/Japan/Shimonoseki | missing | Independent of `22,000` / `Yangtze Province`, not a correct recovery |

Every probed group received `status=ok` audio-derived English. None of the
reviewed gold terms were recovered. The audio route therefore satisfies the
“independent evidence exists” part of Step 24, and does **not** yet satisfy
“corrupt ASR is diagnosable toward the correct meaning.”

## Default-enablement decision

Keep `--speech-translation` off by default until:

1. Workstation B (or another ≥10 GiB GPU) completes a full three-sample run.
2. Every semantic group is `ok` or explicit `unsupported`/`failed`.
3. The `cute`, `Seoul`, and Treaty of Shimonoseki groups contain the required
   terms, or a later adjudicator (Step 26) is in place to use this evidence
   without promoting Seamless text blindly.

## Commands for the other workstation

```powershell
$env:PYTHON_CACHE_HOME = "C:\Users\z005537p\NitishWork\HM\temp\.model-cache"
$env:HF_HOME = "$env:PYTHON_CACHE_HOME\huggingface"
$env:TEMP = "$env:PYTHON_CACHE_HOME\tmp"
$env:TMP = $env:TEMP
python -c "from huggingface_hub import snapshot_download; print(snapshot_download('facebook/seamless-m4t-v2-large'))"
python -m videotranslator qualify-speech-translation --device auto
```
