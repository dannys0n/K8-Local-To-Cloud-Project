$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Cluster = "tcp-lab"
$ServerImage = "simple-tcp-server:dev"
$GatewayImage = "tcp-gateway:dev"
$AutoscalerImage = "tcp-server-autoscaler:dev"
$LoadgenImage = "tcp-loadgen:dev"
$ValkeyImage = "valkey/valkey:8.1-alpine"

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
        kind create cluster --config infra/kind/cluster.yaml
    } else {
        Write-Host "kind cluster '$Cluster' already exists."
    }

    foreach ($NodeResource in @(kubectl get nodes -l tcp-lab.io/database=true -o name)) {
        $NodeName = $NodeResource -replace '^node/', ''
        if (-not [string]::IsNullOrWhiteSpace($NodeName)) {
            kubectl taint node $NodeName tcp-lab.io/database- 2>$null
            kubectl label node $NodeName tcp-lab.io/database- 2>$null
        }
    }

    Write-Host "Building application and load-generator images..."
    docker build -t $ServerImage app/server
    docker build -t $GatewayImage app/gateway
    docker build --provenance=false -t $AutoscalerImage infra/autoscaler
    docker build -t $LoadgenImage tools/loadgen
    docker build --provenance=false -t prom/prometheus:v3.13.1 infra/autoscaler/prometheus
    docker build --provenance=false -t $ValkeyImage infra/valkey

    Write-Host "Loading image into kind..."
    kind load docker-image $ServerImage $GatewayImage $AutoscalerImage prom/prometheus:v3.13.1 $ValkeyImage --name $Cluster

    Write-Host "Installing autoscaling dependencies..."
    & "$Root/infra/autoscaler/install-kind.ps1"

    Write-Host "Applying Kubernetes resources..."
    kubectl apply -f deploy/base/namespace.yaml
    kubectl delete job/valkey-cluster-init -n tcp-lab --ignore-not-found
    kubectl apply -k deploy/overlays/kind
    kubectl rollout status statefulset/valkey -n tcp-lab --timeout=180s
    kubectl wait --for=condition=Complete job/valkey-cluster-init -n tcp-lab --timeout=180s
    kubectl rollout restart deployment/tcp-server -n tcp-lab
    kubectl rollout restart deployment/gateway -n tcp-lab

    kubectl rollout status deployment/tcp-server -n tcp-lab --timeout=180s
    kubectl rollout status deployment/gateway -n tcp-lab --timeout=180s
    kubectl rollout status deployment/prometheus -n tcp-lab --timeout=180s
    kubectl rollout status deployment/gateway-autoscaler -n tcp-lab --timeout=180s
    kubectl rollout status deployment/tcp-server-autoscaler -n tcp-lab --timeout=180s

    Write-Host ""
    Write-Host "Ready."
    Write-Host "TCP endpoint: 127.0.0.1:9000"
    Write-Host "Gateway stats: http://127.0.0.1:8404/"
    Write-Host "Map client: powershell -ExecutionPolicy Bypass -File tools/client.ps1"
} finally {
    Pop-Location
}
