# ============================================
#  Lab PC Central Control (run from front PC)
# ============================================
#
#  Usage:
#    .\central_control.ps1 discover       # Discover lab PCs on network
#    .\central_control.ps1 status         # Check collection status
#    .\central_control.ps1 start          # Start collection on all PCs
#    .\central_control.ps1 pull           # Git pull on all PCs
#    .\central_control.ps1 stop           # Stop collection on all PCs
#    .\central_control.ps1 shutdown       # Shutdown all PCs
#    .\central_control.ps1 wake           # Wake all PCs (WOL)
#    .\central_control.ps1 nosleep        # Disable sleep on all PCs
#    .\central_control.ps1 run "command"  # Run custom command on all PCs

param(
    [Parameter(Position=0)]
    [string]$Action = "status",
    [Parameter(Position=1)]
    [string]$CustomCommand = ""
)

$ErrorActionPreference = "SilentlyContinue"

# --- Config ---
$ConfigFile = "$PSScriptRoot\lab_pcs.json"
$Username = "N325"
$Password = "selab1234"
$RepoPath = "C:\dataset"   # adjust if different
$SecPass = ConvertTo-SecureString $Password -AsPlainText -Force
$Cred = New-Object System.Management.Automation.PSCredential($Username, $SecPass)

# --- Helper: Load PC list ---
function Load-PCs {
    if (Test-Path $ConfigFile) {
        return Get-Content $ConfigFile | ConvertFrom-Json
    }
    Write-Host "[ERROR] $ConfigFile not found. Run: .\central_control.ps1 discover" -ForegroundColor Red
    return $null
}

# --- Action: Discover PCs on network ---
function Discover-PCs {
    $subnet = "10.108.10"
    $myIP = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -like "$subnet.*" }).IPAddress
    Write-Host "Scanning $subnet.1-254 via WinRM..." -ForegroundColor Cyan
    Write-Host "  My IP: $myIP (skipping)" -ForegroundColor Yellow

    $pcs = @()
    $i = 1

    1..254 | ForEach-Object {
        $ip = "$subnet.$_"
        if ($ip -eq $myIP) { return }  # skip self
        $result = Test-WSMan -ComputerName $ip -ErrorAction SilentlyContinue 2>$null
        if ($result) {
            # Get MAC via WinRM
            $mac = ""
            try {
                $macResult = Invoke-Command -ComputerName $ip -Credential $Cred -ScriptBlock {
                    (Get-NetAdapter | Where-Object { $_.Status -eq "Up" -and $_.MacAddress -notlike "00-50-56*" }).MacAddress
                } -ErrorAction SilentlyContinue
                if ($macResult) { $mac = $macResult }
            } catch {}
            $pcs += @{ id = $i; ip = $ip; mac = $mac }
            Write-Host "  PC${i}: $ip  MAC=$mac" -ForegroundColor Green
            $i++
        }
    }

    # Add self
    $myMAC = (Get-NetAdapter | Where-Object { $_.Status -eq "Up" -and $_.MacAddress -notlike "00-50-56*" }).MacAddress
    $pcs += @{ id = $i; ip = $myIP; mac = $myMAC }
    Write-Host "  PC${i}: $myIP  MAC=$myMAC (this PC)" -ForegroundColor Cyan

    if ($pcs.Count -eq 0) {
        Write-Host "[ERROR] No PCs found" -ForegroundColor Red
        return
    }

    $pcs | ConvertTo-Json -Depth 3 | Out-File $ConfigFile -Encoding UTF8
    Write-Host "`nFound $($pcs.Count) PCs. Saved to $ConfigFile" -ForegroundColor Cyan
}

# --- Action: Run command on all PCs ---
function Run-OnAll {
    param([scriptblock]$Script, [string]$Label = "command")

    $pcs = Load-PCs
    if (-not $pcs) { return }

    $ips = $pcs | ForEach-Object { $_.ip }
    Write-Host "[$Label] Running on $($ips.Count) PCs..." -ForegroundColor Cyan

    $results = Invoke-Command -ComputerName $ips -Credential $Cred -ScriptBlock $Script -ErrorAction SilentlyContinue -ErrorVariable errs

    foreach ($r in $results) {
        $pcid = ($pcs | Where-Object { $_.ip -eq $r.PSComputerName }).id
        Write-Host "  PC$pcid ($($r.PSComputerName)): $r" -ForegroundColor Green
    }

    foreach ($e in $errs) {
        $failIP = $e.TargetObject
        $pcid = ($pcs | Where-Object { $_.ip -eq $failIP }).id
        Write-Host "  PC$pcid ($failIP): FAILED - $($e.Exception.Message)" -ForegroundColor Red
    }
}

