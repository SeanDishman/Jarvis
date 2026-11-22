$ErrorActionPreference = 'Stop'
$physical = @(Get-NetAdapter -Physical | Where-Object { $_.Status -eq 'Up' })
$tunnels = @(Get-NetAdapter | Where-Object {
    $_.Status -eq 'Up' -and -not $_.HardwareInterface -and
    ($_.InterfaceDescription -match 'VPN|Tunnel|WireGuard|Wintun|TAP|TUN|Proton|Nord|Surfshark|Cisco|AnyConnect|Mullvad|Express')
})
$rows = @()
foreach ($adapter in $physical) {
    $config = Get-NetIPConfiguration -InterfaceIndex $adapter.ifIndex
    if (-not $config.IPv4DefaultGateway) { continue }
    foreach ($address in $config.IPv4Address) {
        if ($address.IPAddress -notmatch '^169\.254\.') {
            $rows += @{ index = [int]$adapter.ifIndex; address = $address.IPAddress }
        }
    }
}
@{ adapters = $rows; tunnelActive = ($tunnels.Count -gt 0) } | ConvertTo-Json -Compress -Depth 4
