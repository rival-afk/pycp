# pycp installer: clones/updates pycp, installs deps, registers MCP server in OpenCode.
# Usage:  irm https://raw.githubusercontent.com/rival-afk/pycp/main/install.ps1 | iex
#         with -Local switch (download the file and run: ./install.ps1 -Local) — project instead of global.
param([switch]$Local)

$ErrorActionPreference = "Stop"
$RepoUrl = "https://github.com/rival-afk/pycp.git"
$Dest = Join-Path $env:LOCALAPPDATA "pycp"
$Scope = if ($Local) { "project" } else { "global" }
Write-Host "pycp: установка ($Scope) в $Dest"

# 1) clone or update
if (Test-Path (Join-Path $Dest ".git")) {
    Write-Host "pycp: обновляю существующую установку..."
    git -C $Dest pull --ff-only
} elseif (Test-Path $Dest) {
    throw "Папка $Dest существует и не является git-репозиторием. Удалите её вручную и запустите снова."
} else {
    git clone --depth 1 $RepoUrl $Dest
}

# 2) interpreter
$py = $null
foreach ($candidate in @("python", "py", "python3")) {
    if (Get-Command $candidate -ErrorAction SilentlyContinue) { $py = $candidate; break }
}
if (-not $py) { throw "Не найден python в PATH. Установите Python 3 и запустите снова." }
Write-Host "pycp: интерпретатор: $py"

# 3) deps (fallback: venv)
& $py -m pip install -q -r (Join-Path $Dest "requirements.txt")
if ($LASTEXITCODE -ne 0) {
    Write-Host "pycp: pip install не удался, создаю venv в $Dest\.venv ..."
    & $py -m venv (Join-Path $Dest ".venv")
    $py = Join-Path $Dest ".venv\Scripts\python.exe"
    & $py -m pip install -q -r (Join-Path $Dest "requirements.txt")
    if ($LASTEXITCODE -ne 0) { throw "Не удалось установить зависимости в venv." }
}
$pyExe = (& $py -c "import sys; print(sys.executable)").Trim()
Write-Host "pycp: python: $pyExe"

# 4) register in OpenCode
$addArgs = @("mcp", "add", "pycp")
if (-not $Local) { $addArgs += "--global" }
$addArgs += @("--", $pyExe, (Join-Path $Dest "server.py"))

$registered = $false
if (Get-Command opencode -ErrorAction SilentlyContinue) {
    Write-Host "pycp: регистрирую MCP-сервер в OpenCode ($Scope)..."
    & opencode mcp remove pycp 2>$null | Out-Null   # идемпотентность; ошибку игнорируем
    & opencode @addArgs
    if ($LASTEXITCODE -eq 0) {
        $registered = $true
        Write-Host "pycp: готово. Проверка: opencode mcp list  (ожидается 'pycp  connected')"
    } else {
        Write-Warning "pycp: 'opencode mcp add' завершилось с кодом $LASTEXITCODE. Пробую записать конфиг напрямую..."
    }
}

if (-not $registered) {
    # CLI может отсутствовать (например, установлено только десктопное приложение).
    # Не молчим и не печатаем "на всякий случай" JSON: дописываем найденный конфиг.
    Write-Host "pycp: ищу конфиг OpenCode, чтобы зарегистрироваться без CLI..."
    $configDirs = if ($Local) {
        @((Get-Location).Path)
    } else {
        @(
            (Join-Path $env:USERPROFILE ".config\opencode"),
            (Join-Path $env:APPDATA "opencode")
        )
    }
    $configPaths = @()
    foreach ($dir in $configDirs) {
        $configPaths += @((Join-Path $dir "opencode.jsonc"), (Join-Path $dir "opencode.json"))
    }
    $mergeScript = Join-Path $Dest "merge_mcp_config.py"
    foreach ($cfg in $configPaths) {
        if (-not (Test-Path $cfg)) { continue }
        if (-not (Test-Path $mergeScript)) { break }
        & $py $mergeScript $cfg $pyExe (Join-Path $Dest "server.py") $Dest
        if ($LASTEXITCODE -eq 0) {
            $registered = $true
            Write-Host "pycp: готово. Перезапустите OpenCode, чтобы подключить инструменты pycp."
            break
        }
        Write-Warning "pycp: не удалось обновить конфиг $cfg"
    }
}

if (-not $registered) {
    Write-Warning "pycp: НЕ зарегистрирован: opencode CLI и подходящий конфиг не найдены (или запись не удалась)."
    Write-Host "pycp: добавьте в opencode.json(c) вручную:"
    $snippet = @"
{
  `"mcp`": {
    `"servers`": {
      `"pycp`": {
        `"type`": `"local`",
        `"command`": [`"$($pyExe -replace '\\','/')`", `"$((Join-Path $Dest 'server.py') -replace '\\','/')`"],
        `"cwd`": `"$($Dest -replace '\\','/')`"
      }
    }
  }
}
"@
    Write-Host $snippet
    throw "pycp: регистрация MCP-сервера не выполнена."
}
