$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot
$pythonPath = Join-Path $projectRoot ".venv/Scripts/python.exe"
$nodeCommand = Get-Command node -ErrorAction SilentlyContinue
$nodePath = if ($nodeCommand) { $nodeCommand.Source } else { $null }
if ($nodePath -and [int]((& $nodePath --version).TrimStart("v").Split(".")[0]) -lt 20) { $nodePath = $null }
if (-not $nodePath) {
    $bundledNode = Join-Path $env:USERPROFILE ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe"
    if (Test-Path -LiteralPath $bundledNode) { $nodePath = $bundledNode }
}
if (-not $nodePath) { throw "需要 Node.js 20+，推荐 Node.js 24 LTS。" }
if (-not (Test-Path -LiteralPath $pythonPath)) { throw "请先按 README 创建虚拟环境并安装后端依赖。" }
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot "frontend/node_modules/vite/bin/vite.js"))) { throw "请先在 frontend 运行 npm install。" }
foreach ($port in @(8000, 5173)) {
    if (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) { throw "端口 $port 已占用，请先停止原服务。" }
}
$backendProcess = Start-Process -FilePath $pythonPath -ArgumentList @("-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", "8000") -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $projectRoot "backend.stdout.log") -RedirectStandardError (Join-Path $projectRoot "backend.stderr.log") -PassThru
# Provider secrets are inherited by the backend only, never by the Vite process.
$frontendSecretNames = @('DASHSCOPE_API_KEY', 'BOCHA_API_KEY')
$frontendSecretBackup = @{}
try {
    foreach ($secretName in $frontendSecretNames) {
        $frontendSecretBackup[$secretName] = [Environment]::GetEnvironmentVariable($secretName, 'Process')
        [Environment]::SetEnvironmentVariable($secretName, $null, 'Process')
    }
$frontendProcess = Start-Process -FilePath $nodePath -ArgumentList @("node_modules/vite/bin/vite.js", "--host", "127.0.0.1") -WorkingDirectory (Join-Path $projectRoot "frontend") -WindowStyle Hidden -RedirectStandardOutput (Join-Path $projectRoot "frontend.stdout.log") -RedirectStandardError (Join-Path $projectRoot "frontend.stderr.log") -PassThru
} finally {
    foreach ($secretName in $frontendSecretNames) {
        [Environment]::SetEnvironmentVariable($secretName, $frontendSecretBackup[$secretName], 'Process')
    }
}
Write-Host "前端: http://127.0.0.1:5173"
Write-Host "后端文档: http://127.0.0.1:8000/docs"
Write-Host "进程 PID: frontend=$($frontendProcess.Id), backend=$($backendProcess.Id)"
Write-Host "停止前端: taskkill /PID $($frontendProcess.Id) /T /F"
Write-Host "停止后端: taskkill /PID $($backendProcess.Id) /T /F"
