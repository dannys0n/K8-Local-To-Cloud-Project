$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$Operator = "https://github.com/jthomperoo/custom-pod-autoscaler-operator/releases/download/v1.4.2/cluster.yaml"

function Invoke-Kubectl {
    & kubectl @Args
    if ($LASTEXITCODE -ne 0) {
        throw "kubectl failed with exit code $LASTEXITCODE"
    }
}

Invoke-Kubectl apply -f "$Root/deploy/base/namespace.yaml"
Invoke-Kubectl apply -k "$Root/infra/autoscaler/prometheus"
Invoke-Kubectl rollout status deployment/prometheus -n tcp-lab --timeout=180s

Invoke-Kubectl apply --server-side --force-conflicts -f $Operator
Invoke-Kubectl wait --for=condition=Established customresourcedefinition/custompodautoscalers.custompodautoscaler.com --timeout=180s
Invoke-Kubectl rollout status deployment/custom-pod-autoscaler-operator -n default --timeout=180s
