& {
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$installerUrl = "https://raw.githubusercontent.com/wolfydw/easy-image-api/main/scripts/install_skill.py"
$tempFile = $null
$python = $null
$previousSecurityProtocol = [Net.ServicePointManager]::SecurityProtocol

try {
    $candidates = @(
        @{ FilePath = "py"; PrefixArguments = @("-3") },
        @{ FilePath = "python"; PrefixArguments = @() },
        @{ FilePath = "python3"; PrefixArguments = @() }
    )

    foreach ($candidate in $candidates) {
        if (-not (Get-Command $candidate.FilePath -ErrorAction SilentlyContinue)) {
            continue
        }

        $versionArguments = @($candidate.PrefixArguments) + @(
            "-c",
            "import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)"
        )
        $versionExitCode = 1
        try {
            & $candidate.FilePath @versionArguments 2>$null
            $versionExitCode = $LASTEXITCODE
        }
        catch {
            $versionExitCode = 1
        }
        if ($versionExitCode -eq 0) {
            $python = $candidate
            break
        }
    }

    if ($null -eq $python) {
        throw "Python 3.9 or newer is required."
    }

    [Net.ServicePointManager]::SecurityProtocol =
        $previousSecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

    $tempFile = [IO.Path]::GetTempFileName()
    Invoke-WebRequest -UseBasicParsing -Uri $installerUrl -OutFile $tempFile

    $installerArguments = @($python.PrefixArguments) + @($tempFile)
    & $python.FilePath @installerArguments
    if ($LASTEXITCODE -ne 0) {
        throw "The easy-image-api installer failed with exit code $LASTEXITCODE."
    }
}
finally {
    [Net.ServicePointManager]::SecurityProtocol = $previousSecurityProtocol
    if ($null -ne $tempFile -and (Test-Path -LiteralPath $tempFile)) {
        Remove-Item -LiteralPath $tempFile -Force -ErrorAction SilentlyContinue
    }
}
}
