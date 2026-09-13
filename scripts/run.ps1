# Important design constraint:

# This wrapper intentionally avoids any kind of global process cleanup.

# We do NOT kill processes by name (python/node/1C/etc.)
# and we do NOT try to detect "related" processes via command line patterns.

# The only supported cleanup mechanism is terminating the process tree
# of the started child process.

# If something survives outside that tree, it is considered a bug in how
# the process was started — not a reason to add broader cleanup logic.

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Command,

    [int]$TimeoutSec = 900,

    [string]$WorkingDirectory = "",

    [string]$LogDir = ".artifacts\test-logs",

    [switch]$UseWindowsPowerShell
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Section {
    param([Parameter(Mandatory = $true)][string]$Text)
    Write-Host ""
    Write-Host "==== $Text ===="
}

function Ensure-Directory {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        New-Item -ItemType Directory -Path $Path -Force | Out-Null
    }
}

function Stop-ProcessTree {
    param(
        [Parameter(Mandatory = $true)]
        [int]$Pid
    )

    Write-Host "Stopping process tree for PID=$Pid"
    & taskkill /PID $Pid /T /F | Out-Null
    $taskkillExit = $LASTEXITCODE

    if ($taskkillExit -ne 0) {
        Write-Warning "taskkill returned exit code $taskkillExit for PID=$Pid"
        return $false
    }

    return $true
}

function Get-ShellExecutable {
    param([switch]$UseWindowsPowerShell)

    if ($UseWindowsPowerShell) {
        return "powershell.exe"
    }

    $pwsh = Get-Command pwsh.exe -ErrorAction SilentlyContinue
    if ($pwsh) {
        return $pwsh.Source
    }

    return "powershell.exe"
}

