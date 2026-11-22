$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$allowed = @{
    'Get-Date' = @('Microsoft.PowerShell.Utility', 'Date,Format,UFormat,Year,Month,Day,Hour,Minute,Second')
    'Get-TimeZone' = @('Microsoft.PowerShell.Management', 'Id,Name,ListAvailable')
    'Get-Process' = @('Microsoft.PowerShell.Management', 'Name,Id')
    'Get-Service' = @('Microsoft.PowerShell.Management', 'Name,DisplayName,DependentServices,RequiredServices')
    'Get-ComputerInfo' = @('Microsoft.PowerShell.Management', 'Property')
    'Get-HotFix' = @('Microsoft.PowerShell.Management', 'Id,Description')
    'Get-CimInstance' = @('CimCmdlets', 'ClassName,Property')
    'Get-NetAdapter' = @('NetAdapter', 'Name,InterfaceDescription,Physical,IncludeHidden')
    'Get-NetIPAddress' = @('NetTCPIP', 'InterfaceIndex,InterfaceAlias,IPAddress,AddressFamily,AddressState,Type')
    'Get-NetIPConfiguration' = @('NetTCPIP', 'InterfaceIndex,InterfaceAlias,All,Detailed')
    'Get-NetRoute' = @('NetTCPIP', 'InterfaceIndex,InterfaceAlias,DestinationPrefix,AddressFamily')
    'Get-NetTCPConnection' = @('NetTCPIP', 'LocalAddress,LocalPort,RemoteAddress,RemotePort,State,OwningProcess')
    'Get-DnsClientServerAddress' = @('DnsClient', 'InterfaceIndex,InterfaceAlias,AddressFamily')
    'Get-NetConnectionProfile' = @('NetConnection', 'InterfaceIndex,InterfaceAlias,Name')
    'Get-Volume' = @('Storage', 'DriveLetter,FileSystemLabel')
    'Select-Object' = @('Microsoft.PowerShell.Utility', 'Property,ExcludeProperty,ExpandProperty,First,Last,Skip,Unique')
    'Sort-Object' = @('Microsoft.PowerShell.Utility', 'Property,Descending,Unique')
    'Where-Object' = @('Microsoft.PowerShell.Core', 'Property,Value,EQ,NE,GT,GE,LT,LE,Like,NotLike,Contains,NotContains')
    'Measure-Object' = @('Microsoft.PowerShell.Utility', 'Property,Sum,Average,Minimum,Maximum,Character,Word,Line')
    'Format-Table' = @('Microsoft.PowerShell.Utility', 'Property,AutoSize,HideTableHeaders,Wrap')
    'Format-List' = @('Microsoft.PowerShell.Utility', 'Property')
}
$cimClasses = @('Win32_OperatingSystem','Win32_ComputerSystem','Win32_Processor',
    'Win32_VideoController','Win32_LogicalDisk','Win32_Battery','Win32_PhysicalMemory')

function Read-Literal($node) {
    if ($node -is [System.Management.Automation.Language.StringConstantExpressionAst]) { return $node.Value }
    if ($node -is [System.Management.Automation.Language.ConstantExpressionAst]) { return $node.Value }
    if ($node -is [System.Management.Automation.Language.ArrayLiteralAst]) {
        $values = @($node.Elements | ForEach-Object { Read-Literal $_ })
        return ,$values
    }
    throw 'Only literal parameter values are supported. Scripts, variables and expressions cannot run.'
}

