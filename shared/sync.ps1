$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$source = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot 'ui'))
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$targets = @('windows/src/ExaoneChat/wwwroot/shared', 'web/frontend/public/shared')
foreach ($relative in $targets) {
    $target = [IO.Path]::GetFullPath((Join-Path $projectRoot $relative))
    if (-not $target.StartsWith($projectRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase) -or
        (Split-Path $target -Leaf) -ne 'shared') { throw "Unsafe sync target: $target" }
    if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Recurse -Force }
    New-Item -ItemType Directory -Path $target -Force | Out-Null
    Copy-Item -Path (Join-Path $source '*') -Destination $target -Recurse -Force
    $originalFiles = @(Get-ChildItem -LiteralPath $source -Recurse -File)
    if (@(Get-ChildItem -LiteralPath $target -Recurse -File).Count -ne $originalFiles.Count) { throw 'Sync file count mismatch.' }
    foreach ($file in $originalFiles) {
        $copy = Join-Path $target ([IO.Path]::GetRelativePath($source, $file.FullName))
        if ((Get-FileHash -LiteralPath $file.FullName).Hash -ne (Get-FileHash -LiteralPath $copy).Hash) { throw "Hash mismatch: $copy" }
    }
    Write-Host "Synced and verified $($originalFiles.Count) files: $target"
}
