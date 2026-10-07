$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$Tools = Join-Path $Root 'tools'
$Projects = Join-Path $Root 'projects'
$DefaultModel = 'gpt-oss:20b'
$Model = $DefaultModel
$ConfigDir = Join-Path $env:LOCALAPPDATA 'LocalCodexAI'
$ConfigFile = Join-Path $ConfigDir 'config.json'
$CodexPrefix = Join-Path $Tools 'npm-global'
$CodexBin = Join-Path $env:LOCALAPPDATA 'Programs\OpenAI\Codex\bin'
$NodeRoot = $null
$OllamaExe = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'

function Add-UserPath([string[]]$Paths) {
    $parts = @([Environment]::GetEnvironmentVariable('Path', 'User') -split ';' | Where-Object { $_ })
    foreach ($item in $Paths) {
        if ($item -and (Test-Path -LiteralPath $item) -and $parts -notcontains $item) { $parts = @($parts) + $item }
        if ($item -and (Test-Path -LiteralPath $item) -and (($env:Path -split ';') -notcontains $item)) { $env:Path = $item + ';' + $env:Path }
    }
    [Environment]::SetEnvironmentVariable('Path', ($parts -join ';'), 'User')
}

function Invoke-Checked([string]$File, [string[]]$Arguments) {
    & $File @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed (exit $LASTEXITCODE): $File $($Arguments -join ' ')" }
}

function Install-Node {
    New-Item -ItemType Directory -Force -Path $Tools | Out-Null
    $existing = Get-ChildItem -LiteralPath $Tools -Directory -ErrorAction SilentlyContinue | Where-Object { Test-Path (Join-Path $_.FullName 'node.exe') } | Sort-Object Name -Descending | Select-Object -First 1
    if ($existing) { $script:NodeRoot = $existing.FullName }
    else {
        Write-Host 'Installing Node.js LTS...'
        $release = Invoke-RestMethod 'https://nodejs.org/dist/index.json' | Where-Object { $_.lts -and $_.files -contains 'win-x64-zip' } | Select-Object -First 1
        if (-not $release) { throw 'Could not retrieve Node.js LTS release information.' }
        $version = $release.version.TrimStart('v')
        $zip = Join-Path $Tools "node-$version-win-x64.zip"
        Invoke-WebRequest "https://nodejs.org/dist/v$version/node-v$version-win-x64.zip" -OutFile $zip -UseBasicParsing
        Expand-Archive -LiteralPath $zip -DestinationPath $Tools -Force
        Remove-Item -LiteralPath $zip -Force
        $script:NodeRoot = Join-Path $Tools "node-v$version-win-x64"
    }
    $npmCli = Join-Path $script:NodeRoot 'node_modules\npm\bin\npm-cli.js'
    if (-not (Test-Path $npmCli)) { throw 'npm was not found in the Node.js package.' }
    @('@echo off', '"%~dp0node.exe" "%~dp0node_modules\npm\bin\npm-cli.js" %*') | Set-Content -LiteralPath (Join-Path $script:NodeRoot 'npm.cmd') -Encoding ASCII
    @('@echo off', '"%~dp0node.exe" "%~dp0node_modules\npm\bin\npx-cli.js" %*') | Set-Content -LiteralPath (Join-Path $script:NodeRoot 'npx.cmd') -Encoding ASCII
    Add-UserPath @($script:NodeRoot, $CodexPrefix)
}

function Install-CodexCli {
    $npmManaged = Join-Path $CodexPrefix 'codex.cmd'
    if (Test-Path $npmManaged) {
        Add-UserPath @($CodexPrefix)
        Invoke-Checked $npmManaged @('--version')
        return
    }
    $known = Get-Command codex -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($known) {
        $script:CodexBin = Split-Path $known.Source
        Invoke-Checked $known.Source @('--version')
        return
    }
    $codexExe = Join-Path $CodexBin 'codex.exe'
    if (-not (Test-Path $codexExe)) {
        Write-Host 'Installing Codex CLI...'
        $installer = Invoke-RestMethod 'https://chatgpt.com/codex/install.ps1'
        Invoke-Expression $installer
    }
    if (-not (Test-Path $codexExe)) { throw 'Codex CLI installation could not be verified.' }
    Add-UserPath @($CodexBin)
    Invoke-Checked $codexExe @('--version')
}

