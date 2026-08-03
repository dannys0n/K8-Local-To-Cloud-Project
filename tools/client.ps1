param(
    [string]$GatewayHost = "127.0.0.1",
    [int]$GatewayPort = 9000,
    [int]$ListenPort = 0,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$Arguments = @(
    (Join-Path $PSScriptRoot "client.py"),
    "--gateway-host", $GatewayHost,
    "--gateway-port", $GatewayPort,
    "--listen-port", $ListenPort
)
if ($NoBrowser) { $Arguments += "--no-browser" }

if (Get-Command python -ErrorAction SilentlyContinue) {
    & python @Arguments
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 @Arguments
} else {
    throw "Python 3 is required for the browser client."
}