function New-ChildScriptFile {
    param(
        [Parameter(Mandatory = $true)][string]$ScriptContent,
        [Parameter(Mandatory = $true)][string]$Directory,
        [Parameter(Mandatory = $true)][string]$Timestamp
    )

    $tempScriptPath = Join-Path $Directory "run-$Timestamp.child.ps1"

    $childScript = @"
`$ErrorActionPreference = 'Stop'
$ScriptContent
if (`$null -ne `$LASTEXITCODE -and `$LASTEXITCODE -ne 0) {
    exit [int]`$LASTEXITCODE
}
"@

    Set-Content -LiteralPath $tempScriptPath -Value $childScript -Encoding UTF8
    return $tempScriptPath
}

$repoRoot = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($WorkingDirectory)) {
    $WorkingDirectory = $repoRoot
}

Ensure-Directory -Path $LogDir

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$stdoutLog = Join-Path $LogDir "run-$timestamp.stdout.log"
$stderrLog = Join-Path $LogDir "run-$timestamp.stderr.log"
$metaLog   = Join-Path $LogDir "run-$timestamp.meta.log"
$lockPath  = Join-Path $LogDir "active.lock"

if (Test-Path -LiteralPath $lockPath) {
    throw "Wrapper lock file exists: $lockPath . A previous run may still be active."
}

New-Item -ItemType File -Path $lockPath -Force | Out-Null

$childExitCode = 1
$timedOut = $false
$process = $null
$tempScriptPath = $null
$cleanupWarnings = New-Object System.Collections.Generic.List[string]
$startedAt = Get-Date

# Without this, Python child processes buffer stdout into the log file, and a
# long run shows no progress until it ends.
$env:PYTHONUNBUFFERED = "1"

try {
    Write-Section "Wrapper configuration"
    Write-Host "WorkingDirectory: $WorkingDirectory"
    Write-Host "TimeoutSec:       $TimeoutSec"
    Write-Host "StdoutLog:        $stdoutLog"
    Write-Host "StderrLog:        $stderrLog"
    Write-Host "MetaLog:          $metaLog"

    $shellExe = Get-ShellExecutable -UseWindowsPowerShell:$UseWindowsPowerShell

    $tempScriptPath = New-ChildScriptFile `
        -ScriptContent $Command `
        -Directory $LogDir `
        -Timestamp $timestamp

    @(
        "Timestamp: $(Get-Date -Format s)"
        "WorkingDirectory: $WorkingDirectory"
        "Shell: $shellExe"
        "TimeoutSec: $TimeoutSec"
        "ChildScript: $tempScriptPath"
        "Command:"
        $Command
    ) | Set-Content -LiteralPath $metaLog -Encoding UTF8

    $arguments = @(
        "-NoLogo"
        "-NoProfile"
        "-NonInteractive"
        "-ExecutionPolicy", "Bypass"
        "-File", $tempScriptPath
    )

    Write-Section "Starting child process"
    $process = Start-Process `
        -FilePath $shellExe `
        -ArgumentList $arguments `
        -WorkingDirectory $WorkingDirectory `
        -RedirectStandardOutput $stdoutLog `
        -RedirectStandardError $stderrLog `
        -PassThru `
        -ErrorAction Stop

    Write-Host "Started PID=$($process.Id)"

    $finished = $process.WaitForExit($TimeoutSec * 1000)

    if (-not $finished) {
        $timedOut = $true
        Write-Warning "Command timed out after $TimeoutSec seconds."

        $killed = Stop-ProcessTree -Pid $process.Id
        if (-not $killed) {
            $cleanupWarnings.Add("Failed to fully kill child process tree for PID=$($process.Id)")
        }

        Start-Sleep -Seconds 2
    }

    try {
        $process.Refresh()
    }
    catch {
    }

    if ($timedOut) {
        $childExitCode = 124
    }
    else {
        $childExitCode = $process.ExitCode
    }

    Write-Section "Run result"
    Write-Host "TimedOut:  $timedOut"
    Write-Host "ExitCode:  $childExitCode"
}
catch {
    $childExitCode = 1
    Write-Error "Wrapper run failed: $($_.Exception.Message)"
    try {
        Add-Content -LiteralPath $stderrLog -Value ("Wrapper failure: " + $_.Exception.ToString()) -Encoding UTF8
    }
    catch {
        Write-Warning "Could not append wrapper failure to stderr log."
    }
}
finally {
    Write-Section "Cleanup"

    # Written before the lock is removed, so an observer never sees a run that
    # is neither active nor finished. summarize-run-log.py reads this section.
    try {
        @(
            "---- Result ----"
            "ExitCode: $childExitCode"
            "TimedOut: $timedOut"
            "DurationSec: $([int]((Get-Date) - $startedAt).TotalSeconds)"
            "Finished: $(Get-Date -Format s)"
        ) | Add-Content -LiteralPath $metaLog -Encoding UTF8
    }
    catch {
        $cleanupWarnings.Add("Could not append run result to meta log: $metaLog")
    }

    if ($null -ne $tempScriptPath -and (Test-Path -LiteralPath $tempScriptPath)) {
        try {
            Remove-Item -LiteralPath $tempScriptPath -Force -ErrorAction Stop
        }
        catch {
            $cleanupWarnings.Add("Could not remove temp child script: $tempScriptPath")
        }
    }

    if (Test-Path -LiteralPath $lockPath) {
        try {
            Remove-Item -LiteralPath $lockPath -Force -ErrorAction Stop
        }
        catch {
            $cleanupWarnings.Add("Could not remove lock file: $lockPath")
        }
    }

    if ($cleanupWarnings.Count -gt 0) {
        Write-Warning "Cleanup warnings:"
        foreach ($w in $cleanupWarnings) {
            Write-Warning " - $w"
        }

        try {
            Add-Content -LiteralPath $metaLog -Value "" -Encoding UTF8
            Add-Content -LiteralPath $metaLog -Value "Cleanup warnings:" -Encoding UTF8
            foreach ($w in $cleanupWarnings) {
                Add-Content -LiteralPath $metaLog -Value " - $w" -Encoding UTF8
            }
        }
        catch {
            Write-Warning "Could not append cleanup warnings to meta log."
        }
    }
}

# A compact verdict (result, prioritized primary errors, short tails) instead of
# raw tails: an agent reads it first and opens the full logs only if needed.
# The summarizer is shared workspace tooling in Others/dev-utils; without it the
# wrapper falls back to plain tails.
$devUtilsRoot = if ($env:DEV_UTILS_ROOT) { $env:DEV_UTILS_ROOT } else { Join-Path $repoRoot "..\..\Others\dev-utils" }
$summarizer = Join-Path $devUtilsRoot "summarize-run-log.py"
$python = Get-Command python.exe -ErrorAction SilentlyContinue
$summarized = $false

if ($python -and (Test-Path -LiteralPath $summarizer)) {
    Write-Host ""
    $previousEncoding = [Console]::OutputEncoding
    $env:PYTHONIOENCODING = "utf-8"
    try {
        [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
        & $python.Source $summarizer $metaLog
        $summarized = ($LASTEXITCODE -eq 0)
    }
    catch {
        Write-Warning "summarize-run-log.py failed: $($_.Exception.Message)"
    }
    finally {
        [Console]::OutputEncoding = $previousEncoding
    }
}

if (-not $summarized) {
    Write-Section "Log tails"

    if (Test-Path -LiteralPath $stdoutLog) {
        Write-Host "--- stdout tail ---"
        Get-Content -LiteralPath $stdoutLog -Tail 30 -ErrorAction SilentlyContinue
    }

    if (Test-Path -LiteralPath $stderrLog) {
        Write-Host "--- stderr tail ---"
        Get-Content -LiteralPath $stderrLog -Tail 30 -ErrorAction SilentlyContinue
    }
}

exit $childExitCode
