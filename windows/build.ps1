$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# Builds dist\ExaoneChat.exe (single file, needs the .NET 8 Desktop Runtime and WebView2 Runtime).
$project = Join-Path $PSScriptRoot 'src/ExaoneChat/ExaoneChat.csproj'
$dist = Join-Path $PSScriptRoot 'dist'

& pwsh -NoProfile -File (Join-Path $PSScriptRoot '../shared/sync.ps1')
if ($LASTEXITCODE -ne 0) { throw 'Shared UI sync failed.' }

& pwsh -NoProfile -File (Join-Path $PSScriptRoot 'tools/generate_icon.ps1')
if ($LASTEXITCODE -ne 0) { throw 'Icon generation failed.' }

& dotnet publish $project -c Release -r win-x64 --self-contained false -p:PublishSingleFile=true -p:IncludeNativeLibrariesForSelfExtract=true -o $dist
if ($LASTEXITCODE -ne 0) { throw 'Publish failed.' }

$exe = Join-Path $dist 'ExaoneChat.exe'
if (-not (Test-Path -LiteralPath $exe)) { throw 'Publish output missing.' }
Write-Host "Built $exe"
