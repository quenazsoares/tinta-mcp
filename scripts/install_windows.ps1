#Requires -Version 5.1
<#
.SYNOPSIS
    One-command installer for Tinta (Inkscape MCP server) on Windows.

.DESCRIPTION
    Runs the full Windows setup in one step:
      1. Discovers your Inkscape install (registry, PATH, common locations)
      2. Copies the Tinta extension into Inkscape's user extensions directory
      3. Attempts to suppress Inkscape's first-run "Welcome" dialog by
         updating existing preferences when Inkscape is not running
      4. Creates the Python virtual environment and installs dependencies via uv
      5. Prints a ready-to-paste MCP client config snippet

    Safe to re-run after updating Tinta. The installer refreshes Tinta-owned
    extension files and checks whether optional setup is already complete.
#>

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir

function Write-Step($msg) { Write-Host ""; Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg) { Write-Host "    OK: $msg" -ForegroundColor Green }
function Write-Warn2($msg) { Write-Host "    WARNING: $msg" -ForegroundColor Yellow }
function Write-Err2($msg) { Write-Host "    ERROR: $msg" -ForegroundColor Red }

# --- Step 1: Find Inkscape -------------------------------------------------
Write-Step "Checking required tools"

function Find-InkscapeExe {
    $envOverride = $env:INKSCAPE_COMMAND
    if ($envOverride -and (Test-Path $envOverride)) { return $envOverride }

    $onPath = Get-Command inkscape.exe -ErrorAction SilentlyContinue
    if ($onPath) { return $onPath.Source }

    foreach ($hive in @("HKLM:", "HKCU:")) {
        $key = "$hive\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\inkscape.exe"
        if (Test-Path $key) {
            $val = (Get-ItemProperty -Path $key -ErrorAction SilentlyContinue).'(default)'
            if ($val -and (Test-Path $val)) { return $val }
        }
    }

    foreach ($candidate in @(
        "C:\Program Files\Inkscape\bin\inkscape.exe",
        "C:\Program Files (x86)\Inkscape\bin\inkscape.exe"
    )) {
        if (Test-Path $candidate) { return $candidate }
    }

    return $null
}

$inkscapeExe = Find-InkscapeExe
if (-not $inkscapeExe) {
    Write-Err2 "Could not find Inkscape."
    Write-Host "    Install it from https://inkscape.org/release/ first, or set the" -ForegroundColor Red
    Write-Host "    INKSCAPE_COMMAND environment variable to the full path of inkscape.exe" -ForegroundColor Red
    Write-Host "    and re-run this script." -ForegroundColor Red
    exit 1
}
$inkscapeBin = Split-Path -Parent $inkscapeExe
$inkscapeCom = Join-Path $inkscapeBin "inkscape.com"
Write-Ok "Found Inkscape at $inkscapeExe"

# --- Step 2: Extensions directory ------------------------------------------
Write-Step "Locating Inkscape's user extensions directory"

$userDataDir = ((& $inkscapeCom --user-data-directory 2>$null) | Select-Object -Last 1)
if ($userDataDir) { $userDataDir = $userDataDir.Trim() }
if (-not $userDataDir) {
    Write-Err2 "Could not determine Inkscape's user data directory. Is Inkscape installed correctly?"
    exit 1
}
$extensionsDir = Join-Path $userDataDir "extensions"
New-Item -ItemType Directory -Force -Path $extensionsDir | Out-Null
Write-Ok "Extensions directory: $extensionsDir"

# --- Step 3: Install the extension -----------------------------------------
Write-Step "Installing the Tinta extension"

# Clean legacy inkmcp / inkscape_mcp files if present
$legacyFiles = @(
    (Join-Path $extensionsDir "inkscape_mcp.py"),
    (Join-Path $extensionsDir "inkscape_mcp.inx")
)
foreach ($f in $legacyFiles) {
    if (Test-Path $f) { Remove-Item -Force $f }
}

$legacyInkmcp = Join-Path $extensionsDir "inkmcp"
if (Test-Path $legacyInkmcp) { Remove-Item -Recurse -Force $legacyInkmcp }

Copy-Item -Path (Join-Path $repoRoot "extension\tinta_mcp.py") -Destination $extensionsDir -Force
Copy-Item -Path (Join-Path $repoRoot "extension\tinta_mcp.inx") -Destination $extensionsDir -Force

