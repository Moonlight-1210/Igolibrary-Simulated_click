# 结束占用 7789 端口的旧 node 服务进程（只结束 node.exe，避免误杀其他程序）
$ErrorActionPreference = 'SilentlyContinue'
$conn = Get-NetTCPConnection -LocalPort 7789 -State Listen | Select-Object -First 1
if ($null -ne $conn) {
    $p = Get-Process -Id $conn.OwningProcess
    if ($null -ne $p -and $p.ProcessName -eq 'node') {
        Stop-Process -Id $conn.OwningProcess -Force
        Write-Host "已结束旧服务进程 PID=$($conn.OwningProcess)"
        Start-Sleep -Milliseconds 600
    }
}
