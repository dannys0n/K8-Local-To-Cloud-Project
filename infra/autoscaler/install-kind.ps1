$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

function Invoke-Kubectl {
    & kubectl @Args
    if ($LASTEXITCODE -ne 0) {
        throw "kubectl failed with exit code $LASTEXITCODE"
    }
}

Invoke-Kubectl apply -f "$Root/deploy/base/namespace.yaml"
Invoke-Kubectl apply -k "$Root/infra/autoscaler/prometheus"
Invoke-Kubectl rollout status deployment/prometheus -n tcp-lab --timeout=180s
