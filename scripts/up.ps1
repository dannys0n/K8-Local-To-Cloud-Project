$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Cluster = "tcp-lab"
$ServerImage = "simple-tcp-server:dev"
$GatewayImage = "tcp-gateway:dev"
$AutoscalerImage = "tcp-server-autoscaler:dev"
$LoadgenImage = "tcp-loadgen:dev"

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

    $DatabaseNode = "$Cluster-worker"
    $ExistingDatabaseNodes = @(
        (kubectl get nodes -l tcp-lab.io/database=true -o jsonpath='{.items[*].metadata.name}') -split '\s+' |
            Where-Object { $_ }
    )
    if ($ExistingDatabaseNodes | Where-Object { $_ -ne $DatabaseNode }) {
        throw "Database node placement changed. Recreate the kind cluster before running up.ps1 so the node-local PostgreSQL volume is not stranded."
    }
    Write-Host "Labeling and tainting kind database node '$DatabaseNode'..."
    kubectl label node $DatabaseNode tcp-lab.io/database=true --overwrite
    kubectl taint node $DatabaseNode tcp-lab.io/database=true:NoSchedule --overwrite

    Write-Host "Building application and load-generator images..."
    docker build -t $ServerImage app/server
    docker build -t $GatewayImage app/gateway
    docker build -t $AutoscalerImage infra/autoscaler
    docker build -t $LoadgenImage tools/loadgen

    Write-Host "Loading image into kind..."
    kind load docker-image $ServerImage $GatewayImage $AutoscalerImage --name $Cluster

    Write-Host "Installing autoscaling dependencies..."
    & "$Root/infra/autoscaler/install-kind.ps1"

    Write-Host "Applying Kubernetes resources..."
    kubectl apply -f deploy/base/namespace.yaml
    kubectl delete job/tcp-server-schema -n tcp-lab --ignore-not-found
    kubectl apply -k deploy/overlays/kind
    kubectl wait --for=condition=Complete job/tcp-server-schema -n tcp-lab --timeout=180s
    kubectl rollout restart deployment/tcp-server -n tcp-lab
    kubectl rollout restart deployment/gateway -n tcp-lab

    kubectl rollout status deployment/tcp-server -n tcp-lab --timeout=180s
    kubectl rollout status deployment/gateway -n tcp-lab --timeout=180s
    kubectl wait --for=condition=Ready pod -n tcp-lab -l app=tcp-server-autoscaler --timeout=180s

    Write-Host ""
    Write-Host "Ready."
    Write-Host "TCP endpoint: 127.0.0.1:9000"
    Write-Host "Gateway stats: http://127.0.0.1:8404/"
    Write-Host "Map client: powershell -ExecutionPolicy Bypass -File tools/client.ps1"
} finally {
    Pop-Location
}
