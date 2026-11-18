# One-click Mobile App & Public HTTPS Tunnel Launcher for Let's Meet
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

Write-Host "=== Let's Meet · 启动手机 App 服务与公网 HTTPS 通道 ===" -ForegroundColor Cyan

# Stop previous listeners on 8780
Get-NetTCPConnection -LocalPort 8780 -State Listen -ErrorAction SilentlyContinue | ForEach-Object {
  Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue
}
Get-Process cloudflared -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue

$env:PYTHONPATH = "$ProjectRoot\src"
Start-Process -FilePath "python" -ArgumentList "-m", "uvicorn", "lets_meet.app:app", "--host", "0.0.0.0", "--port", "8780" -WindowStyle Hidden

$Cloudflared = "$ProjectRoot\tools\cloudflared.exe"
$TunnelLog = "$ProjectRoot\tools\tunnel.log"
if (Test-Path $TunnelLog) { Remove-Item $TunnelLog -Force }

if (Test-Path $Cloudflared) {
  Start-Process -FilePath $Cloudflared -ArgumentList "tunnel", "--url", "http://127.0.0.1:8780", "--protocol", "http2", "--logfile", $TunnelLog -WindowStyle Hidden
  Write-Host "正在分配公网 HTTPS 域名 (支持 4G/5G、微信转发、手机真实 GPS 定位)..." -ForegroundColor Yellow
  for ($i = 0; $i -lt 15; $i++) {
    Start-Sleep -Seconds 1
    if (Test-Path $TunnelLog) {
      $content = Get-Content $TunnelLog -Raw -ErrorAction SilentlyContinue
      if ($content -match "(https://[a-z0-9-]+\.trycloudflare\.com)") {
        Write-Host "`n✅ 手机公网 HTTPS 地址: $($Matches[1])" -ForegroundColor Green
        break
      }
    }
  }
}

Write-Host "✅ 本机预览地址: http://127.0.0.1:8780`n" -ForegroundColor Green
