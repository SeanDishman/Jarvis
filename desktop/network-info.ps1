$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$adapters = @()
foreach ($adapter in @(Get-NetAdapter | Where-Object Status -eq 'Up')) {
    $addresses = @(Get-NetIPAddress -InterfaceIndex $adapter.ifIndex -ErrorAction SilentlyContinue |
        Where-Object AddressState -eq 'Preferred' | ForEach-Object {
            @{ address=$_.IPAddress; family=[string]$_.AddressFamily; prefixLength=$_.PrefixLength }
        })
    $vpn = -not $adapter.HardwareInterface -and
        ($adapter.Name + ' ' + $adapter.InterfaceDescription) -match 'VPN|WireGuard|Wintun|TAP|TUN|Proton|Nord|Surfshark|Cisco|AnyConnect|Mullvad|Express'
    $adapters += @{name=$adapter.Name; index=[int]$adapter.ifIndex; physical=[bool]$adapter.HardwareInterface;
                   vpnHint=[bool]$vpn; addresses=$addresses}
}
@{adapters=$adapters; vpnDetected=[bool](@($adapters | Where-Object vpnHint).Count)} | ConvertTo-Json -Compress -Depth 5
