param(
    [ValidateSet("infra-up", "ecr-login", "push", "deploy", "up", "status", "down")]
    [string]$Action = "status",
    [string]$ImageTag = "",
    [string]$TerraformArgs = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$TerraformDirectory = Join-Path $Root "infra/eks"
$RuntimeOverlay = Join-Path $Root ".generated/eks"

function Require-Command([string]$Name) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command '$Name' was not found on PATH."
    }
}

function Invoke-TerraformOutput([string]$Name) {
    $Value = & terraform "-chdir=$TerraformDirectory" output -raw $Name
    if ($LASTEXITCODE -ne 0) { throw "Unable to read Terraform output '$Name'." }
    return $Value.Trim()
}

function Get-AwsProfile {
    $ConfigureCommand = Invoke-TerraformOutput "configure_kubectl"
    if ($ConfigureCommand -match '--profile\s+([^\s]+)') {
        return $Matches[1].Trim('"')
    }
    return $env:AWS_PROFILE
}

function Connect-Ecr([string]$Registry, [string]$Region, [string]$Profile) {
    $AwsArguments = @("ecr", "get-login-password", "--region", $Region)
    if ($Profile) { $AwsArguments += @("--profile", $Profile) }
    [string]$Token = ((& aws @AwsArguments) | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) { throw "Unable to retrieve an ECR login token." }

    # Windows PowerShell can alter native pipeline input. Write the token
    # directly to Docker's stdin so the ECR password remains byte-safe.
    $StartInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $StartInfo.FileName = "docker"
    $StartInfo.Arguments = "login --username AWS --password-stdin $Registry"
    $StartInfo.UseShellExecute = $false
    $StartInfo.RedirectStandardInput = $true
    $StartInfo.RedirectStandardOutput = $true
    $StartInfo.RedirectStandardError = $true

    $Process = [System.Diagnostics.Process]::Start($StartInfo)
    # ECR tokens are ASCII. Writing bytes avoids the UTF-8 BOM emitted by the
    # .NET Framework stream writer used by Windows PowerShell 5.1.
    $TokenBytes = [System.Text.Encoding]::ASCII.GetBytes($Token)
    $Process.StandardInput.BaseStream.Write($TokenBytes, 0, $TokenBytes.Length)
    $Process.StandardInput.BaseStream.Flush()
    $Process.StandardInput.Close()
    $Process.WaitForExit()
    $Output = $Process.StandardOutput.ReadToEnd().Trim()
    $ErrorOutput = $Process.StandardError.ReadToEnd().Trim()

    if ($Process.ExitCode -ne 0) {
        throw "ECR login failed: $ErrorOutput"
    }
    if ($Output) { Write-Host $Output }
}

function Enable-EcrCredentialHelper([string]$Registry, [string]$Profile) {
    if (-not (Get-Command docker-credential-ecr-login -ErrorAction SilentlyContinue)) {
        return $false
    }

    $DockerDirectory = Join-Path $env:USERPROFILE ".docker"
    $DockerConfigPath = Join-Path $DockerDirectory "config.json"
    New-Item -ItemType Directory -Force -Path $DockerDirectory | Out-Null

    if (Test-Path $DockerConfigPath) {
        $DockerConfig = Get-Content -LiteralPath $DockerConfigPath -Raw | ConvertFrom-Json
    } else {
        $DockerConfig = [pscustomobject]@{}
    }
    if (-not $DockerConfig.PSObject.Properties["credHelpers"]) {
        $DockerConfig | Add-Member -MemberType NoteProperty -Name credHelpers -Value ([pscustomobject]@{})
    }
    if ($DockerConfig.credHelpers.PSObject.Properties[$Registry]) {
        $DockerConfig.credHelpers.$Registry = "ecr-login"
    } else {
        $DockerConfig.credHelpers | Add-Member -MemberType NoteProperty -Name $Registry -Value "ecr-login"
    }

    $Json = $DockerConfig | ConvertTo-Json -Depth 20
    [System.IO.File]::WriteAllText($DockerConfigPath, $Json, [System.Text.UTF8Encoding]::new($false))
    if ($Profile) { $env:AWS_PROFILE = $Profile }
    Write-Host "Using Docker's ECR credential helper with AWS profile '$Profile'."
    return $true
}

