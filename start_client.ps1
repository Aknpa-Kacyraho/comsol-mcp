# 启动 COMSOL Desktop 客户端并连接本机 COMSOL server
#
# 用法:
#   .\start_client.ps1                      # 连接 localhost:2036
#   .\start_client.ps1 -Port 2037           # 指定端口
#   .\start_client.ps1 -Open "model.mph"    # 连上后打开模型
#   .\start_client.ps1 -ComsolRoot "D:\Soft\Comsol\COMSOL64\Multiphysics"
param(
    [int]$Port = 2036,
    [string]$Open = "",
    [string]$ComsolRoot = $env:COMSOL_ROOT
)

$ErrorActionPreference = "Stop"

# 1) 环境变量 -> 2) 注册表自动发现
if (-not $ComsolRoot) {
    $base = "HKLM:\SOFTWARE\Comsol"
    if (Test-Path $base) {
        $candidates = Get-ChildItem $base | Where-Object { $_.PSChildName -match "^COMSOL\d+" }
        $found = $candidates |
            ForEach-Object { (Get-ItemProperty $_.PSPath).COMSOLROOT } |
            Where-Object { $_ } |
            Sort-Object -Descending
        $ComsolRoot = $found | Select-Object -First 1
    }
}

if (-not $ComsolRoot) {
    throw "找不到 COMSOL 安装目录。请设置环境变量 COMSOL_ROOT 或传 -ComsolRoot。"
}

$client = Join-Path $ComsolRoot "bin\win64\comsolmphclient.exe"
if (-not (Test-Path $client)) {
    throw "找不到 comsolmphclient.exe：$client"
}

$clientArgs = @("-server", "localhost", "-port", "$Port")
if ($Open) {
    $clientArgs += @("-open", $Open)
}

Write-Host "COMSOL root : $ComsolRoot"
Write-Host "Connecting  : localhost:$Port"
& $client @clientArgs
