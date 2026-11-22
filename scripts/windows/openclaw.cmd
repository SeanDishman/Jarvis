@echo off
setlocal
set "PATH=%~dp0..\..\.runtime\node-v24.21.0-win-x64;%PATH%"
set "OPENCLAW_STATE_DIR=%~dp0..\..\.openclaw"
set "OPENCLAW_CONFIG_PATH=%~dp0..\..\.openclaw\openclaw.json"
set "npm_config_cache=%~dp0..\..\.cache\npm"
set "JARVIS_TMP_DIR=%~dp0..\..\.openclaw\tmp-root"
set "XDG_CACHE_HOME=%~dp0..\..\.cache"
"%~dp0..\..\.runtime\node-v24.21.0-win-x64\node.exe" "%~dp0..\..\.runtime\openclaw\node_modules\openclaw\openclaw.mjs" %*
exit /b %errorlevel%
