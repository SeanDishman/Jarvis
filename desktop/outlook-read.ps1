$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [Text.Encoding]::UTF8
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$request = [Console]::In.ReadToEnd() | ConvertFrom-Json
try {
    $outlook = New-Object -ComObject Outlook.Application
    $namespace = $outlook.GetNamespace('MAPI')
    $accounts = @()
    foreach ($account in $namespace.Accounts) {
        $address = [string]$account.SmtpAddress
        $provider = if ($address -match '@(gmail|googlemail)\.com$') { 'gmail' } else { 'outlook' }
        $accounts += [pscustomobject]@{ address=$address; provider=$provider; account=$account }
    }
    if ($request.action -eq 'accounts') {
        @{ok=$true; accounts=@($accounts | Select-Object address,provider)} | ConvertTo-Json -Depth 5 -Compress
        exit 0
    }
    if ($request.action -eq 'read') {
        $item = $namespace.GetItemFromID([string]$request.entryId, [string]$request.storeId)
        if ($item.Class -ne 43) { throw 'Not a mail message' }
        $body = [string]$item.Body
        @{ok=$true; subject=[string]$item.Subject; from=[string]$item.SenderName; body=$body.Substring(0,[Math]::Min(16000,$body.Length))} | ConvertTo-Json -Depth 5 -Compress
        exit 0
    }
    if ($request.action -ne 'search') { throw 'Unknown read-only operation' }
    $found = @()
    $presentProviders = @($accounts | ForEach-Object { $_.provider } | Select-Object -Unique)
    $missingProviders = @($request.providers | Where-Object { $presentProviders -notcontains $_ })
    $watch = [Diagnostics.Stopwatch]::StartNew()
    $foldersVisited = 0
    $truncated = $false
    foreach ($entry in $accounts) {
        if ($request.providers -notcontains $entry.provider) { continue }
        $accountMatches = 0
        $store = $entry.account.DeliveryStore
        $folders = New-Object 'System.Collections.Generic.Queue[object]'
        if ([string]::IsNullOrWhiteSpace($request.query)) { $folders.Enqueue($store.GetDefaultFolder(6)) }
        else { $folders.Enqueue($store.GetRootFolder()) }
        while ($folders.Count -gt 0 -and $foldersVisited -lt 50 -and $watch.Elapsed.TotalSeconds -lt 18) {
            $folder = $folders.Dequeue()
            $foldersVisited++
            if (-not [string]::IsNullOrWhiteSpace($request.query)) {
                foreach ($child in $folder.Folders) { $folders.Enqueue($child) }
            }
            if ($folder.DefaultItemType -ne 0) { continue }
            $items = $folder.Items
            $items.Sort('[ReceivedTime]', $true)
            if ($request.unreadOnly) { $items = $items.Restrict('[UnRead] = true') }
            $count = [Math]::Min(500, $items.Count)
            for ($index=1; $index -le $count; $index++) {
                if ($watch.Elapsed.TotalSeconds -gt 18) { $truncated=$true; break }
                $item = $items.Item($index)
                if ($item.Class -ne 43) { continue }
                $body = [string]$item.Body
                $haystack = ([string]$item.Subject) + ' ' + ([string]$item.SenderName) + ' ' + ([string]$item.SenderEmailAddress) + ' ' + $body
                if ($request.query -and $haystack.IndexOf([string]$request.query, [StringComparison]::OrdinalIgnoreCase) -lt 0) { continue }
                $found += [pscustomobject]@{entryId=[string]$item.EntryID; storeId=[string]$store.StoreID; provider=$entry.provider; from=[string]$item.SenderName; subject=[string]$item.Subject; date=$item.ReceivedTime.ToString('o'); snippet=$body.Substring(0,[Math]::Min(350,$body.Length)); unread=[bool]$item.UnRead}
                $accountMatches++
                if ([string]::IsNullOrWhiteSpace($request.query) -and $accountMatches -ge $request.limit) { break }
            }
            if ($items.Count -gt 500) { $truncated=$true }
        }
        if ($folders.Count -gt 0) { $truncated=$true }
    }
    $result = @{ok=($missingProviders.Count -eq 0); messages=@($found | Sort-Object date -Descending | Select-Object -First ([int]$request.limit)); scope='Outlook classic, latest 500 messages per folder, up to 50 folders'; truncated=$truncated; missingProviders=$missingProviders}
    if ($missingProviders.Count -gt 0) {
        $result.error = 'Not connected in Outlook classic: ' + ($missingProviders -join ', ') + '. Add those accounts in Email settings.'
    }
    $result | ConvertTo-Json -Depth 6 -Compress
} catch {
    @{ok=$false; error='Outlook could not read mail. Open Outlook classic and finish account setup or its access prompt.'} | ConvertTo-Json -Compress
}