$extOpsDir = Join-Path $extensionsDir "tinta_mcp_ops"
if (Test-Path $extOpsDir) { Remove-Item -Recurse -Force $extOpsDir }
New-Item -ItemType Directory -Force -Path $extOpsDir | Out-Null
Copy-Item -Path (Join-Path $repoRoot "extension\tinta_mcp_ops\*") -Destination $extOpsDir -Recurse -Force

Get-ChildItem -Path $extOpsDir -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

Write-Ok "Tinta extension installed to $extensionsDir"

# --- Step 4: Configure the first-run Welcome screen -------------------------
Write-Step "Configuring Inkscape's first-run Welcome screen"
Write-Host "    (the 'Quick Setup / Time to Draw' wizard owns the GTK event loop"
Write-Host "    until dismissed - an operation landing during it can hang or"
Write-Host "    crash Inkscape - see README)"

$versionOutput = & $inkscapeCom --version 2>$null
$versionMatch = [regex]::Match($versionOutput, 'Inkscape\s+(\d+\.\d+(?:\.\d+)?)')
$inkscapeVersion = if ($versionMatch.Success) { $versionMatch.Groups[1].Value } else { $null }
if (-not $inkscapeVersion) {
    Write-Warn2 "Could not determine Inkscape's version from '$versionOutput' - skipping this step."
    Write-Warn2 "You can dismiss the Welcome screen manually: uncheck 'Show this every time' on its"
    Write-Warn2 "'Time to Draw' tab and click New Document."
} else {
    $verAttr = "ver$inkscapeVersion"

    $inkscapeRunning = Get-Process -Name inkscape -ErrorAction SilentlyContinue
    if ($inkscapeRunning) {
        Write-Warn2 "Inkscape is currently running - it will overwrite preferences.xml on exit,"
        Write-Warn2 "so this step can't be applied safely right now."
        Write-Warn2 "Close Inkscape, then re-run this script to apply the Welcome-screen fix."
    } else {
        $prefsPath = Join-Path $userDataDir "preferences.xml"
        if (-not (Test-Path $prefsPath)) {
            Write-Warn2 "preferences.xml does not exist yet. Launch and close Inkscape once, then re-run this script to apply the Welcome-screen fix."
        }

        if (Test-Path $prefsPath) {
            [xml]$prefs = Get-Content -Path $prefsPath -Raw
            $bootNode = $prefs.SelectSingleNode("//group[@id='boot']")
            if (-not $bootNode) {
                Write-Warn2 "No existing boot preference group found - creating one."
                $bootNode = $prefs.CreateElement("group")
                $bootNode.SetAttribute("id", "boot") | Out-Null
                $prefs.DocumentElement.AppendChild($bootNode) | Out-Null
            }
            $shownNode = $bootNode.SelectSingleNode("group[@id='shown']")
            if (-not $shownNode) {
                $shownNode = $prefs.CreateElement("group")
                $shownNode.SetAttribute("id", "shown") | Out-Null
                $bootNode.AppendChild($shownNode) | Out-Null
            }

            $alreadyApplied = ($bootNode.GetAttribute("mode") -eq "1") -and ($shownNode.GetAttribute($verAttr) -eq "1")
            if (-not $alreadyApplied) {
                $stamp = Get-Date -Format yyyyMMdd-HHmmss
                Copy-Item -Path $prefsPath -Destination "$prefsPath.bak-install-$stamp" -Force
                if ($bootNode.HasAttribute("enabled")) { $bootNode.RemoveAttribute("enabled") | Out-Null }
                $bootNode.SetAttribute("mode", "1") | Out-Null
                $shownNode.SetAttribute($verAttr, "1") | Out-Null
                $prefs.Save($prefsPath)
                Write-Ok "Welcome screen disabled for Inkscape $inkscapeVersion (backup saved as preferences.xml.bak-install-$stamp)"
            } else {
                Write-Ok "Welcome screen already disabled for Inkscape $inkscapeVersion"
            }
        } else {
            Write-Warn2 "Could not create preferences.xml automatically."
            Write-Warn2 "Launch Inkscape once manually, close it, then re-run this script."
        }
    }
}

