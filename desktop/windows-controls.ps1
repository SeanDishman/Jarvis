$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$request = [Console]::In.ReadToEnd() | ConvertFrom-Json
switch ($request.action) {
    'brightness_get' {
        $screens = @(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness | Select-Object InstanceName, CurrentBrightness)
        @{ok=$true; displays=$screens} | ConvertTo-Json -Depth 4 -Compress
    }
    'brightness_set' {
        $level = [int]$request.value
        if ($level -lt 0 -or $level -gt 100) { throw 'Invalid brightness' }
        $screens = @(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods)
        if ($screens.Count -eq 0) { throw 'This monitor does not expose brightness control' }
        foreach ($screen in $screens) {
            Invoke-CimMethod -InputObject $screen -MethodName WmiSetBrightness -Arguments @{Timeout=[uint32]1; Brightness=[byte]$level} | Out-Null
        }
        $actual = @(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness | Select-Object InstanceName, CurrentBrightness)
        @{ok=$true; displays=$actual} | ConvertTo-Json -Depth 4 -Compress
    }
    'clipboard_read' {
        $text = [string](Get-Clipboard -Raw)
        @{ok=$true; text=$text.Substring(0,[Math]::Min($text.Length,8000)); truncated=($text.Length -gt 8000)} | ConvertTo-Json -Compress
    }
    'clipboard_write' {
        $text = [string]$request.value
        if ($text.Length -gt 4000) { throw 'Clipboard text too long' }
        Set-Clipboard -Value $text
        @{ok=$true; message='Copied the dictated text to your clipboard.'} | ConvertTo-Json -Compress
    }
    default { throw 'Unknown Windows control' }
}
