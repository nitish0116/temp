[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [Parameter()]
    [string] $Source = (Join-Path $PSScriptRoot 'Library'),

    [Parameter()]
    [string] $Destination = (Join-Path $PSScriptRoot 'lib_to_up')
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $Source -PathType Container)) {
    throw "Source directory does not exist: $Source"
}

$sourcePath = (Resolve-Path -LiteralPath $Source).Path.TrimEnd(
    [IO.Path]::DirectorySeparatorChar,
    [IO.Path]::AltDirectorySeparatorChar
)

if (-not (Test-Path -LiteralPath $Destination -PathType Container)) {
    if ($PSCmdlet.ShouldProcess($Destination, 'Create destination directory')) {
        New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    }
}

# Resolve a not-yet-created destination without requiring it to exist in -WhatIf mode.
if (Test-Path -LiteralPath $Destination -PathType Container) {
    $destinationPath = (Resolve-Path -LiteralPath $Destination).Path.TrimEnd(
        [IO.Path]::DirectorySeparatorChar,
        [IO.Path]::AltDirectorySeparatorChar
    )
}
else {
    $destinationPath = [IO.Path]::GetFullPath($Destination).TrimEnd(
        [IO.Path]::DirectorySeparatorChar,
        [IO.Path]::AltDirectorySeparatorChar
    )
}

if ($sourcePath.Equals($destinationPath, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Source and destination must be different directories.'
}

$sourcePrefix = $sourcePath + [IO.Path]::DirectorySeparatorChar
$destinationPrefix = $destinationPath + [IO.Path]::DirectorySeparatorChar
if (
    $destinationPath.StartsWith($sourcePrefix, [StringComparison]::OrdinalIgnoreCase) -or
    $sourcePath.StartsWith($destinationPrefix, [StringComparison]::OrdinalIgnoreCase)
) {
    throw 'Source and destination cannot contain one another.'
}

$mediaFiles = @(
    Get-ChildItem -LiteralPath $sourcePath -Recurse -File -Force |
        Where-Object { $_.Extension -in '.mp3', '.mp4' }
)

$moved = 0
$deduplicated = 0
$conflicts = 0

foreach ($file in $mediaFiles) {
    if (-not $file.FullName.StartsWith($sourcePrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to process a file outside the source directory: $($file.FullName)"
    }

    $relativePath = $file.FullName.Substring($sourcePrefix.Length)
    $targetFile = Join-Path $destinationPath $relativePath
    $targetDirectory = Split-Path -Parent $targetFile

    if (Test-Path -LiteralPath $targetFile -PathType Leaf) {
        $existing = Get-Item -LiteralPath $targetFile -Force
        $identical = $existing.Length -eq $file.Length

        if ($identical) {
            $sourceHash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash
            $targetHash = (Get-FileHash -LiteralPath $existing.FullName -Algorithm SHA256).Hash
            $identical = $sourceHash -eq $targetHash
        }

        if ($identical) {
            if ($PSCmdlet.ShouldProcess($file.FullName, "Remove duplicate already stored at '$targetFile'")) {
                Remove-Item -LiteralPath $file.FullName -Force
                $deduplicated++
            }
        }
        else {
            Write-Warning "Conflict left untouched: '$targetFile' already exists with different content."
            $conflicts++
        }

        continue
    }

    if ($PSCmdlet.ShouldProcess($file.FullName, "Move to '$targetFile'")) {
        if (-not (Test-Path -LiteralPath $targetDirectory -PathType Container)) {
            New-Item -ItemType Directory -Path $targetDirectory -Force | Out-Null
        }

        Move-Item -LiteralPath $file.FullName -Destination $targetFile
        $moved++
    }
}

# Remove only directories made empty by moving media; preserve all other content.
if (-not $WhatIfPreference) {
    Get-ChildItem -LiteralPath $sourcePath -Recurse -Directory -Force |
        Sort-Object { $_.FullName.Length } -Descending |
        ForEach-Object {
            if (-not (Get-ChildItem -LiteralPath $_.FullName -Force | Select-Object -First 1)) {
                Remove-Item -LiteralPath $_.FullName -Force
            }
        }
}

[pscustomobject]@{
    Found        = $mediaFiles.Count
    Moved        = $moved
    Deduplicated = $deduplicated
    Conflicts    = $conflicts
    Source       = $sourcePath
    Destination  = $destinationPath
}

if ($conflicts -gt 0) {
    throw "$conflicts conflicting media file(s) were left untouched."
}
