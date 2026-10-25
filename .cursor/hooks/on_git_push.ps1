[CmdletBinding()]
param(
    [string]$AutofixModel = "composer-2.5"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
    $PSNativeCommandUseErrorActionPreference = $false
}

function Ensure-Dir {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        New-Item -ItemType Directory -Path $Path -Force | Out-Null
    }
}

function Write-Log {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Message
    )
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-ddTHH:mm:ssK"), $Message
    Add-Content -LiteralPath $Path -Value $line
}

function Push-AppearsSuccessful {
    param([string]$OutputText)
    if ([string]::IsNullOrWhiteSpace($OutputText)) {
        return $false
    }

    $failurePatterns = @(
        "failed to push",
        "\[rejected\]",
        "error:",
        "fatal:"
    )

    foreach ($pattern in $failurePatterns) {
        if ($OutputText -match $pattern) {
            return $false
        }
    }

    return $true
}

$raw = [Console]::In.ReadToEnd()
if ([string]::IsNullOrWhiteSpace($raw)) {
    exit 0
}

$payload = $null
try {
    $payload = $raw | ConvertFrom-Json
}
catch {
    exit 0
}

# Does the command run "git push"? This is the same pattern as the "matcher" in .cursor/hooks.json: keep the two
# equal. "git push" must stand at the start of a statement: at the start of the command, or after ";", "&", "|",
# "(", "{" or a line break. Options such as "-C <dir>" and "git.exe" are fine. The words "git push" inside a quoted
# string or a note (for example a JSON payload in a test) do not count. A quoted text that follows ";" or a line
# break still counts.
$GitPushPattern = '(?:^|[;&\|\(\{\r\n])\s*git(?:\.exe)?(?:\s+-[Cc]\s+\S+)*\s+push(?:[\s;&\|\)\}]|$)'

$command = [string]$payload.command
if ($command -notmatch $GitPushPattern) {
    exit 0
}

# Read all of the output of git first. With "| Select-Object -First 1" the pipeline stops early, PowerShell
# can then end git and set $LASTEXITCODE to -1, and the check below could make the hook exit before it started the watcher.
$repoRootOut = & git rev-parse --show-toplevel 2>$null
$repoRootExit = $LASTEXITCODE
$repoRoot = ($repoRootOut | Select-Object -First 1)
if ($repoRootExit -ne 0 -or [string]::IsNullOrWhiteSpace($repoRoot)) {
    exit 0
}
$repoRoot = $repoRoot.Trim()

$hookRoot = Join-Path $repoRoot ".cursor/hooks"
$stateDir = Join-Path $hookRoot "state"
$logDir = Join-Path $hookRoot "logs"
Ensure-Dir -Path $stateDir
Ensure-Dir -Path $logDir

$logPath = Join-Path $logDir "push-hook.log"

if (-not (Push-AppearsSuccessful -OutputText ([string]$payload.output))) {
    Write-Log -Path $logPath -Message "Skip watcher: push command appears failed. cmd=[$command]"
    exit 0
}

Push-Location $repoRoot
try {
    $branch = (& git rev-parse --abbrev-ref HEAD | Select-Object -First 1).Trim()
    $sha = (& git rev-parse HEAD | Select-Object -First 1).Trim()
}
finally {
    Pop-Location
}

if ([string]::IsNullOrWhiteSpace($branch) -or $branch -eq "HEAD") {
    Write-Log -Path $logPath -Message "Skip watcher: detached HEAD."
    exit 0
}
if ([string]::IsNullOrWhiteSpace($sha)) {
    Write-Log -Path $logPath -Message "Skip watcher: empty HEAD sha."
    exit 0
}

$safeBranch = ($branch -replace "[^A-Za-z0-9._-]", "_")
$lockPath = Join-Path $stateDir "$safeBranch.lock.json"

if (Test-Path -LiteralPath $lockPath) {
    try {
        $existing = Get-Content -LiteralPath $lockPath -Raw | ConvertFrom-Json
        $existingPid = [int]$existing.pid
        $existingProcess = Get-Process -Id $existingPid -ErrorAction SilentlyContinue
        if ($null -ne $existingProcess) {
            Write-Log -Path $logPath -Message "Skip watcher: existing watcher pid=$existingPid branch=$branch (it follows the branch head, so it picks up sha=$sha)"
            exit 0
        }
    }
    catch {
        # Ignore malformed lock files and replace them.
    }
    Remove-Item -LiteralPath $lockPath -Force -ErrorAction SilentlyContinue
}

$watcherScript = Join-Path $hookRoot "watch_ci_and_autofix.ps1"
if (-not (Test-Path -LiteralPath $watcherScript)) {
    Write-Log -Path $logPath -Message "Skip watcher: watcher script missing at $watcherScript"
    exit 0
}

$proc = Start-Process `
    -FilePath "powershell.exe" `
    -ArgumentList @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", $watcherScript,
        "-RepoRoot", $repoRoot,
        "-Branch", $branch,
        "-HeadSha", $sha,
        "-LockFile", $lockPath,
        "-AutofixModel", $AutofixModel
    ) `
    -WindowStyle Hidden `
    -PassThru

$lock = [ordered]@{
    pid = $proc.Id
    branch = $branch
    head_sha = $sha
    started_at = (Get-Date).ToString("o")
} | ConvertTo-Json -Depth 4
Set-Content -LiteralPath $lockPath -Value $lock -Encoding UTF8

Write-Log -Path $logPath -Message "Started watcher pid=$($proc.Id) branch=$branch sha=$sha"
exit 0
