param([switch]$Offline, [switch]$SkipPrefetch)

$ErrorActionPreference = "Stop"
$workspace = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$environment = Join-Path $workspace "ocrTransformerEnv"
$python = Join-Path $environment "Scripts\python.exe"
$requirements = Join-Path $PSScriptRoot "requirements-transformer.txt"
$marker = Join-Path $environment ".markdown-cleaner-environment.json"
$model = "distilbert/distilroberta-base"

$cacheRoot = [Environment]::GetEnvironmentVariable("PYTHON_CACHE_HOME", "Process")
if ([string]::IsNullOrWhiteSpace($cacheRoot)) {
    $cacheRoot = [Environment]::GetEnvironmentVariable("PYTHON_CACHE_HOME", "User")
}
if ([string]::IsNullOrWhiteSpace($cacheRoot)) {
    $cacheRoot = Join-Path $workspace ".model-cache"
}
$env:PYTHON_CACHE_HOME = $cacheRoot
if ([string]::IsNullOrWhiteSpace($env:HF_HOME)) {
    $env:HF_HOME = Join-Path $cacheRoot "huggingface"
}
$env:HF_HUB_CACHE = Join-Path $env:HF_HOME "hub"
$env:HUGGINGFACE_HUB_CACHE = $env:HF_HUB_CACHE
if ([string]::IsNullOrWhiteSpace($env:TORCH_HOME)) {
    $env:TORCH_HOME = Join-Path $cacheRoot "torch"
}
$env:TEMP = Join-Path $cacheRoot "tmp"
$env:TMP = $env:TEMP
@($cacheRoot, $env:HF_HOME, $env:HF_HUB_CACHE, $env:TORCH_HOME, $env:TEMP) |
    ForEach-Object { New-Item -ItemType Directory -Path $_ -Force | Out-Null }

$bootstrapPython = (Get-Command python -ErrorAction Stop).Source
$pythonMinor = & $bootstrapPython -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
$requirementsHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $requirements).Hash.ToLowerInvariant()
$fingerprint = "$pythonMinor`:$requirementsHash"
$isCurrent = $false
if ((Test-Path -LiteralPath $python -PathType Leaf) -and (Test-Path -LiteralPath $marker -PathType Leaf)) {
    try {
        $state = Get-Content -Raw -LiteralPath $marker | ConvertFrom-Json
        $isCurrent = $state.requirements_fingerprint -eq $fingerprint
    } catch { $isCurrent = $false }
}

if (-not $isCurrent) {
    if ($Offline) {
        throw "ocrTransformerEnv is missing or stale; run install-transformer.ps1 online first."
    }
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        & $bootstrapPython -m venv --system-site-packages $environment
        if ($LASTEXITCODE -ne 0) { throw "Virtual-environment creation failed." }
    }
    & $python -m pip install -r $requirements
    if ($LASTEXITCODE -ne 0) { throw "Transformer dependency installation failed." }
    @{
        schema_version = 1
        requirements = "cleanup/markdownCleaner/requirements-transformer.txt"
        requirements_fingerprint = $fingerprint
        python_version = $pythonMinor
    } | ConvertTo-Json | Set-Content -LiteralPath $marker -Encoding utf8
}

if (-not $SkipPrefetch) {
    $localOnly = if ($Offline) { "True" } else { "False" }
    & $python -c "from transformers import AutoModelForMaskedLM, AutoTokenizer; m='$model'; AutoTokenizer.from_pretrained(m, use_fast=True, local_files_only=$localOnly); AutoModelForMaskedLM.from_pretrained(m, local_files_only=$localOnly); print('Context model ready:', m)"
    if ($LASTEXITCODE -ne 0) { throw "Context-model prefetch or offline verification failed." }
}

Write-Host "Transformer environment ready: $environment"
Write-Host "Shared model cache:          $cacheRoot"
Write-Host "Model:                       $model (Apache-2.0)"
