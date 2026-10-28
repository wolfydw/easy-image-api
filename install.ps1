& {
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$archiveUrl = "https://github.com/wolfydw/easy-image-api/archive/refs/heads/main.zip"
$defaultModel = "gpt-image-2"
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

function Get-ModelsUri([string]$Endpoint) {
    if ($Endpoint -match "/v1(?:/.*)?$") {
        return (($Endpoint -replace "/v1(?:/.*)?$", "/v1") + "/models")
    }
    return ($Endpoint.TrimEnd("/") + "/v1/models")
}

function Select-Model([string]$ApiKey, [string]$CurrentModel = $defaultModel, [string]$Endpoint = "https://cf.ydw.cool") {
    $models = @()
    try {
        $headers = @{ Authorization = "Bearer $ApiKey" }
        $response = Invoke-RestMethod `
            -UseBasicParsing `
            -Uri (Get-ModelsUri $Endpoint) `
            -Headers $headers `
            -Method Get `
            -TimeoutSec 30
        if ($null -ne $response.data) {
            $models = @(
                $response.data |
                    ForEach-Object { [string]$_.id } |
                    Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
                    Select-Object -Unique
            )
        }
    }
    catch {
        $models = @()
    }

    if (-not [string]::IsNullOrWhiteSpace($CurrentModel) -and $models -notcontains $CurrentModel) {
        $models += $CurrentModel
    }

    if ($models.Count -gt 0) {
        Write-Host "可用模型："
        for ($index = 0; $index -lt $models.Count; $index++) {
            $marker = if ($models[$index] -eq $CurrentModel) { "（当前）" } else { "" }
            Write-Host ("  {0}) {1}{2}" -f ($index + 1), $models[$index], $marker)
        }
        while ($true) {
            $choice = Read-Host "请选择模型编号（直接回车使用 $CurrentModel）"
            if ([string]::IsNullOrWhiteSpace($choice)) {
                if (-not [string]::IsNullOrWhiteSpace($CurrentModel)) {
                    return $CurrentModel
                }
                return $models[0]
            }
            $number = 0
            if ([int]::TryParse($choice, [ref]$number) -and $number -ge 1 -and $number -le $models.Count) {
                return $models[$number - 1]
            }
            Write-Warning "请输入列表中的有效编号。"
        }
    }

    Write-Warning "无法获取可用模型列表，请手动输入模型名。"
    $manualModel = Read-Host "请输入模型名称（直接回车使用 $CurrentModel）"
    if ([string]::IsNullOrWhiteSpace($manualModel)) {
        return $CurrentModel
    }
    return $manualModel.Trim()
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
    foreach ($required in @("SKILL.md", "scripts\generate_image.py", "scripts\run_generate_image.sh", "scripts\run_generate_image.ps1")) {
        if (-not (Test-Path -LiteralPath (Join-Path $source.FullName $required) -PathType Leaf)) {
            throw "安装包缺少必要文件：$required"
        }
    }

    $configPath = Join-Path $destination "config.json"
    $configEntry = Get-Item -LiteralPath $configPath -Force -ErrorAction SilentlyContinue
    $hasExistingConfig = $null -ne $configEntry
    $apiKey = $null
    $model = $defaultModel
    $existingConfig = $null
    $configChanged = $false
    if (-not $hasExistingConfig) {
        $apiKey = Read-ApiKey
        $model = Select-Model $apiKey
    }
    else {
        try {
            $existingConfig = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
            $apiKey = [string]$existingConfig.api_key
            $currentModel = [string]$existingConfig.model
            if ([string]::IsNullOrWhiteSpace($currentModel)) {
                $currentModel = $defaultModel
            }
            $configuredEndpoint = [string]$existingConfig.endpoint
            if ([string]::IsNullOrWhiteSpace($configuredEndpoint)) {
                $configuredEndpoint = "https://cf.ydw.cool"
            }
            if (-not [string]::IsNullOrWhiteSpace($apiKey)) {
                $model = Select-Model $apiKey $currentModel $configuredEndpoint
                $configChanged = $model -ne $currentModel
            }
        }
        catch {
            Write-Warning "无法读取现有配置，已跳过模型选择并保留原配置。"
            $existingConfig = $null
        }
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
            model = $model
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
    elseif ($configChanged -and $null -ne $existingConfig) {
        if ($existingConfig.PSObject.Properties.Name -contains "model") {
            $existingConfig.model = $model
        }
        else {
            $existingConfig | Add-Member -NotePropertyName model -NotePropertyValue $model
        }
        $updatedJson = ($existingConfig | ConvertTo-Json -Depth 20) + [Environment]::NewLine
        $updatedPath = Join-Path $tempDirectory "config.updated.json"
        $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
        [IO.File]::WriteAllText($updatedPath, $updatedJson, $utf8NoBom)
        Move-Item -LiteralPath $updatedPath -Destination $configPath -Force
    }

    if ($hasExistingConfig) {
        Write-Output "easy-image-api skill 已升级：$destination"
        if ($configChanged) {
            Write-Output "已更新模型选择，其他配置字段保持不变。"
        }
        else {
            Write-Output "已保留原有 config.json。"
        }
        Write-Output "配置文件位置：$configPath"
    }
    else {
        Write-Output "easy-image-api skill 已安装：$destination"
        Write-Output "配置文件已生成：$configPath"
    }
    Write-Output "安装阶段不需要 Python。实际生成或编辑图片时，skill 会探测现有的 Python 3.7+ 环境；不会自动安装 Python。"
    Write-Output "请在 Codex 的下一个任务中使用 `$easy-image-api。"
}
finally {
    [Net.ServicePointManager]::SecurityProtocol = $previousSecurityProtocol
    if ($null -ne $tempDirectory -and (Test-Path -LiteralPath $tempDirectory)) {
        Remove-Item -LiteralPath $tempDirectory -Recurse -Force -ErrorAction SilentlyContinue
    }
}
}
