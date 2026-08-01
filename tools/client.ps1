param(
    [string]$ServerHost = "127.0.0.1",
    [ValidateSet("any", "los-angeles", "new-york", "london", "singapore", "frankfurt")]
    [string]$Location = "any",
    [int]$Port = 9000
)

$ErrorActionPreference = "Stop"
$Locations = @("any", "los-angeles", "new-york", "london", "singapore", "frankfurt")

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
            return @{ Client = $Client; Writer = $Writer; Reader = $Reader; Hello = $Hello }
        } catch {
            if ($null -ne $Client) { $Client.Dispose() }
            Write-Warning "Connect failed: $($_.Exception.Message). Retrying in 1 second."
            Start-Sleep -Seconds 1
        }
    }
}

Write-Host "Commands: /location NAME, /reconnect, /status, /locations, /help"
Write-Host "Selected location: $Location. Enter messages; Ctrl+C exits."
$Connection = Open-Connection
try {
    while ($true) {
        $Message = Read-Host ">"
        if ([string]::IsNullOrWhiteSpace($Message)) { continue }
        if ($Message -eq "/help") {
            Write-Host "/location NAME  switch location and reconnect"
            Write-Host "/reconnect      replace the current TCP connection"
            Write-Host "/status         show the selected and connected identity"
            Write-Host "/locations      list valid locations"
            continue
        }
        if ($Message -eq "/locations") {
            Write-Host ($Locations -join ", ")
            continue
        }
        if ($Message -eq "/status") {
            if ($null -eq $Connection) {
                Write-Host "Selected: $Location; connected: disconnected"
            } else {
                Write-Host "Selected: $Location; connected: $($Connection.Hello.server) ($($Connection.Hello.location))"
            }
            continue
        }
        if ($Message -eq "/reconnect" -or $Message -match "^/location\s+(\S+)\s*$") {
            if ($Message -ne "/reconnect") {
                $Requested = $Matches[1]
                if ($Locations -notcontains $Requested) {
                    Write-Warning "Unknown location: $Requested. Use /locations."
                    continue
                }
                $Location = $Requested
            }
            Close-Connection $Connection
            $Connection = Open-Connection
            continue
        }
        if ($Message.StartsWith("/")) {
            Write-Warning "Unknown command. Use /help."
            continue
        }
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