try {
    $request = [Console]::In.ReadToEnd() | ConvertFrom-Json
    if ($request.command -isnot [string] -or $request.command.Length -gt 4000) { throw 'Invalid PowerShell command.' }
    $tokens = $null; $parseErrors = $null
    $tree = [System.Management.Automation.Language.Parser]::ParseInput($request.command, [ref]$tokens, [ref]$parseErrors)
    if ($parseErrors.Count -or $tree.ParamBlock -or $tree.BeginBlock -or $tree.ProcessBlock -or $tree.DynamicParamBlock -or
        -not $tree.EndBlock -or $tree.EndBlock.Statements.Count -lt 1 -or $tree.EndBlock.Statements.Count -gt 4 -or $tree.EndBlock.Traps.Count) {
        throw 'Use one to four simple PowerShell diagnostic pipelines.'
    }
    $pipelines = @()
    foreach ($statement in $tree.EndBlock.Statements) {
        if ($statement -isnot [System.Management.Automation.Language.PipelineAst] -or $statement.PipelineElements.Count -gt 6) {
            throw 'Only diagnostic command pipelines are supported.'
        }
        $commands = @()
        foreach ($node in $statement.PipelineElements) {
            if ($node -isnot [System.Management.Automation.Language.CommandAst] -or $node.Redirections.Count -or $node.InvocationOperator -ne 'Unknown') {
                throw 'Scripts, redirection and executable invocation are unavailable.'
            }
            $name = $node.GetCommandName()
            if (-not $name -or -not $allowed.ContainsKey($name)) {
                throw "Unsupported diagnostic command: $name. PowerShell supports local read-only Windows diagnostics; use the dedicated app and file tools for changes."
            }
            if ($commands.Count -eq 0 -and -not $name.StartsWith('Get-', [StringComparison]::OrdinalIgnoreCase)) {
                throw 'Start each pipeline with a supported Get command.'
            }
            if ($commands.Count -gt 0 -and $name.StartsWith('Get-', [StringComparison]::OrdinalIgnoreCase)) {
                throw 'Use a separate pipeline for each Get command.'
            }
            $arguments = @(); $className = $null
            $parameters = $allowed[$name][1].Split(',')
            $resolved = Get-Command ($allowed[$name][0] + '\' + $name) -ErrorAction Stop
            for ($i=1; $i -lt $node.CommandElements.Count; $i++) {
                $element = $node.CommandElements[$i]
                if ($element -is [System.Management.Automation.Language.CommandParameterAst]) {
                    $parameter = $element.ParameterName
                    if ($parameter -notin $parameters) { throw "Unsupported parameter $parameter for $name." }
                    $isSwitch = $resolved.Parameters[$parameter].ParameterType -eq [System.Management.Automation.SwitchParameter]
                    $hasValue = $false; $value = $null
                    if ($element.Argument) { $value = Read-Literal $element.Argument; $hasValue = $true }
                    elseif (-not $isSwitch -and $i+1 -lt $node.CommandElements.Count -and $node.CommandElements[$i+1] -isnot [System.Management.Automation.Language.CommandParameterAst]) {
                        $i++; $value = Read-Literal $node.CommandElements[$i]; $hasValue = $true
                    }
                    $arguments += @{name=$parameter; value=$value; hasValue=$hasValue}
                    if ($parameter -eq 'ClassName') { $className = $value }
                } else {
                    if ($name -eq 'Get-CimInstance') { throw 'Get-CimInstance requires a named ClassName parameter.' }
                    $arguments += @{name=$null; value=(Read-Literal $element); hasValue=$true}
                }
            }
            if ($name -eq 'Get-CimInstance' -and $className -notin $cimClasses) { throw 'That CIM class is outside local hardware diagnostics.' }
            $commands += @{name=($allowed[$name][0] + '\' + $name); arguments=$arguments}
        }
        $pipelines += ,$commands
    }
    $output = ''; $truncated = $false
    foreach ($commands in $pipelines) {
        $engine = [PowerShell]::Create()
        try {
            foreach ($command in $commands) {
                $null = $engine.AddCommand($command.name)
                foreach ($argument in $command.arguments) {
                    if ($null -eq $argument.name) { $null = $engine.AddArgument($argument.value) }
                    elseif ($argument.hasValue) { $null = $engine.AddParameter($argument.name, $argument.value) }
                    else { $null = $engine.AddParameter($argument.name) }
                }
            }
            $null = $engine.AddCommand('Microsoft.PowerShell.Utility\Select-Object').AddParameter('First', 201)
            $rows = $engine.Invoke()
            if ($engine.HadErrors) { throw (($engine.Streams.Error | Select-Object -First 1).ToString()) }
            if ($rows.Count -gt 200) { $truncated = $true }
            $output += ($rows | Select-Object -First 200 | Out-String -Width 160)
        } finally { $engine.Dispose() }
    }
    if ($output.Length -gt 12000) { $output = $output.Substring(0,12000); $truncated = $true }
    @{ok=$true; output=$output.Trim(); truncated=$truncated; readOnly=$true} | ConvertTo-Json -Compress
} catch {
    $message = $_.Exception.Message
    if ($message.Length -gt 500) { $message = $message.Substring(0,500) }
    @{ok=$false; error=$message} | ConvertTo-Json -Compress
}
