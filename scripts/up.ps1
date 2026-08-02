$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Cluster = "tcp-lab"
$Image = "simple-tcp-server:dev"

foreach ($Command in @("docker", "kind", "kubectl")) {
    if (-not (Get-Command $Command -ErrorAction SilentlyContinue)) {
        throw "Required command not found: $Command"
    }
}

Push-Location $Root
try {
    $Clusters = @(& kind get clusters --quiet)

    if ($LASTEXITCODE -ne 0) {
        $Clusters = @()
    }
    if ($Clusters -notcontains $Cluster) {
        Write-Host "Creating kind cluster '$Cluster'..."
        kind create cluster --config kind/cluster.yaml
    } else {
        Write-Host "kind cluster '$Cluster' already exists."
    }

    Write-Host "Building $Image..."
    docker build -t $Image app/server

    Write-Host "Loading image into kind..."
    kind load docker-image $Image --name $Cluster

    Write-Host "Applying Kubernetes resources..."
    kubectl apply -k deploy/overlays/kind
    kubectl rollout restart deployment/tcp-server -n tcp-lab

    kubectl rollout status deployment/tcp-server -n tcp-lab --timeout=180s
    kubectl rollout status deployment/haproxy -n tcp-lab --timeout=180s

    Write-Host ""
    Write-Host "Ready."
    Write-Host "TCP endpoint: 127.0.0.1:9000"
    Write-Host "HAProxy stats: http://127.0.0.1:8404/stats"
    Write-Host "Run: powershell -ExecutionPolicy Bypass -File tools/client.ps1"
} finally {
    Pop-Location
}
