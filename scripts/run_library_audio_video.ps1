param(
    [string]$RepoRoot = 'C:/Users/z005537p/NitishWork/HM/temp',
    [string]$LibraryFolder = 'TBAtE',
    [int]$StartVolume,
    [int]$EndVolume,
    [string]$Voice = 'Ava',
    [int]$EdgeWorkers = 8,
    [string]$Resolution = '480p'
)

$ErrorActionPreference = 'Stop'

$libraryRoot = Join-Path $RepoRoot 'Library'
$seriesRoot = if ([System.IO.Path]::IsPathRooted($LibraryFolder)) {
    $LibraryFolder
}
else {
    Join-Path $libraryRoot $LibraryFolder
}

$cleanedRoot = Join-Path $seriesRoot 'output/Cleaned'
$mdAudioDir = Join-Path $RepoRoot 'md-audio'
$mp3ToYTDir = Join-Path $RepoRoot 'mp3ToYT'

if (-not (Test-Path -LiteralPath $libraryRoot)) {
    throw "Library root not found: $libraryRoot"
}
if (-not (Test-Path -LiteralPath $seriesRoot)) {
    throw "Series folder not found: $seriesRoot"
}
if (-not (Test-Path -LiteralPath $cleanedRoot)) {
    throw "Cleaned output root not found: $cleanedRoot"
}
if (-not (Test-Path -LiteralPath $mdAudioDir)) {
    throw "md-audio directory not found: $mdAudioDir"
}
if (-not (Test-Path -LiteralPath $mp3ToYTDir)) {
    throw "mp3ToYT directory not found: $mp3ToYTDir"
}

if ($PSBoundParameters.ContainsKey('StartVolume') -xor $PSBoundParameters.ContainsKey('EndVolume')) {
    throw 'StartVolume and EndVolume must be provided together.'
}

if (
    $PSBoundParameters.ContainsKey('StartVolume') -and
    $PSBoundParameters.ContainsKey('EndVolume') -and
    $StartVolume -gt $EndVolume
) {
    throw 'StartVolume cannot be greater than EndVolume.'
}

$allVolumeDirs = Get-ChildItem -LiteralPath $cleanedRoot -Directory | Where-Object {
    $_.Name -match '^Volume\s+\d+$'
} | Sort-Object {
    [int](($_.Name -replace '^Volume\s+', ''))
}

if ($PSBoundParameters.ContainsKey('StartVolume')) {
    $volumeDirs = $allVolumeDirs | Where-Object {
        $n = [int](($_.Name -replace '^Volume\s+', ''))
        $n -ge $StartVolume -and $n -le $EndVolume
    }
}
else {
    $volumeDirs = $allVolumeDirs
}

if (-not $volumeDirs -or $volumeDirs.Count -eq 0) {
    throw "No matching volume folders found in: $cleanedRoot"
}

$rangeText = if ($PSBoundParameters.ContainsKey('StartVolume')) {
    "volumes $StartVolume to $EndVolume"
}
else {
    'all discovered volume folders'
}

Write-Host "Series: $seriesRoot" -ForegroundColor Cyan
Write-Host "Running for $rangeText..." -ForegroundColor Cyan

foreach ($dir in $volumeDirs) {
    $volumeDir = $dir.FullName
    $volumeLabel = $dir.Name

    $imagePath = Get-ChildItem -LiteralPath $volumeDir -File |
        Where-Object { $_.Extension -in @('.png', '.jpg', '.jpeg', '.webp') } |
        Sort-Object Name |
        Select-Object -First 1 -ExpandProperty FullName

    if ($null -eq $imagePath) {
        Write-Warning "Skipping $volumeLabel because no image file (.png/.jpg/.jpeg/.webp) was found in $volumeDir"
        continue
    }

    Write-Host "`n=== $volumeLabel ===" -ForegroundColor Yellow

    Push-Location $mdAudioDir
    try {
        python md_to_audio.py --chapter-markers --backend edge --voice $Voice --edge-workers $EdgeWorkers "$volumeDir" "$volumeDir" --cue-file
    }
    finally {
        Pop-Location
    }

    Push-Location $mp3ToYTDir
    try {
        python mp3_to_youtube.py "$volumeDir" "$volumeDir" --resolution $Resolution --image "$imagePath"
    }
    finally {
        Pop-Location
    }
}

Write-Host "`nDone." -ForegroundColor Green