function Initialize-Infrastructure {
    Require-Command terraform
    Require-Command aws
    & terraform "-chdir=$TerraformDirectory" init
    if ($LASTEXITCODE -ne 0) { throw "terraform init failed." }

    $ExtraArgs = @()
    if ($TerraformArgs) { $ExtraArgs = $TerraformArgs -split "\s+" }
    & terraform "-chdir=$TerraformDirectory" apply @ExtraArgs
    if ($LASTEXITCODE -ne 0) { throw "terraform apply failed." }

    $Cluster = Invoke-TerraformOutput "cluster_name"
    $Region = Invoke-TerraformOutput "aws_region"
    $Profile = Get-AwsProfile
    $AwsArguments = @("eks", "update-kubeconfig", "--name", $Cluster, "--region", $Region)
    if ($Profile) { $AwsArguments += @("--profile", $Profile) }
    & aws @AwsArguments
    if ($LASTEXITCODE -ne 0) { throw "Unable to configure kubectl for EKS." }
}

function Resolve-ImageTag {
    if ($ImageTag) { return $ImageTag }
    $Revision = (& git -C $Root rev-parse --short=12 HEAD).Trim()
    return "$Revision-$([DateTime]::UtcNow.ToString('yyyyMMddHHmmss'))"
}

function Push-Images([string]$Tag) {
    Require-Command docker
    Require-Command aws
    Require-Command terraform

    $Region = Invoke-TerraformOutput "aws_region"
    $ServerRepository = Invoke-TerraformOutput "ecr_server_repository"
    $GatewayRepository = Invoke-TerraformOutput "ecr_gateway_repository"
    $AutoscalerRepository = Invoke-TerraformOutput "ecr_autoscaler_repository"
    $Registry = $ServerRepository.Split("/")[0]
    $Profile = Get-AwsProfile

    if (-not (Enable-EcrCredentialHelper $Registry $Profile)) {
        Connect-Ecr $Registry $Region $Profile
    }

    $Builds = @(
        @{ Repository = $ServerRepository; Directory = "app/server" },
        @{ Repository = $GatewayRepository; Directory = "app/gateway" },
        @{ Repository = $AutoscalerRepository; Directory = "infra/autoscaler" }
    )
    foreach ($Build in $Builds) {
        & docker buildx build --platform linux/amd64,linux/arm64 --push -t "$($Build.Repository):$Tag" (Join-Path $Root $Build.Directory)
        if ($LASTEXITCODE -ne 0) { throw "Image build failed for $($Build.Repository)." }
    }
}

function Write-RuntimeOverlay([string]$Tag) {
    $ServerRepository = Invoke-TerraformOutput "ecr_server_repository"
    $GatewayRepository = Invoke-TerraformOutput "ecr_gateway_repository"
    $AutoscalerRepository = Invoke-TerraformOutput "ecr_autoscaler_repository"
    $Cidrs = (Invoke-TerraformOutput "nlb_source_cidrs_csv").Split(",")
    $SourceRanges = ($Cidrs | ForEach-Object { "          - $_" }) -join "`n"

    New-Item -ItemType Directory -Force -Path $RuntimeOverlay | Out-Null
    $Manifest = @"
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - ../../deploy/overlays/eks
images:
  - name: simple-tcp-server
    newName: $ServerRepository
    newTag: $Tag
  - name: tcp-gateway
    newName: $GatewayRepository
    newTag: $Tag
  - name: tcp-server-autoscaler
    newName: $AutoscalerRepository
    newTag: $Tag
patches:
  - target:
      kind: Service
      name: gateway
    patch: |-
      - op: add
        path: /spec/loadBalancerSourceRanges
        value:
$SourceRanges
"@
    Set-Content -LiteralPath (Join-Path $RuntimeOverlay "kustomization.yaml") -Value $Manifest -Encoding utf8
}