# --- Action: Status ---
function Get-Status {
    Run-OnAll -Label "STATUS" -Script {
        $proc = Get-Process python -ErrorAction SilentlyContinue
        $hostname = $env:COMPUTERNAME
        if ($proc) {
            "$hostname : python running ($($proc.Count) processes)"
        } else {
            "$hostname : IDLE (no python)"
        }
    }
}

# --- Action: Start collection ---
function Start-Collection {
    $pcs = Load-PCs
    if (-not $pcs) { return }

    foreach ($pc in $pcs) {
        $machineId = $pc.id
        Write-Host "  Starting PC$machineId ($($pc.ip))..." -ForegroundColor Yellow
        Invoke-Command -ComputerName $pc.ip -Credential $Cred -ScriptBlock {
            param($repo, $mid)
            cd $repo
            $proc = Get-Process python -ErrorAction SilentlyContinue
            if ($proc) {
                "$env:COMPUTERNAME : already running, skip"
            } else {
                Start-Process -FilePath "cmd.exe" -ArgumentList "/c start.bat $mid" -WindowStyle Hidden
                "$env:COMPUTERNAME : started machine $mid"
            }
        } -ArgumentList $RepoPath, $machineId
    }
}

# --- Action: Git pull ---
function Git-Pull {
    Run-OnAll -Label "GIT PULL" -Script {
        param($repo)
        cd $repo
        $result = git pull 2>&1
        "$env:COMPUTERNAME : $result"
    }
}

# --- Action: Stop collection ---
function Stop-Collection {
    Run-OnAll -Label "STOP" -Script {
        Stop-Process -Name python -Force -ErrorAction SilentlyContinue
        "$env:COMPUTERNAME : python stopped"
    }
}

# --- Action: Shutdown ---
function Shutdown-All {
    Write-Host "WARNING: This will shutdown all lab PCs!" -ForegroundColor Red
    $confirm = Read-Host "Type 'yes' to confirm"
    if ($confirm -ne "yes") {
        Write-Host "Cancelled." -ForegroundColor Yellow
        return
    }
    Run-OnAll -Label "SHUTDOWN" -Script {
        shutdown /s /t 60 /c "central control shutdown"
        "$env:COMPUTERNAME : shutting down in 60s"
    }
}

# --- Action: Wake-on-LAN ---
function Wake-All {
    $pcs = Load-PCs
    if (-not $pcs) { return }

    Write-Host "[WAKE] Sending WOL packets..." -ForegroundColor Cyan
    foreach ($pc in $pcs) {
        $mac = $pc.mac
        if (-not $mac) {
            Write-Host "  PC$($pc.id): no MAC address, skip" -ForegroundColor Yellow
            continue
        }
        $macBytes = $mac -split "[-:]" | ForEach-Object { [byte]("0x$_") }
        $packet = [byte[]](,0xFF * 6) + ($macBytes * 16)
        $udp = New-Object System.Net.Sockets.UdpClient
        $udp.Connect(([System.Net.IPAddress]::Broadcast), 9)
        $udp.Send($packet, $packet.Length) | Out-Null
        $udp.Close()
        Write-Host "  PC$($pc.id) ($($pc.ip)): WOL sent" -ForegroundColor Green
    }
}

# --- Action: Disable sleep ---
function Disable-Sleep {
    Run-OnAll -Label "NOSLEEP" -Script {
        powercfg /change standby-timeout-ac 0
        powercfg /change hibernate-timeout-ac 0
        "$env:COMPUTERNAME : sleep disabled"
    }
}

# --- Action: Custom command ---
function Run-Custom {
    param([string]$cmd)
    if (-not $cmd) {
        Write-Host "[ERROR] Usage: .\central_control.ps1 run `"command`"" -ForegroundColor Red
        return
    }
    $sb = [scriptblock]::Create($cmd)
    Run-OnAll -Label "CUSTOM" -Script $sb
}

# --- Main ---
switch ($Action.ToLower()) {
    "discover" { Discover-PCs }
    "status"   { Get-Status }
    "start"    { Start-Collection }
    "pull"     { Git-Pull }
    "stop"     { Stop-Collection }
    "shutdown" { Shutdown-All }
    "wake"     { Wake-All }
    "nosleep"  { Disable-Sleep }
    "run"      { Run-Custom -cmd $CustomCommand }
    default {
        Write-Host "Usage: .\central_control.ps1 <action>" -ForegroundColor Cyan
        Write-Host "  discover  - Scan network for lab PCs"
        Write-Host "  status    - Check collection status"
        Write-Host "  start     - Start collection"
        Write-Host "  pull      - Git pull on all PCs"
        Write-Host "  stop      - Stop collection"
        Write-Host "  shutdown  - Shutdown all PCs"
        Write-Host "  wake      - Wake all PCs (WOL)"
        Write-Host "  nosleep   - Disable sleep mode"
        Write-Host "  run `"cmd`" - Run custom command"
    }
}
