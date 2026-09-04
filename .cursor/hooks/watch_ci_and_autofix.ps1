[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$RepoRoot,
    [Parameter(Mandatory = $true)][string]$Branch,
    [Parameter(Mandatory = $true)][string]$HeadSha,
    [Parameter(Mandatory = $true)][string]$LockFile,
    [int]$MaxAttempts = 2,
    [int]$PollIntervalSeconds = 20,
    [int]$DiscoveryTimeoutMinutes = 15,
    [int]$RunTimeoutMinutes = 60
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
    $PSNativeCommandUseErrorActionPreference = $false
}

$SafeBranch = ($Branch -replace "[^A-Za-z0-9._-]", "_")
$StateDir = Join-Path $RepoRoot ".cursor/hooks/state"
$LogDir = Join-Path $RepoRoot ".cursor/hooks/logs"
$WorktreeBaseDir = Join-Path $RepoRoot ".cursor/hooks/worktrees"
$Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$LogPath = Join-Path $LogDir ("ci-autofix-{0}-{1}.log" -f $SafeBranch, $Timestamp)

function Ensure-Dir {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        New-Item -ItemType Directory -Path $Path -Force | Out-Null
    }
}

Ensure-Dir -Path $StateDir
Ensure-Dir -Path $LogDir
Ensure-Dir -Path $WorktreeBaseDir

function Write-Log {
    param([Parameter(Mandatory = $true)][string]$Message)
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-ddTHH:mm:ssK"), $Message
    Add-Content -LiteralPath $LogPath -Value $line
}

function Write-LockState {
    param(
        [Parameter(Mandatory = $true)][string]$Status,
        [Parameter(Mandatory = $true)][string]$CurrentSha,
        [int]$Attempt = 0
    )
    $payload = [ordered]@{
        pid = $PID
        branch = $Branch
        status = $Status
        current_sha = $CurrentSha
        attempt = $Attempt
        updated_at = (Get-Date).ToString("o")
    } | ConvertTo-Json -Depth 4
    Set-Content -LiteralPath $LockFile -Value $payload -Encoding UTF8
}

function Invoke-External {
    param(
        [Parameter(Mandatory = $true)][string]$Exe,
        [Parameter(Mandatory = $true)][string[]]$Args,
        [Parameter(Mandatory = $true)][string]$Cwd,
        [bool]$AllowFailure = $false
    )

    $oldEap = $ErrorActionPreference
    Push-Location $Cwd
    try {
        $ErrorActionPreference = "Continue"
        $raw = & $Exe @Args 2>&1
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $oldEap
        Pop-Location
    }

    $output = ""
    if ($null -ne $raw) {
        $output = ($raw | Out-String).TrimEnd()
    }

    if ($exitCode -ne 0 -and -not $AllowFailure) {
        $joined = ($Args -join " ")
        throw "Command failed: $Exe $joined (exit=$exitCode)`n$output"
    }

    return [pscustomobject]@{
        ExitCode = $exitCode
        Output = $output
    }
}

function Get-CiRunForSha {
    param([Parameter(Mandatory = $true)][string]$Sha)

    $deadline = (Get-Date).AddMinutes($DiscoveryTimeoutMinutes)
    while ((Get-Date) -lt $deadline) {
        $runsRes = Invoke-External `
            -Exe "gh" `
            -Args @(
                "run", "list",
                "--workflow", "CI",
                "--branch", $Branch,
                "--json", "databaseId,headSha,status,conclusion,workflowName,createdAt,url",
                "--limit", "100"
            ) `
            -Cwd $RepoRoot

        $runs = @()
        if (-not [string]::IsNullOrWhiteSpace($runsRes.Output)) {
            $runs = $runsRes.Output | ConvertFrom-Json
        }

        $matched = $runs |
            Where-Object { $_.headSha -eq $Sha -and $_.workflowName -eq "CI" } |
            Sort-Object -Property createdAt -Descending |
            Select-Object -First 1

        if ($null -ne $matched) {
            Write-Log ("Matched CI run for sha={0}: runId={1}" -f $Sha, $matched.databaseId)
            return $matched
        }

        Write-Log ("No CI run yet for sha={0}; sleeping {1}s" -f $Sha, $PollIntervalSeconds)
        Start-Sleep -Seconds $PollIntervalSeconds
    }

    throw "Timed out waiting for CI run for sha=$Sha on branch=$Branch"
}

function Wait-CiRunCompletion {
    param([Parameter(Mandatory = $true)][string]$RunId)

    $deadline = (Get-Date).AddMinutes($RunTimeoutMinutes)
    while ((Get-Date) -lt $deadline) {
        $viewRes = Invoke-External `
            -Exe "gh" `
            -Args @("run", "view", $RunId, "--json", "status,conclusion,url,headSha,workflowName,updatedAt") `
            -Cwd $RepoRoot
        $view = $viewRes.Output | ConvertFrom-Json

        if ($view.status -eq "completed") {
            Write-Log ("Run completed. runId={0} conclusion={1}" -f $RunId, $view.conclusion)
            return $view
        }

        Write-Log ("Run in progress. runId={0} status={1}; sleeping {2}s" -f $RunId, $view.status, $PollIntervalSeconds)
        Start-Sleep -Seconds $PollIntervalSeconds
    }

    throw "Timed out waiting CI completion for runId=$RunId"
}