function Deploy-Workloads([string]$Tag) {
    Require-Command kubectl
    Require-Command terraform

    $Address = Invoke-TerraformOutput "valkey_address"
    & kubectl apply -f (Join-Path $Root "deploy/base/namespace.yaml")
    if ($LASTEXITCODE -ne 0) { throw "Unable to create the namespace." }

    $Secret = & kubectl create secret generic tcp-server-valkey -n tcp-lab --from-literal="addresses=$Address" --from-literal="tls=true" --dry-run=client -o yaml
    $Secret | & kubectl apply -f -
    if ($LASTEXITCODE -ne 0) { throw "Unable to apply the Valkey connection Secret." }

    & kubectl apply -k (Join-Path $Root "infra/autoscaler/prometheus")
    if ($LASTEXITCODE -ne 0) { throw "Unable to install Prometheus." }

    Write-RuntimeOverlay $Tag
    & kubectl apply -k $RuntimeOverlay
    if ($LASTEXITCODE -ne 0) { throw "Unable to deploy the EKS workload overlay." }

    & kubectl apply -k (Join-Path $Root "infra/observability-eks")
    if ($LASTEXITCODE -ne 0) { throw "Unable to install EKS observability." }
}

function Remove-KubernetesResources {
    if (-not (Get-Command kubectl -ErrorAction SilentlyContinue)) {
        Write-Warning "kubectl was not found; Terraform will still destroy the AWS infrastructure."
        return
    }

    & kubectl --request-timeout=10s get namespace tcp-lab *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "The EKS API is unavailable; skipping Kubernetes cleanup and continuing with Terraform."
        return
    }

    # Delete the public Service first. Its finalizer removes the AWS NLB before
    # Terraform starts dismantling the cluster networking.
    & kubectl delete service gateway -n tcp-lab --ignore-not-found=true --wait=true --timeout=5m
    if ($LASTEXITCODE -ne 0) { Write-Warning "Gateway NLB cleanup was incomplete." }

    & kubectl delete -k (Join-Path $Root "infra/observability-eks") --ignore-not-found=true --wait=true --timeout=3m
    if ($LASTEXITCODE -ne 0) { Write-Warning "Observability cleanup was incomplete." }

    & kubectl delete -k (Join-Path $Root "infra/autoscaler/prometheus") --ignore-not-found=true --wait=true --timeout=3m
    if ($LASTEXITCODE -ne 0) { Write-Warning "Prometheus cleanup was incomplete." }

    if (Test-Path (Join-Path $RuntimeOverlay "kustomization.yaml")) {
        & kubectl delete -k $RuntimeOverlay --ignore-not-found=true --wait=true --timeout=5m
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Workload cleanup was incomplete; Terraform destroy will still be attempted."
        }
    }
}

switch ($Action) {
    "infra-up" { Initialize-Infrastructure }
    "ecr-login" {
        $Region = Invoke-TerraformOutput "aws_region"
        $Repository = Invoke-TerraformOutput "ecr_server_repository"
        $Registry = $Repository.Split("/")[0]
        $Profile = Get-AwsProfile
        if (-not (Enable-EcrCredentialHelper $Registry $Profile)) {
            Connect-Ecr $Registry $Region $Profile
        }
    }
    "push" {
        $Tag = Resolve-ImageTag
        Push-Images $Tag
        Write-Host "Pushed immutable image tag: $Tag"
    }
    "deploy" {
        if (-not $ImageTag) { throw "deploy requires -ImageTag with a tag already pushed to ECR." }
        Deploy-Workloads $ImageTag
    }
    "up" {
        Initialize-Infrastructure
        $Tag = Resolve-ImageTag
        Push-Images $Tag
        Deploy-Workloads $Tag
        Write-Host "EKS lab deployed with image tag: $Tag"
    }
    "status" {
        Require-Command kubectl
        & kubectl get nodes
        & kubectl get pods,service,pdb -n tcp-lab -o wide
    }
    "down" {
        Require-Command terraform
        Remove-KubernetesResources

        $ExtraArgs = @()
        if ($TerraformArgs) { $ExtraArgs = $TerraformArgs -split "\s+" }
        & terraform "-chdir=$TerraformDirectory" destroy @ExtraArgs
        if ($LASTEXITCODE -ne 0) { throw "terraform destroy failed; billable resources may remain." }
    }
}
