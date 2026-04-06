# Lab PC Remote Control Setup (run as Administrator)
# Usage: Right-click > Run with PowerShell
#   or:  powershell -ExecutionPolicy Bypass -File setup_remote.ps1

# Check admin
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "[ERROR] Run as Administrator" -ForegroundColor Red
    pause
    exit 1
}

Write-Host "============================================"
Write-Host "  Lab PC Remote Control Setup"
Write-Host "============================================"

# 1. Password
Write-Host "`n[1/5] Setting password..."
net user $env:USERNAME selab1234 | Out-Null
Write-Host "  Password set: selab1234"

# 2. Network to Private
Write-Host "`n[2/5] Setting network to Private..."
Get-NetConnectionProfile | Where-Object { $_.NetworkCategory -eq "Public" } | Set-NetConnectionProfile -NetworkCategory Private -ErrorAction SilentlyContinue
Write-Host "  Network OK"

# 3. WinRM
Write-Host "`n[3/5] Enabling WinRM..."
winrm quickconfig -quiet -force 2>$null | Out-Null
Enable-PSRemoting -Force -SkipNetworkProfileCheck -ErrorAction SilentlyContinue | Out-Null
Set-Item WSMan:\localhost\Client\TrustedHosts -Value "*" -Force -ErrorAction SilentlyContinue
Write-Host "  WinRM enabled"

# 4. Firewall
Write-Host "`n[4/5] Firewall rules..."
netsh advfirewall firewall add rule name="WinRM-HTTP" dir=in action=allow protocol=TCP localport=5985 2>$null | Out-Null
netsh advfirewall firewall add rule name="WOL" dir=in action=allow protocol=UDP localport=9 2>$null | Out-Null
Write-Host "  Firewall OK"

# 5. Disable sleep
Write-Host "`n[5/5] Disabling sleep..."
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
Write-Host "  Sleep disabled"

# Show info
$ip = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -like "10.108.*" }).IPAddress
$mac = (Get-NetAdapter | Where-Object { $_.Status -eq "Up" -and $_.MacAddress -notlike "00-50-56*" }).MacAddress

Write-Host "`n============================================"
Write-Host "  Setup complete!"
Write-Host "  IP:  $ip"
Write-Host "  MAC: $mac"
Write-Host "============================================"
pause
