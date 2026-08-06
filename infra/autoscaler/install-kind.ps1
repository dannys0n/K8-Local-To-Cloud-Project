$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$MetricsServer = "https://github.com/kubernetes-sigs/metrics-server/releases/download/v0.8.1/components.yaml"
$Operator = "https://github.com/jthomperoo/custom-pod-autoscaler-operator/releases/download/v1.4.2/cluster.yaml"

function Invoke-Kubectl {
    & kubectl @Args
    if ($LASTEXITCODE -ne 0) {
        throw "kubectl failed with exit code $LASTEXITCODE"
    }
}

Invoke-Kubectl apply -f $MetricsServer
Invoke-Kubectl patch deployment metrics-server -n kube-system --type strategic --patch-file "$Root/infra/autoscaler/metrics-server-kind-patch.yaml"
Invoke-Kubectl rollout status deployment/metrics-server -n kube-system --timeout=180s

Invoke-Kubectl apply --server-side --force-conflicts -f $Operator
Invoke-Kubectl wait --for=condition=Established customresourcedefinition/custompodautoscalers.custompodautoscaler.com --timeout=180s
Invoke-Kubectl rollout status deployment/custom-pod-autoscaler-operator -n default --timeout=180s
