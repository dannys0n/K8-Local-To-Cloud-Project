$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Cluster = "tcp-lab"
$ServerImage = "simple-tcp-server:dev"
$GatewayImage = "tcp-gateway:dev"
$AutoscalerImage = "tcp-server-autoscaler:dev"
$LoadgenImage = "tcp-loadgen:dev"
$ValkeyAutoscalerImage = "valkey-shard-autoscaler:dev"

foreach ($Command in @("docker", "kind", "kubectl", "helm")) {
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

    $DatabaseNodes = @(kubectl get nodes -l tcp-lab.io/database=true -o name)
    if ($DatabaseNodes.Count -ne 3) {
        throw "Cluster topology is outdated: expected 3 dedicated database workers, found $($DatabaseNodes.Count). Recreate the kind cluster."
    }
    $LabNamespace = kubectl get namespace/tcp-lab --ignore-not-found -o name
    if (-not [string]::IsNullOrWhiteSpace($LabNamespace) -and
        -not [string]::IsNullOrWhiteSpace((kubectl get statefulset/valkey -n tcp-lab --ignore-not-found -o name))) {
        throw "The cluster contains the retired hand-managed Valkey StatefulSet. Recreate the kind cluster before installing the operator."
    }

    Write-Host "Building application and load-generator images..."
    docker build -t $ServerImage app/server
    docker build -t $GatewayImage app/gateway
    docker build --provenance=false -t $AutoscalerImage infra/autoscaler
    docker build -t $LoadgenImage tools/loadgen
    docker build --provenance=false -t prom/prometheus:v3.13.1 infra/autoscaler/prometheus
    docker build --provenance=false -t $ValkeyAutoscalerImage infra/valkey/autoscaler

    Write-Host "Loading image into kind..."
    kind load docker-image $ServerImage $GatewayImage $AutoscalerImage prom/prometheus:v3.13.1 $ValkeyAutoscalerImage --name $Cluster

    Write-Host "Installing autoscaling dependencies and Valkey Operator in parallel..."
    $AutoscalingJob = Start-Job -ScriptBlock {
        param($InstallScript)
        & powershell -NoProfile -ExecutionPolicy Bypass -File $InstallScript
        if ($LASTEXITCODE -ne 0) {
            throw "Autoscaling dependency installation failed with exit code $LASTEXITCODE"
        }
    } -ArgumentList "$Root/infra/autoscaler/install-kind.ps1"
    $OperatorJob = Start-Job -ScriptBlock {
        param($WorkingDirectory)
        Set-Location $WorkingDirectory
        & helm repo add valkey https://valkey.io/valkey-helm --force-update
        if ($LASTEXITCODE -ne 0) {
            throw "Helm repository update failed with exit code $LASTEXITCODE"
        }
        & helm upgrade --install valkey-operator valkey/valkey-operator --version 0.4.0 `
            --namespace valkey-operator-system --create-namespace `
            --values infra/valkey/operator-values.yaml --wait --timeout 3m
        if ($LASTEXITCODE -ne 0) {
            throw "Valkey Operator installation failed with exit code $LASTEXITCODE"
        }
    } -ArgumentList $Root
    $StartupJobs = @($AutoscalingJob, $OperatorJob)
    $StartupJobs | Wait-Job | Out-Null
    $FailedJobs = @($StartupJobs | Where-Object State -ne "Completed")
    $StartupJobs | Receive-Job -ErrorAction Continue
    $StartupJobs | Remove-Job
    if ($FailedJobs.Count -gt 0) {
        throw "One or more parallel infrastructure installations failed."
    }

    Write-Host "Applying Kubernetes resources..."
    kubectl apply -f deploy/base/namespace.yaml
    kubectl apply -k deploy/overlays/kind
    kubectl wait --for=condition=Ready valkeycluster/tcp-lab -n tcp-lab --timeout=300s
    kubectl rollout restart deployment/tcp-server -n tcp-lab
    kubectl rollout restart deployment/gateway -n tcp-lab

    kubectl rollout status deployment/tcp-server -n tcp-lab --timeout=180s
    kubectl rollout status deployment/gateway -n tcp-lab --timeout=180s
    kubectl rollout status deployment/prometheus -n tcp-lab --timeout=180s
    kubectl rollout status deployment/gateway-autoscaler -n tcp-lab --timeout=180s
    kubectl rollout status deployment/tcp-server-autoscaler -n tcp-lab --timeout=180s
    kubectl rollout status deployment/valkey-shard-autoscaler -n tcp-lab --timeout=180s

    Write-Host ""
    Write-Host "Ready."
    Write-Host "TCP endpoint: 127.0.0.1:9000"
    Write-Host "Gateway stats: http://127.0.0.1:8404/"
    Write-Host "Map client: powershell -ExecutionPolicy Bypass -File tools/client.ps1"
} finally {
    Pop-Location
}
