#requires -Version 7.0
[CmdletBinding()]
param([switch]$DryRun)

$ErrorActionPreference = 'Stop'
$webRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$tag = Get-Date -Format 'yyyyMMdd-HHmmss'
$nasTarget = 'codex-nas'
$remoteRoot = '/volume1/docker/exaone'
$stage = "/tmp/exaone-deploy-$tag"
$baseCompose = Join-Path $webRoot 'docker-compose.yml'
$searxImage = ((Get-Content -LiteralPath $baseCompose | Select-String '^\s+image: searxng/searxng:').Line.Trim() -replace '^image:\s*', '')
if ($searxImage -notmatch '^searxng/searxng:[a-zA-Z0-9_.-]+$') { throw 'SearXNG image tag is invalid.' }

if ($DryRun) {
    Write-Output "1. EXAONE_TAG=$tag; docker compose -f docker-compose.yml build api web"
    Write-Output "2. docker save exaone-chat-api:$tag exaone-chat-web:$tag -> gzip stream -> ssh $nasTarget sudo -n /usr/local/bin/docker load"
    Write-Output "3. NAS pull: postgres:16-alpine, $searxImage"
    Write-Output "4. Upload three compose files, searxng/settings.yml, env template and nas-proxy.py to $remoteRoot via a private staging directory"
    Write-Output '5. If .env is absent: generate APP_SECRET/POSTGRES_PASSWORD/SEARXNG_SECRET with openssl on NAS, atomically save mode 600; otherwise update only EXAONE_TAG'
    Write-Output '6. NAS docker-compose -p exaone -f docker-compose.yml -f docker-compose.synology.yml up -d --no-build'
    Write-Output '7. NAS curl -fsS http://127.0.0.1:61480/api/health'
    Write-Output 'DryRun: no commands, transfers, image builds or NAS writes executed.'
    return
}

function Start-RedirectedProcess([string]$File, [string[]]$Arguments, [bool]$InputPipe = $false, [bool]$OutputPipe = $false) {
    $info = [Diagnostics.ProcessStartInfo]::new()
    $info.FileName = $File
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardError = $true
    $info.RedirectStandardInput = $InputPipe
    $info.RedirectStandardOutput = $OutputPipe
    foreach ($argument in $Arguments) { [void]$info.ArgumentList.Add($argument) }
    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $info
    [void]$process.Start()
    return $process
}

function Invoke-Native([string]$File, [string[]]$Arguments) {
    & $File @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$File failed (exit $LASTEXITCODE)." }
}

function Send-NasScript([string]$Script, [string]$RemoteCommand) {
    $process = Start-RedirectedProcess 'ssh' @('-o', 'BatchMode=yes', $nasTarget, $RemoteCommand) $true $true
    $errors = $process.StandardError.ReadToEndAsync()
    $output = $process.StandardOutput.ReadToEndAsync()
    try {
        $bytes = [Text.UTF8Encoding]::new($false).GetBytes(($Script -replace "`r`n", "`n"))
        $process.StandardInput.BaseStream.Write($bytes, 0, $bytes.Length)
        $process.StandardInput.Close()
        $process.WaitForExit()
        if ($process.ExitCode -ne 0) { throw 'NAS deployment step failed; sensitive remote output was suppressed.' }
        Write-Output ($output.GetAwaiter().GetResult().Trim())
        [void]$errors.GetAwaiter().GetResult()
    } finally {
        if (-not $process.HasExited) { $process.Kill() }
        $process.Dispose()
    }
}