function Install-Ollama {
    $known = Get-Command ollama.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($known) { $script:OllamaExe = $known.Source; return }
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'),
        (Join-Path $env:ProgramFiles 'Ollama\ollama.exe')
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) { $script:OllamaExe = $candidate; return }
    }
    Write-Host 'Installing Ollama...'
    $installerScript = Invoke-RestMethod 'https://ollama.com/install.ps1'
    Invoke-Expression $installerScript
    $known = Get-Command ollama.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($known) { $script:OllamaExe = $known.Source }
    if (-not (Test-Path $script:OllamaExe)) { throw 'Ollama installation could not be verified.' }
    Add-UserPath @((Split-Path $script:OllamaExe))
}

function Install-PowerShellShim {
    # Local models often call "powershell" even when only PowerShell 7 (pwsh) exists.
    # Create tools\shim\powershell.cmd and put it first on PATH for this session.
    $target = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    if (-not (Test-Path -LiteralPath $target)) {
        $target = $null
        $found = Get-Command pwsh.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($found) { $target = $found.Source }
        if (-not $target) {
            $candidate = Join-Path $env:ProgramFiles 'PowerShell\7\pwsh.exe'
            if (Test-Path -LiteralPath $candidate) { $target = $candidate }
        }
    }
    if (-not $target) { Write-Host 'Warning: no PowerShell executable found for the powershell shim.'; return }
    $shimDir = Join-Path $Tools 'shim'
    New-Item -ItemType Directory -Force -Path $shimDir | Out-Null
    $shimFile = Join-Path $shimDir 'powershell.cmd'
    @('@echo off', "`"$target`" %*") | Set-Content -LiteralPath $shimFile -Encoding ASCII
    if (($env:Path -split ';') -notcontains $shimDir) { $env:Path = $shimDir + ';' + $env:Path }
}

function Start-OllamaAndModel {
    $env:OLLAMA_CONTEXT_LENGTH = '65536'
    [Environment]::SetEnvironmentVariable('OLLAMA_CONTEXT_LENGTH', '65536', 'User')
    $ready = $false
    try { $null = Invoke-RestMethod 'http://localhost:11434/api/tags' -TimeoutSec 3; $ready = $true } catch {
        Start-Process -FilePath $script:OllamaExe -ArgumentList 'serve' -WindowStyle Hidden
    }
    for ($i = 0; -not $ready -and $i -lt 90; $i++) {
        Start-Sleep -Seconds 1
        try { $null = Invoke-RestMethod 'http://localhost:11434/api/tags' -TimeoutSec 2; $ready = $true } catch {}
    }
    if (-not $ready) { throw 'Could not start the Ollama server.' }
    $tags = Invoke-RestMethod 'http://localhost:11434/api/tags' -TimeoutSec 5
    if ($tags.models.name -notcontains $Model) {
        Write-Host "Downloading model $Model..."
        Invoke-Checked $script:OllamaExe @('pull', $Model)
    }
}

function Set-Model([string]$SelectedModel) {
    if ($SelectedModel -notmatch '^[a-zA-Z0-9][a-zA-Z0-9._:/-]*$') {
        throw "Invalid Ollama model name: $SelectedModel"
    }
    $script:Model = $SelectedModel
    New-Item -ItemType Directory -Force -Path $ConfigDir | Out-Null
    @{ model = $script:Model } | ConvertTo-Json | Set-Content -LiteralPath $ConfigFile -Encoding UTF8
}

function Select-Model {
    $recommendations = @(
        [pscustomobject]@{ Model = 'gpt-oss:20b'; Label = 'Strong general coding (about 13 GB)' },
        [pscustomobject]@{ Model = 'qwen3-coder:30b'; Label = 'Coding-focused, needs more memory (about 19 GB)' },
        [pscustomobject]@{ Model = 'qwen3:8b'; Label = 'Smaller general model (about 5 GB)' },
        [pscustomobject]@{ Model = 'deepseek-r1:8b'; Label = 'Reasoning-focused, smaller option (about 5 GB)' }
    )
    Write-Host ''
    Write-Host "Current model: $Model"
    for ($i = 0; $i -lt $recommendations.Count; $i++) {
        $item = $recommendations[$i]
        Write-Host "$($i + 1). $($item.Model) - $($item.Label)"
    }
    Write-Host '5. Enter a custom Ollama model name'
    $choice = Read-Host 'Choose a model (Enter keeps the current model)'
    if (-not $choice) { return }
    if ($choice -match '^[1-4]$') {
        Set-Model $recommendations[[int]$choice - 1].Model
        return
    }
    if ($choice -eq '5') {
        $customModel = Read-Host 'Ollama model name (example: qwen3:8b)'
        if ($customModel) { Set-Model $customModel.Trim() }
        return
    }
    Write-Host 'Model selection was not changed.'
}

function Read-ModelConfig {
    if (-not (Test-Path -LiteralPath $ConfigFile)) { return }
    $config = Get-Content -LiteralPath $ConfigFile -Raw | ConvertFrom-Json
    if ($config.model) {
        if ($config.model -notmatch '^[a-zA-Z0-9][a-zA-Z0-9._:/-]*$') {
            throw "The saved model name in $ConfigFile is invalid."
        }
        $script:Model = $config.model
    }
}

function Select-StartupMode {
    Write-Host 'Local Codex AI - startup mode'
    Write-Host '1. Normal mode (install missing requirements, then open the project menu)'
    Write-Host '2. Install mode (install/repair requirements and selected model, then exit)'
    Write-Host '3. Force mode (install missing requirements, then request an unrestricted launch)'
    Write-Host '0. Exit'
    return (Read-Host 'Select a mode')
}

function Install-GitPortable {
    $known = Get-Command git.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($known) { return $known.Path }
    Write-Host 'Installing Git to download the GitHub repository...'
    $headers = @{ 'User-Agent' = 'LocalCodexBootstrap' }
    $release = Invoke-RestMethod 'https://api.github.com/repos/git-for-windows/git/releases/latest' -Headers $headers
    $asset = $release.assets | Where-Object { $_.name -match '^MinGit-.*-64-bit\.zip$' } | Select-Object -First 1
    if (-not $asset) { throw 'Could not find a Git for Windows MinGit download.' }
    $gitRoot = Join-Path $Tools 'git'
    $zip = Join-Path $Tools 'mingit.zip'
    Invoke-WebRequest $asset.browser_download_url -OutFile $zip -UseBasicParsing
    New-Item -ItemType Directory -Force -Path $gitRoot | Out-Null
    Expand-Archive -LiteralPath $zip -DestinationPath $gitRoot -Force
    Remove-Item -LiteralPath $zip -Force
    $git = Join-Path $gitRoot 'cmd\git.exe'
    if (-not (Test-Path $git)) { throw 'Git installation could not be verified.' }
    Add-UserPath @((Join-Path $gitRoot 'cmd'))
    return $git
}

function Install-Python {
    $python = Join-Path $Tools 'python\python.exe'
    if (Test-Path $python) { return $python }
    Write-Host 'This repository requires Python. Installing Python...'
    $page = (Invoke-WebRequest 'https://www.python.org/downloads/windows/' -UseBasicParsing).Content
    $match = [regex]::Match($page, '(?:https://www\.python\.org)?/ftp/python/(?<v>\d+\.\d+\.\d+)/python-\k<v>-amd64\.exe')
    if (-not $match.Success) { throw 'Could not find the latest Windows installer on python.org.' }
    $url = 'https://www.python.org' + $match.Value
    $installer = Join-Path $Tools 'python-installer.exe'
    Invoke-WebRequest $url -OutFile $installer -UseBasicParsing
    $pythonInstall = Start-Process -FilePath $installer -ArgumentList @('/quiet', 'InstallAllUsers=0', "TargetDir=$(Join-Path $Tools 'python')", 'PrependPath=0', 'Include_launcher=0', 'Include_pip=1', 'Include_test=0') -Wait -PassThru
    if ($pythonInstall.ExitCode -ne 0) { throw "Python installation failed (exit $($pythonInstall.ExitCode))." }
    if (-not (Test-Path $python)) { throw 'Python installation could not be verified.' }
    Add-UserPath @((Join-Path $Tools 'python'), (Join-Path $Tools 'python\Scripts'))
    return $python
}

function Get-ProjectSignature([string]$Path) {
    $files = @('package.json','package-lock.json','npm-shrinkwrap.json','pnpm-lock.yaml','yarn.lock','requirements.txt','pyproject.toml','setup.py')
    $parts = foreach ($name in $files) {
        $file = Join-Path $Path $name
        if (Test-Path $file) { $item = Get-Item $file; "$name|$($item.Length)|$($item.LastWriteTimeUtc.Ticks)" }
    }
    return ($parts -join ';')
}

function Install-ProjectDependencies([string]$Path) {
    $package = Join-Path $Path 'package.json'
    $pythonFiles = @('requirements.txt','pyproject.toml','setup.py') | Where-Object { Test-Path (Join-Path $Path $_) }
    if (-not (Test-Path $package) -and -not $pythonFiles) { return }
    $signature = Get-ProjectSignature $Path
    $sha = [Security.Cryptography.SHA256]::Create()
    try { $hash = [BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($Path))).Replace('-','') }
    finally { $sha.Dispose() }
    $stateDir = Join-Path $env:LOCALAPPDATA 'LocalCodexAI\deps'
    $stateFile = Join-Path $stateDir "$hash.json"
    if (Test-Path $stateFile) {
        try { if ((Get-Content $stateFile -Raw | ConvertFrom-Json).signature -eq $signature) { return } } catch {}
    }
    Write-Host 'Checking and installing project dependencies...'
    if (Test-Path $package) {
        $npm = Join-Path $script:NodeRoot 'npm.cmd'
        Push-Location $Path
        try {
        if (Test-Path (Join-Path $Path 'pnpm-lock.yaml')) { Invoke-Checked (Join-Path $script:NodeRoot 'corepack.cmd') @('pnpm','install','--frozen-lockfile') }
        elseif (Test-Path (Join-Path $Path 'yarn.lock')) { Invoke-Checked (Join-Path $script:NodeRoot 'corepack.cmd') @('yarn','install','--immutable') }
        elseif (Test-Path (Join-Path $Path 'package-lock.json')) { Invoke-Checked $npm @('ci','--prefix',$Path) }
        else { Invoke-Checked $npm @('install','--prefix',$Path) }
        } finally { Pop-Location }
    }
    if ($pythonFiles) {
        $python = Install-Python
        $venvPython = Join-Path $Path '.venv\Scripts\python.exe'
        if (-not (Test-Path $venvPython)) { Invoke-Checked $python @('-m','venv',(Join-Path $Path '.venv')) }
        if (Test-Path (Join-Path $Path 'requirements.txt')) { Invoke-Checked $venvPython @('-m','pip','install','-r',(Join-Path $Path 'requirements.txt')) }
        if ((Test-Path (Join-Path $Path 'pyproject.toml')) -or (Test-Path (Join-Path $Path 'setup.py'))) { Invoke-Checked $venvPython @('-m','pip','install','-e',$Path) }
    }
    New-Item -ItemType Directory -Force -Path $stateDir | Out-Null
    @{ signature = $signature; installed = (Get-Date).ToUniversalTime().ToString('o') } | ConvertTo-Json | Set-Content -LiteralPath $stateFile -Encoding UTF8
}

function Get-GitHubProjectName([string]$Url) {
    $clean = $Url.Trim().TrimEnd('/')
    $name = [IO.Path]::GetFileName($clean) -replace '\.git$',''
    if (-not $name -or $name -match '[<>:"/\\|?*]') { throw 'Could not determine the repository name from the URL.' }
    return $name
}

function Add-GitHubRepository {
    $url = Read-Host 'GitHub repository HTTPS URL (example: https://github.com/user/repo.git)'
    if (-not $url) {
        Write-Host 'No URL entered. Repository setup was cancelled.'
        Read-Host 'Press Enter to return to the menu' | Out-Null
        return
    }
    if ($url -notmatch '^(https://github\.com/|git@github\.com:)') { throw 'Enter a repository URL from github.com.' }
    $git = Install-GitPortable
    New-Item -ItemType Directory -Force -Path $Projects | Out-Null
    $name = Get-GitHubProjectName $url
    $path = Join-Path $Projects $name
    if (-not (Test-Path $path)) { Invoke-Checked $git @('clone',$url,$path) }
    $repoList = Join-Path $Root 'GitHubRepo.txt'
    $entries = @()
    if (Test-Path $repoList) { $entries = @(Get-Content $repoList | Where-Object { $_.Trim() }) }
    if ($entries -notcontains $url) { Add-Content -LiteralPath $repoList -Value $url -Encoding UTF8 }
    Set-Content -LiteralPath (Join-Path $Root 'ActiveProject.txt') -Value $path -Encoding UTF8
    Write-Host "Project ready: $path"
    Start-Codex $path $false
}

function Start-Codex([string]$Path, [bool]$Resume, [bool]$Force = $false) {
    Install-ProjectDependencies $Path
    Set-Content -LiteralPath (Join-Path $Root 'ActiveProject.txt') -Value $Path -Encoding UTF8
    $launch = @('launch','codex','--model',$Model,'--yes','--')
    if ($Resume) { $launch += @('resume','--all') }
    if ($Force) {
        $launch += '--dangerously-bypass-approvals-and-sandbox'
        Write-Host 'WARNING: force mode disables Codex approval prompts and sandbox protections.' -ForegroundColor Yellow
    }
    $launch += @('-C',$Path)
    Write-Host "Starting Codex: $Path (model: $Model)"
    Invoke-Checked $script:OllamaExe $launch
    Write-Host 'Codex has exited.'
    Read-Host 'Press Enter to return to the menu' | Out-Null
}

function Start-ForceCodex([string]$Path, [bool]$Resume = $false) {
    Write-Host 'Force mode lets Codex run without approval prompts or sandbox protections.' -ForegroundColor Yellow
    $confirmation = Read-Host 'Type FORCE to continue'
    if ($confirmation -cne 'FORCE') {
        Write-Host 'Force launch cancelled.'
        return
    }
    Start-Codex $Path $Resume $true
}

function Select-Project {
    $items = @(Get-ChildItem -LiteralPath $Projects -Directory -ErrorAction SilentlyContinue | Sort-Object Name)
    if (-not $items.Count) {
        Write-Host 'No projects are available yet. Choose option 1 and enter a GitHub repository URL first.'
        Read-Host 'Press Enter to return to the menu' | Out-Null
        return $null
    }
    Write-Host ''
    for ($i=0; $i -lt $items.Count; $i++) { Write-Host "[$($i+1)] $($items[$i].Name)" }
    $choice = Read-Host 'Project number (Enter to cancel)'
    if ($choice -match '^\d+$' -and [int]$choice -ge 1 -and [int]$choice -le $items.Count) { return $items[[int]$choice-1].FullName }
    return $null
}

function Get-StartupWorkspace {
    $activeFile = Join-Path $Root 'ActiveProject.txt'
    if (Test-Path $activeFile) {
        $saved = (Get-Content -LiteralPath $activeFile -Raw).Trim()
        if ($saved -and (Test-Path -LiteralPath $saved -PathType Container)) { return $saved }
    }
    $projects = @(Get-ChildItem -LiteralPath $Projects -Directory -ErrorAction SilentlyContinue | Sort-Object Name)
    if ($projects.Count -eq 1) { return $projects[0].FullName }
    return $Root
}

function Start-Manager {
    if (-not [Environment]::Is64BitOperatingSystem) { throw '64-bit Windows is required.' }
    $mode = Select-StartupMode
    if ($mode -eq '0') { return }
    if ($mode -notin @('1','2','3')) { throw 'Choose a valid startup mode (0, 1, 2, or 3).' }
    Read-ModelConfig
    if (-not (Test-Path -LiteralPath $ConfigFile) -or $mode -eq '2') {
        Select-Model
        if (-not (Test-Path -LiteralPath $ConfigFile)) { Set-Model $Model }
    }
    New-Item -ItemType Directory -Force -Path $Projects | Out-Null
    Install-Node
    Install-CodexCli
    Install-Ollama
    Add-UserPath @((Split-Path $script:OllamaExe), $CodexPrefix)
    Start-OllamaAndModel
    Install-PowerShellShim
    if ($mode -eq '2') {
        Write-Host "Installation complete. Selected model: $Model"
        Read-Host 'Press Enter to exit' | Out-Null
        return
    }
    if ($mode -eq '3') {
        $workspace = Get-StartupWorkspace
        Start-ForceCodex $workspace
        return
    }

    while ($true) {
        Clear-Host
        $workspace = Get-StartupWorkspace
        $projectCount = @(Get-ChildItem -LiteralPath $Projects -Directory -ErrorAction SilentlyContinue).Count
        Write-Host 'Local Codex AI'
        Write-Host "Projects available: $projectCount"
        Write-Host ''
        Write-Host "Enter. Start the agent now in: $workspace"
        Write-Host '1. Add GitHub repository, clone, and start the agent'
        Write-Host '2. Select a project and start the agent'
        Write-Host '3. Select a project and resume a previous conversation'
        Write-Host '4. Resume a previous conversation in this folder (no project)'
        Write-Host '5. Force-start the current workspace (bypasses approvals and sandbox)'
        Write-Host '6. Select a project and force-start it'
        Write-Host '7. Change model'
        Write-Host '0. Exit'
        $choice = Read-Host 'Select an option'
        switch ($choice) {
            '' {
                Start-Codex $workspace $false
            }
            '1' { Add-GitHubRepository }
            '2' {
                $path = Select-Project
                if ($path) { Start-Codex $path $false }
            }
            '3' {
                $path = Select-Project
                if ($path) { Start-Codex $path $true }
            }
            '4' { Start-Codex $Root $true }
            '5' {
                Start-ForceCodex $workspace
            }
            '6' {
                $path = Select-Project
                if ($path) { Start-ForceCodex $path }
            }
            '7' {
                Select-Model
                Start-OllamaAndModel
            }
            '0' { return }
            default { Write-Host 'Choose an option from the menu.'; Start-Sleep -Seconds 1 }
        }
    }
}

try { Start-Manager }
catch {
    $errorText = "Local Codex AI error`r`nTime: $(Get-Date -Format o)`r`n`r`n$($_.Exception.ToString())`r`n$($_.ScriptStackTrace)"
    $errorFile = Join-Path $Root 'bootstrap-error.log'
    try { Set-Content -LiteralPath $errorFile -Value $errorText -Encoding UTF8 } catch {}
    Write-Host "`nError: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Detailed error log: $errorFile"
    exit 1
}