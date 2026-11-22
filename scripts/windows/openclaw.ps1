$ErrorActionPreference = 'Stop'
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$nodeDir = Join-Path $projectRoot '.runtime\node-v24.21.0-win-x64'
$env:PATH = "$nodeDir;$env:PATH"
$env:OPENCLAW_STATE_DIR = Join-Path $projectRoot '.openclaw'
$env:OPENCLAW_CONFIG_PATH = Join-Path $env:OPENCLAW_STATE_DIR 'openclaw.json'
$env:npm_config_cache = Join-Path $projectRoot '.cache\npm'
$env:JARVIS_TMP_DIR = Join-Path $projectRoot '.openclaw\tmp-root'
$env:XDG_CACHE_HOME = Join-Path $projectRoot '.cache'
$entry = Join-Path $projectRoot '.runtime\openclaw\node_modules\openclaw\openclaw.mjs'
& (Join-Path $nodeDir 'node.exe') $entry @args
exit $LASTEXITCODE