function Run-LocalChecks {
    param([Parameter(Mandatory = $true)][string]$WorkingDir)

    $ruffPath = Join-Path $RepoRoot ".venv/Scripts/ruff.exe"
    $pythonPath = Join-Path $RepoRoot ".venv/Scripts/python.exe"

    if (-not (Test-Path -LiteralPath $ruffPath)) {
        throw "Missing ruff executable at $ruffPath"
    }
    if (-not (Test-Path -LiteralPath $pythonPath)) {
        throw "Missing python executable at $pythonPath"
    }

    $commands = @(
        @{ Exe = $ruffPath; Args = @("check", "."); Name = "ruff check" },
        @{ Exe = $ruffPath; Args = @("format", "--check", "."); Name = "ruff format --check" },
        @{ Exe = $pythonPath; Args = @("-m", "pytest", "-v"); Name = "pytest -v" }
    )

    foreach ($cmd in $commands) {
        Write-Log ("Running local check: {0}" -f $cmd.Name)
        $res = Invoke-External -Exe $cmd.Exe -Args $cmd.Args -Cwd $WorkingDir -AllowFailure $true
        if (-not [string]::IsNullOrWhiteSpace($res.Output)) {
            Write-Log ("{0} output:`n{1}" -f $cmd.Name, $res.Output)
        }
        if ($res.ExitCode -ne 0) {
            throw "Local check failed: $($cmd.Name)"
        }
    }
}

