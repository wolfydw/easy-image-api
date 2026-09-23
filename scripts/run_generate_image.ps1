& {
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$scriptPath = Join-Path $PSScriptRoot "generate_image.py"
$versionCheck = "import sys; raise SystemExit(0 if sys.version_info >= (3, 7) else 1)"
$candidates = @(
    ,@{ Command = "py"; Prefix = @(-3) }
    ,@{ Command = "python3"; Prefix = @() }
    ,@{ Command = "python"; Prefix = @() }
)

foreach ($candidate in $candidates) {
    try {
        & $candidate.Command @($candidate.Prefix) -c $versionCheck *> $null
    }
    catch {
        continue
    }
    if ($LASTEXITCODE -eq 0) {
        & $candidate.Command @($candidate.Prefix) $scriptPath @args
        exit $LASTEXITCODE
    }
}

Write-Error "错误：未找到可用的 Python 3.7+ 环境。请先安装 Python 3，再重试。"
exit 1
}
