param(
    [string]$RunDir,
    [string]$AdoptFrom,
    [switch]$Resume
)

$ErrorActionPreference = 'Stop'
$vfssqlEvalRoot = $PSScriptRoot
$vfssqlBackendRoot = Split-Path -Parent $vfssqlEvalRoot
$vfssqlPython = Join-Path $vfssqlBackendRoot '.venv\Scripts\python.exe'

# 凭据仅保存在当前进程环境中，再由隐藏后台进程继承。
if (-not $env:VFSSQL_EVAL_EMAIL) {
    $env:VFSSQL_EVAL_EMAIL = Read-Host 'DeerFlow 评估账号'
}
if (-not $env:VFSSQL_EVAL_PASSWORD) {
    $vfssqlSecurePassword = Read-Host 'DeerFlow 评估密码' -AsSecureString
    $vfssqlPasswordPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($vfssqlSecurePassword)
    try {
        $env:VFSSQL_EVAL_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($vfssqlPasswordPointer)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($vfssqlPasswordPointer)
    }
}

try {
    $vfssqlArguments = @('-X', 'utf8', '-B', (Join-Path $vfssqlEvalRoot 'start_evaluation.py'))
    if ($RunDir) { $vfssqlArguments += @('--run-dir', $RunDir) }
    if ($Resume) { $vfssqlArguments += '--resume' }
    if ($AdoptFrom) { $vfssqlArguments += @('--adopt-from', $AdoptFrom) }
    & $vfssqlPython @vfssqlArguments
    if ($LASTEXITCODE -ne 0) { throw "评估启动失败，退出码 $LASTEXITCODE" }
}
finally {
    Remove-Item -LiteralPath 'Env:VFSSQL_EVAL_EMAIL' -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath 'Env:VFSSQL_EVAL_PASSWORD' -ErrorAction SilentlyContinue
}
