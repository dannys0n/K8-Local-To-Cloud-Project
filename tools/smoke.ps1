param(
    [int]$Messages = 5,
    [string]$ServerHost = "127.0.0.1",
    [int]$Port = 9000
)

$ErrorActionPreference = "Stop"
$Client = [System.Net.Sockets.TcpClient]::new()
$Client.Connect($ServerHost, $Port)
$Stream = $Client.GetStream()
$Writer = [System.IO.StreamWriter]::new($Stream, [System.Text.UTF8Encoding]::new($false), 1024, $true)
$Reader = [System.IO.StreamReader]::new($Stream, [System.Text.Encoding]::UTF8, $false, 1024, $true)
$Writer.AutoFlush = $true
try {
    $Writer.WriteLine("@location any")
    if ([string]::IsNullOrWhiteSpace($Reader.ReadLine())) { throw "Handshake failed" }
    1..$Messages | ForEach-Object {
        $Writer.WriteLine("smoke-$_")
        $Response = $Reader.ReadLine()
        if ([string]::IsNullOrWhiteSpace($Response)) { throw "No response received" }
        Write-Host $Response
    }
    $Writer.WriteLine("@location 2")
    $Routed = $Reader.ReadLine() | ConvertFrom-Json
    if ($Routed.location_id -ne 2) { throw "Runtime route change failed" }
    $Writer.WriteLine("smoke-routed")
    $Response = $Reader.ReadLine()
    $Body = $Response | ConvertFrom-Json
    if ($Body.location_id -ne 2) { throw "Message used wrong route" }
    Write-Host $Response
} finally {
    $Writer.Dispose()
    $Reader.Dispose()
    $Stream.Dispose()
    $Client.Dispose()
}
