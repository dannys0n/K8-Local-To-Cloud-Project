param(
    [string]$ServerHost = "127.0.0.1",
    [ValidateSet("any", "los-angeles", "new-york", "london", "singapore", "frankfurt")]
    [string]$Location = "any",
    [int]$Port = 9000
)

$ErrorActionPreference = "Stop"

function Close-Connection($Connection) {
    if ($null -eq $Connection) { return }
    if ($null -ne $Connection.Reader) { $Connection.Reader.Dispose() }
    if ($null -ne $Connection.Writer) { $Connection.Writer.Dispose() }
    if ($null -ne $Connection.Client) { $Connection.Client.Dispose() }
}

function Open-Connection {
    while ($true) {
        try {
            $Client = [System.Net.Sockets.TcpClient]::new()
            $Client.Connect($ServerHost, $Port)
            $Stream = $Client.GetStream()
            $Writer = [System.IO.StreamWriter]::new($Stream, [System.Text.Encoding]::UTF8, 1024, $true)
            $Reader = [System.IO.StreamReader]::new($Stream, [System.Text.Encoding]::UTF8, $false, 1024, $true)
            $Writer.AutoFlush = $true
            $Writer.WriteLine("@location $Location")
            $Hello = $Reader.ReadLine() | ConvertFrom-Json
            if ($Location -ne "any" -and $Hello.location -ne $Location) { throw "Location handshake failed" }
            Write-Host "Connected to $($Hello.server) ($($Hello.location)) via ${ServerHost}:$Port."
            return @{ Client = $Client; Writer = $Writer; Reader = $Reader }
        } catch {
            if ($null -ne $Client) { $Client.Dispose() }
            Write-Warning "Connect failed: $($_.Exception.Message). Retrying in 1 second."
            Start-Sleep -Seconds 1
        }
    }
}

Write-Host "Location: $Location. Enter messages; Ctrl+C exits."
$Connection = $null
try {
    while ($true) {
        $Message = Read-Host ">"
        if ([string]::IsNullOrWhiteSpace($Message)) { continue }
        while ($true) {
            try {
                if ($null -eq $Connection) { $Connection = Open-Connection }
                $Connection.Writer.WriteLine($Message)
                $Response = $Connection.Reader.ReadLine()
                if ($null -eq $Response) { throw "Connection closed" }
                Write-Host $Response
                break
            } catch {
                Write-Warning "Disconnected: $($_.Exception.Message). Reconnecting."
                Close-Connection $Connection
                $Connection = $null
                Start-Sleep -Seconds 1
            }
        }
    }
} finally {
    Close-Connection $Connection
}
