param(
    [string]$ServerHost = "127.0.0.1",
    [int]$Port = 9000
)

$ErrorActionPreference = "Stop"
$Client = [System.Net.Sockets.TcpClient]::new()
$Client.Connect($ServerHost, $Port)
$Stream = $Client.GetStream()
$Writer = [System.IO.StreamWriter]::new($Stream, [System.Text.Encoding]::UTF8, 1024, $true)
$Reader = [System.IO.StreamReader]::new($Stream, [System.Text.Encoding]::UTF8, $false, 1024, $true)
$Writer.AutoFlush = $true

Write-Host "Connected to ${ServerHost}:$Port. Enter messages; Ctrl+C exits."
try {
    while ($true) {
        $Message = Read-Host ">"
        if ([string]::IsNullOrWhiteSpace($Message)) { continue }
        $Writer.WriteLine($Message)
        $Response = $Reader.ReadLine()
        if ($null -eq $Response) {
            Write-Host "Connection closed by server."
            break
        }
        Write-Host $Response
    }
} finally {
    $Writer.Dispose()
    $Reader.Dispose()
    $Stream.Dispose()
    $Client.Dispose()
}
