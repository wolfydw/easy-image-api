& {
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$archiveUrl = "https://github.com/wolfydw/easy-image-api/archive/refs/heads/main.zip"
$tempDirectory = $null
$previousSecurityProtocol = [Net.ServicePointManager]::SecurityProtocol

function Read-ApiKey {
    while ($true) {
        $plainValue = Read-Host "请输入生图 API Key"
        if (-not [string]::IsNullOrWhiteSpace($plainValue)) {
            return $plainValue
        }
        Write-Warning "API Key 不能为空，请重新输入。"
    }
}

try {
    [Net.ServicePointManager]::SecurityProtocol =
        $previousSecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

    $tempDirectory = Join-Path ([IO.Path]::GetTempPath()) ("easy-image-api-" + [Guid]::NewGuid().ToString("N"))
    $archivePath = Join-Path $tempDirectory "easy-image-api.zip"
    $extractPath = Join-Path $tempDirectory "extracted"
    if ([string]::IsNullOrWhiteSpace($env:CODEX_HOME)) {
        $codexHome = Join-Path $HOME ".codex"
    }
    else {
        $codexHome = $env:CODEX_HOME
    }
    $destination = Join-Path $codexHome "skills\easy-image-api"
    New-Item -ItemType Directory -Force -Path $tempDirectory, $extractPath | Out-Null

    Invoke-WebRequest -UseBasicParsing -Uri $archiveUrl -OutFile $archivePath
    Expand-Archive -LiteralPath $archivePath -DestinationPath $extractPath -Force

    $source = Get-ChildItem -LiteralPath $extractPath -Directory | Select-Object -First 1
    if ($null -eq $source) {
        throw "安装包中没有找到 skill 源目录。"
    }
    foreach ($required in @("SKILL.md", "scripts\generate_image.py")) {
        if (-not (Test-Path -LiteralPath (Join-Path $source.FullName $required) -PathType Leaf)) {
            throw "安装包缺少必要文件：$required"
        }
    }

    $configPath = Join-Path $destination "config.json"
    $configEntry = Get-Item -LiteralPath $configPath -Force -ErrorAction SilentlyContinue
    $hasExistingConfig = $null -ne $configEntry
    $apiKey = $null
    if (-not $hasExistingConfig) {
        $apiKey = Read-ApiKey
    }

    $destinationExists = Test-Path -LiteralPath $destination -PathType Container
    if (-not $destinationExists) {
        New-Item -ItemType Directory -Force -Path $destination | Out-Null
    }

    foreach ($managed in @("SKILL.md", "agents", "assets", "references", "scripts")) {
        $sourcePath = Join-Path $source.FullName $managed
        if (Test-Path -LiteralPath $sourcePath) {
            Copy-Item -LiteralPath $sourcePath -Destination $destination -Recurse -Force
        }
    }

    if (-not $hasExistingConfig) {
        $config = [ordered]@{
            endpoint = "https://cf.ydw.cool"
            api_key = $apiKey
            model = "gpt-image-2.5"
            size = "auto"
            quality = "high"
            output_format = "png"
        }
        $json = ($config | ConvertTo-Json) + [Environment]::NewLine
        $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
        $configStream = $null
        try {
            $configStream = [IO.File]::Open(
                $configPath,
                [IO.FileMode]::CreateNew,
                [IO.FileAccess]::Write,
                [IO.FileShare]::None
            )
        }
        catch [IO.IOException] {
            if ($null -eq (Get-Item -LiteralPath $configPath -Force -ErrorAction SilentlyContinue)) {
                throw
            }
        }
        if ($null -ne $configStream) {
            try {
                $configBytes = $utf8NoBom.GetBytes($json)
                $configStream.Write($configBytes, 0, $configBytes.Length)
                $configStream.Flush()
            }
            catch {
                $configStream.Dispose()
                $configStream = $null
                Remove-Item -LiteralPath $configPath -Force -ErrorAction SilentlyContinue
                throw
            }
            finally {
                if ($null -ne $configStream) {
                    $configStream.Dispose()
                }
            }
        }
    }

    if ($hasExistingConfig) {
        Write-Output "easy-image-api skill 已升级：$destination"
        Write-Output "已保留原有 config.json，未读取、覆盖或修改。"
        Write-Output "配置文件位置：$configPath"
    }
    else {
        Write-Output "easy-image-api skill 已安装：$destination"
        Write-Output "配置文件已生成：$configPath"
    }
    Write-Output "安装阶段不需要 Python。实际生成或编辑图片时需要 Python 3.9 或更高版本。"
    Write-Output "请在 Codex 的下一个任务中使用 `$easy-image-api。"
}
finally {
    [Net.ServicePointManager]::SecurityProtocol = $previousSecurityProtocol
    if ($null -ne $tempDirectory -and (Test-Path -LiteralPath $tempDirectory)) {
        Remove-Item -LiteralPath $tempDirectory -Recurse -Force -ErrorAction SilentlyContinue
    }
}
}
