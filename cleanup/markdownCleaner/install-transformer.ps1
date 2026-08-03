$ErrorActionPreference = "Stop"

$python = "D:\Git\Projects\.venv\Scripts\python.exe"
$cacheRoot = "D:\PythonCaches"

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Shared virtual-environment Python was not found at: $python"
}

$env:PIP_CACHE_DIR = Join-Path $cacheRoot "pip"
$env:HF_HOME = Join-Path $cacheRoot "huggingface"
$env:TORCH_HOME = Join-Path $cacheRoot "torch"
$env:TEMP = Join-Path $cacheRoot "temp"
$env:TMP = $env:TEMP

@(
    $env:PIP_CACHE_DIR,
    $env:HF_HOME,
    $env:TORCH_HOME,
    $env:TEMP
) | ForEach-Object {
    New-Item -ItemType Directory -Path $_ -Force | Out-Null
}

$requirements = Join-Path $PSScriptRoot "requirements-transformer.txt"
Write-Host "Installing into: $python"
Write-Host "Cache root:     $cacheRoot"
& $python -m pip install -r $requirements
if ($LASTEXITCODE -ne 0) {
    throw "pip failed with exit code $LASTEXITCODE"
}