# --- Step 5: Python dependencies and global tool via uv --------------------
Write-Step "Setting up Python environment with uv"
$toolInstalled = $false
if (Get-Command uv -ErrorAction SilentlyContinue) {
    Set-Location $repoRoot
    uv sync
    if ($LASTEXITCODE -ne 0) {
        Write-Err2 "uv sync failed with exit code $LASTEXITCODE. Fix the environment and re-run the installer."
        exit $LASTEXITCODE
    }
    Write-Ok "Environment synchronized via uv (.venv)"

    Write-Host "Installing tinta as a global CLI tool via 'uv tool install'..." -ForegroundColor Cyan
    try {
        uv tool install . --force
        if ($LASTEXITCODE -eq 0) {
            $toolInstalled = $true
            Write-Ok "tinta command is now globally available on your system"
        } else {
            Write-Warn2 "uv tool install failed with exit code $LASTEXITCODE, fallback to local uv run."
        }
    } catch {
        Write-Warn2 "Could not install global tool via 'uv tool install', fallback to local uv run."
    }
} else {
    Write-Warn2 "uv is not installed on PATH. Please install uv (irm https://astral.sh/uv/install.ps1 | iex)."
}

# --- Step 6: Install AI Agent Skill (optional) ------------------------------
Write-Step "Checking for AI agent skill directories"
$candidateSkillDirs = @(
    (Join-Path $env:USERPROFILE ".gemini\config\skills"),
    (Join-Path $env:USERPROFILE ".claude\skills"),
    (Join-Path $env:USERPROFILE ".cursor\skills")
)
$sourceSkillDir = Join-Path $repoRoot ".agents\skills\tinta-designer"
if (-not (Test-Path $sourceSkillDir)) {
    $sourceSkillDir = Join-Path $repoRoot "skills\tinta-designer"
}

if (Test-Path $sourceSkillDir) {
    foreach ($cand in $candidateSkillDirs) {
        $parent = Split-Path -Parent $cand
        if (Test-Path $parent) {
            $answer = Read-Host "Detected AI agent directory ($cand). Install the 'tinta-designer' skill here? [Y/n] "
            if ($answer -eq "" -or $answer -match "^[yYsS]") {
                $targetSkill = Join-Path $cand "tinta-designer"
                if (Test-Path $targetSkill) { Remove-Item -Recurse -Force $targetSkill }
                New-Item -ItemType Directory -Force -Path $targetSkill | Out-Null
                Copy-Item -Path (Join-Path $sourceSkillDir "*") -Destination $targetSkill -Recurse -Force
                Write-Ok "Installed tinta-designer skill to $targetSkill"
            } else {
                Write-Host "    Skipped skill installation for $cand" -ForegroundColor DarkGray
            }
        }
    }
}

# --- Step 7: Print MCP config snippet ---------------------------------------
Write-Step "MCP client configuration"

$escapedRepoRoot = $repoRoot -replace '\\', '\\'
if ($toolInstalled) {
    $snippet = @"
{
  "mcpServers": {
    "tinta": {
      "command": "tinta"
    }
  }
}
"@
} else {
    $snippet = @"
{
  "mcpServers": {
    "tinta": {
      "command": "uv",
      "args": [
        "--directory",
        "$escapedRepoRoot",
        "run",
        "tinta"
      ]
    }
  }
}
"@
}
$configFile = Join-Path $repoRoot "mcp_config.snippet.json"
$snippet | Out-File -FilePath $configFile -Encoding utf8 -Force

Write-Host ""
Write-Host $snippet -ForegroundColor White
Write-Host ""
Write-Ok "Configuration saved to $configFile"
Write-Host "    Paste the above JSON into your AI client's MCP configuration (Claude Desktop," -ForegroundColor Cyan
Write-Host "    Claude Code, Antigravity, Cursor, etc.)." -ForegroundColor Cyan

# --- Step 8: Verify -----------------------------------------------------------
Write-Step "Verifying Tinta extension reachability"

$inkscapeNowRunning = Get-Process -Name inkscape -ErrorAction SilentlyContinue
if ($inkscapeNowRunning) {
    $gdbusExe = Join-Path $inkscapeBin "gdbus.exe"
    $listOutput = & $gdbusExe call --session --dest org.inkscape.Inkscape --object-path /org/inkscape/Inkscape --method org.gtk.Actions.List 2>$null
    if ($listOutput -match "org\.tinta\.mcp") {
        Write-Ok "Tinta extension detected on the running Inkscape instance and is ready to use."
    } else {
        Write-Warn2 "Extension action org.tinta.mcp not detected on the currently running Inkscape instance."
        Write-Warn2 "Restart Inkscape to pick up the newly installed extension."
    }
} else {
    Write-Host "    Inkscape is not currently running. Start Inkscape with a document open to use Tinta." -ForegroundColor White
}

Write-Host ""
Write-Host "Setup complete." -ForegroundColor Green
