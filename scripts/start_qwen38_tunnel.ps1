param(
    [ValidateRange(1024, 65535)]
    [int]$LocalPort = 18080
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$resultsRoot = Join-Path $projectRoot 'test-results'
New-Item -ItemType Directory -Path $resultsRoot -Force | Out-Null
$stateFile = Join-Path $resultsRoot 'qwen38-api-tunnel.json'
$forward = "127.0.0.1:${LocalPort}:127.0.0.1:18080"
$listener = Get-NetTCPConnection -State Listen -LocalPort $LocalPort -ErrorAction SilentlyContinue
if ($listener) {
    $existing = Get-CimInstance Win32_Process -Filter "ProcessId = $($listener[0].OwningProcess)"
    if ($existing.Name -ne 'ssh.exe' -or $existing.CommandLine -notlike "*-L $forward dgx*") {
        throw "Local port $LocalPort is occupied by another process."
    }
    Write-Output "SSH forward is already running: http://127.0.0.1:$LocalPort"
    exit 0
}

$connector = 'C:/Program Files/Git/mingw64/bin/connect.exe'
if (-not (Test-Path -LiteralPath $connector -PathType Leaf)) {
    throw "SSH connector was not found at $connector"
}
$arguments = @(
    '-N', '-T',
    '-o', '"ProxyCommand=\"C:/Program Files/Git/mingw64/bin/connect.exe\" -H 127.0.0.1:7890 %h %p"',
    '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
    '-o', 'ExitOnForwardFailure=yes', '-o', 'ConnectTimeout=12',
    '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=3',
    '-L', $forward, 'dgx'
)
$process = Start-Process -FilePath 'ssh.exe' -ArgumentList $arguments -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $resultsRoot 'qwen38-api-tunnel.stdout.log') `
    -RedirectStandardError (Join-Path $resultsRoot 'qwen38-api-tunnel.stderr.log')
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    Start-Sleep -Milliseconds 500
    $process.Refresh()
    if ($process.HasExited) {
        throw (Get-Content -LiteralPath (Join-Path $resultsRoot 'qwen38-api-tunnel.stderr.log') -Raw)
    }
    $listener = Get-NetTCPConnection -State Listen -LocalPort $LocalPort -ErrorAction SilentlyContinue
    if ($listener -and $listener[0].OwningProcess -eq $process.Id) {
        @{
            pid = $process.Id
            local_port = $LocalPort
            remote_port = 18080
            host_alias = 'dgx'
            url = "http://127.0.0.1:$LocalPort"
            started_at = (Get-Date).ToString('o')
        } | ConvertTo-Json | Set-Content -LiteralPath $stateFile -Encoding utf8
        Write-Output "SSH forward is running: http://127.0.0.1:$LocalPort"
        exit 0
    }
}
Stop-Process -Id $process.Id
throw 'SSH forward did not open its local port.'