$oldTag = $env:EXAONE_TAG
Push-Location $webRoot
try {
    $env:EXAONE_TAG = $tag
    Invoke-Native 'docker' @('compose', '-f', 'docker-compose.yml', 'build', 'api', 'web')

    # Native binary streams bypass PowerShell text pipelines; gzip is provided by .NET.
    $loader = Start-RedirectedProcess 'ssh' @('-o', 'BatchMode=yes', $nasTarget, 'sudo -n /usr/local/bin/docker load') $true $true
    $save = Start-RedirectedProcess 'docker' @('save', "exaone-chat-api:$tag", "exaone-chat-web:$tag") $false $true
    $loaderErrors = $loader.StandardError.ReadToEndAsync()
    $loaderOutput = $loader.StandardOutput.ReadToEndAsync()
    $saveErrors = $save.StandardError.ReadToEndAsync()
    $gzip = [IO.Compression.GZipStream]::new($loader.StandardInput.BaseStream, [IO.Compression.CompressionLevel]::Fastest, $true)
    try {
        $save.StandardOutput.BaseStream.CopyTo($gzip)
        $gzip.Dispose()
        $loader.StandardInput.Close()
        $save.WaitForExit()
        $loader.WaitForExit()
        if ($save.ExitCode -ne 0 -or $loader.ExitCode -ne 0) { throw 'Image streaming failed.' }
        Write-Output ($loaderOutput.GetAwaiter().GetResult().Trim())
        [void]$saveErrors.GetAwaiter().GetResult()
        [void]$loaderErrors.GetAwaiter().GetResult()
    } finally {
        $gzip.Dispose()
        if (-not $save.HasExited) { $save.Kill() }
        if (-not $loader.HasExited) { $loader.Kill() }
        $save.Dispose()
        $loader.Dispose()
    }

    Invoke-Native 'ssh' @('-o', 'BatchMode=yes', $nasTarget, "sudo -n /usr/local/bin/docker pull postgres:16-alpine && sudo -n /usr/local/bin/docker pull $searxImage")
    Invoke-Native 'ssh' @('-o', 'BatchMode=yes', $nasTarget, "umask 077; mkdir -p '$stage'")
    $files = @($baseCompose, (Join-Path $webRoot 'docker-compose.mock.yml'),
        (Join-Path $PSScriptRoot 'docker-compose.synology.yml'), (Join-Path $PSScriptRoot 'env.synology.example'),
        (Join-Path $PSScriptRoot 'nas-proxy.py'))
    Invoke-Native 'scp' ($files + @("${nasTarget}:$stage/"))
    Invoke-Native 'scp' @((Join-Path $webRoot 'searxng/settings.yml'), "${nasTarget}:$stage/searxng-settings.yml")

    $install = @'
set -eu
tag="$1"
stage="$2"
root=/volume1/docker/exaone
umask 077
mkdir -p "$root/searxng" "$root/backups"
for file in docker-compose.yml docker-compose.mock.yml docker-compose.synology.yml env.synology.example nas-proxy.py; do
    cp "$stage/$file" "$root/$file"
done
cp "$stage/searxng-settings.yml" "$root/searxng/settings.yml"
# The file contains only a placeholder; the real secret is supplied through the environment.
chmod 644 "$root/searxng/settings.yml"
python3 - "$root/.env" "$root/env.synology.example" "$tag" <<'PY'
import os, pathlib, re, subprocess, sys, tempfile
path, template, tag = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), sys.argv[3]
if path.is_symlink():
    raise SystemExit('Refusing a symlink for .env')
exists = path.exists()
text = path.read_text() if exists else template.read_text()
if not exists:
    for name in ('APP_SECRET', 'POSTGRES_PASSWORD', 'SEARXNG_SECRET'):
        value = subprocess.check_output(['openssl', 'rand', '-base64', '32'], text=True).strip()
        text = re.sub(r'^' + name + r'=.*$', lambda m: name + '=' + value, text, flags=re.M)
text, count = re.subn(r'^EXAONE_TAG=.*$', lambda m: 'EXAONE_TAG=' + tag, text, flags=re.M)
if not count:
    text = text.rstrip() + '\nEXAONE_TAG=' + tag + '\n'
fd, temporary = tempfile.mkstemp(prefix='.env-', dir=str(path.parent))
try:
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, 'w') as output:
        output.write(text)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, str(path))
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
PY
cd "$root"
/usr/local/bin/docker-compose -p exaone -f docker-compose.yml -f docker-compose.synology.yml up -d --no-build
curl -fsS --max-time 10 http://127.0.0.1:61480/api/health
'@
    Send-NasScript $install "sudo -n sh -s -- '$tag' '$stage'"
} finally {
    Pop-Location
    if ($null -eq $oldTag) { Remove-Item Env:EXAONE_TAG -ErrorAction SilentlyContinue } else { $env:EXAONE_TAG = $oldTag }
}