function Cleanup-Worktree {
    param([Parameter(Mandatory = $true)][string]$WorktreeDir)

    if (Test-Path -LiteralPath $WorktreeDir) {
        $rmRes = Invoke-External `
            -Exe "git" `
            -Args @("worktree", "remove", "--force", $WorktreeDir) `
            -Cwd $RepoRoot `
            -AllowFailure $true
        if ($rmRes.ExitCode -ne 0) {
            Write-Log ("git worktree remove failed; forcing folder deletion: {0}" -f $WorktreeDir)
            Remove-Item -LiteralPath $WorktreeDir -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

function Run-AutofixAttempt {
    param(
        [Parameter(Mandatory = $true)][string]$CurrentSha,
        [Parameter(Mandatory = $true)][string]$RunId,
        [Parameter(Mandatory = $true)][int]$Attempt
    )

    $worktreeDir = Join-Path $WorktreeBaseDir ("{0}-attempt{1}-{2}" -f $SafeBranch, $Attempt, (Get-Date -Format "yyyyMMddHHmmss"))

    $null = Invoke-External `
        -Exe "git" `
        -Args @("worktree", "add", "--force", "--detach", $worktreeDir, $CurrentSha) `
        -Cwd $RepoRoot
    Write-Log ("Created detached worktree: {0}" -f $worktreeDir)

    try {
        $failedLogRes = Invoke-External `
            -Exe "gh" `
            -Args @("run", "view", $RunId, "--log-failed") `
            -Cwd $RepoRoot `
            -AllowFailure $true

        $failureLogPath = Join-Path $worktreeDir (".ci-failed-run-{0}.log" -f $RunId)
        if (-not [string]::IsNullOrWhiteSpace($failedLogRes.Output)) {
            Set-Content -LiteralPath $failureLogPath -Value $failedLogRes.Output -Encoding UTF8
        }
        else {
            $fallback = Invoke-External -Exe "gh" -Args @("run", "view", $RunId) -Cwd $RepoRoot -AllowFailure $true
            Set-Content -LiteralPath $failureLogPath -Value $fallback.Output -Encoding UTF8
        }

        $ruffPath = Join-Path $RepoRoot ".venv/Scripts/ruff.exe"
        $pythonPath = Join-Path $RepoRoot ".venv/Scripts/python.exe"

        $prompt = @"
You are fixing a GitHub Actions CI failure in the current repository checkout.
Target branch: $Branch
Target SHA: $CurrentSha
Failed run id: $RunId

Read failure logs from:
$failureLogPath

Requirements:
1) Identify the root cause from the CI logs and apply minimal code changes.
2) Run and pass these local checks:
   - $ruffPath check .
   - $ruffPath format --check .
   - $pythonPath -m pytest -v
3) If checks fail, continue fixing until all pass.
4) Do NOT run git commit, git push, create branch, or open PR.
5) Keep unrelated files unchanged.
Return a short summary of what was fixed.
"@

        Write-Log ("Starting agent autofix attempt={0}" -f $Attempt)
        $agentRes = Invoke-External `
            -Exe "agent" `
            -Args @("-p", $prompt, "--output-format", "text", "--force") `
            -Cwd $worktreeDir `
            -AllowFailure $true

        $agentLogPath = Join-Path $LogDir ("agent-output-{0}-attempt{1}.log" -f $SafeBranch, $Attempt)
        Set-Content -LiteralPath $agentLogPath -Value $agentRes.Output -Encoding UTF8
        if ($agentRes.ExitCode -ne 0) {
            throw "Agent CLI failed on attempt=$Attempt. See $agentLogPath"
        }

        Run-LocalChecks -WorkingDir $worktreeDir

        $statusRes = Invoke-External -Exe "git" -Args @("status", "--porcelain") -Cwd $worktreeDir
        if ([string]::IsNullOrWhiteSpace($statusRes.Output)) {
            throw "Autofix attempt produced no file changes."
        }

        $null = Invoke-External -Exe "git" -Args @("add", "-A") -Cwd $worktreeDir
        $commitMessage = "fix(ci): autofix run $RunId attempt $Attempt"
        $commitRes = Invoke-External -Exe "git" -Args @("commit", "-m", $commitMessage) -Cwd $worktreeDir -AllowFailure $true
        if ($commitRes.ExitCode -ne 0) {
            throw "git commit failed in worktree."
        }

        $pushRes = Invoke-External -Exe "git" -Args @("push", "origin", "HEAD:$Branch") -Cwd $worktreeDir -AllowFailure $true
        if ($pushRes.ExitCode -ne 0) {
            throw "git push failed in worktree."
        }

        $newShaRes = Invoke-External -Exe "git" -Args @("rev-parse", "HEAD") -Cwd $worktreeDir
        $newSha = $newShaRes.Output.Trim()
        if ([string]::IsNullOrWhiteSpace($newSha)) {
            throw "Unable to read new sha after autofix push."
        }

        Write-Log ("Autofix pushed new commit sha={0}" -f $newSha)
        return $newSha
    }
    finally {
        Cleanup-Worktree -WorktreeDir $worktreeDir
    }
}

try {
    Write-Log ("Watcher start: branch={0} sha={1} pid={2}" -f $Branch, $HeadSha, $PID)
    Write-LockState -Status "starting" -CurrentSha $HeadSha -Attempt 0

    if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
        throw "gh CLI is not installed. Install GitHub CLI first."
    }
    if (-not (Get-Command agent -ErrorAction SilentlyContinue)) {
        throw "agent CLI is not installed. Install Cursor CLI first."
    }

    $authRes = Invoke-External -Exe "gh" -Args @("auth", "status") -Cwd $RepoRoot -AllowFailure $true
    if ($authRes.ExitCode -ne 0 -or $authRes.Output -match "not logged into any GitHub hosts") {
        throw "gh is not authenticated. Run: gh auth login"
    }

    $currentSha = $HeadSha
    $attempt = 0

    while ($true) {
        Write-LockState -Status "watching" -CurrentSha $currentSha -Attempt $attempt

        $run = Get-CiRunForSha -Sha $currentSha
        $runId = [string]$run.databaseId
        $final = Wait-CiRunCompletion -RunId $runId

        if ($final.conclusion -eq "success") {
            Write-Log ("CI success for sha={0}; no autofix needed." -f $currentSha)
            break
        }

        if ($attempt -ge $MaxAttempts) {
            Write-Log ("CI failed with conclusion={0}, max attempts reached ({1})." -f $final.conclusion, $MaxAttempts)
            break
        }

        $attempt += 1
        Write-LockState -Status "autofixing" -CurrentSha $currentSha -Attempt $attempt
        Write-Log ("CI failed (run={0}, conclusion={1}), starting autofix attempt {2}/{3}" -f $runId, $final.conclusion, $attempt, $MaxAttempts)

        $currentSha = Run-AutofixAttempt -CurrentSha $currentSha -RunId $runId -Attempt $attempt
        Write-Log ("Autofix attempt complete; monitoring new sha={0}" -f $currentSha)
    }
}
catch {
    Write-Log ("Watcher error: {0}" -f $_.Exception.Message)
}
finally {
    try {
        if (Test-Path -LiteralPath $LockFile) {
            Remove-Item -LiteralPath $LockFile -Force -ErrorAction SilentlyContinue
        }
    }
    catch {
        # Ignore lock cleanup failures.
    }
    Write-Log "Watcher finished."
}

exit 0
