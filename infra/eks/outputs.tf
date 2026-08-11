output "cluster_name" {
  value = module.eks.cluster_name
}

output "aws_region" {
  value = var.aws_region
}

output "ecr_repositories" {
  value = { for name, repository in aws_ecr_repository.workload : name => repository.repository_url }
}

output "ecr_server_repository" {
  value = aws_ecr_repository.workload["server"].repository_url
}

output "ecr_gateway_repository" {
  value = aws_ecr_repository.workload["gateway"].repository_url
}

output "ecr_autoscaler_repository" {
  value = aws_ecr_repository.workload["autoscaler"].repository_url
}

output "valkey_address" {
  value = "${aws_elasticache_serverless_cache.valkey.endpoint[0].address}:${aws_elasticache_serverless_cache.valkey.endpoint[0].port}"
}

output "valkey_tls" {
  value = true
}

output "nlb_source_cidrs" {
  value = var.nlb_source_cidrs
}

output "nlb_source_cidrs_csv" {
  value = join(",", var.nlb_source_cidrs)
}

output "configure_kubectl" {
  value = "aws eks update-kubeconfig --name ${module.eks.cluster_name} --region ${var.aws_region}${var.aws_profile == null ? "" : " --profile ${var.aws_profile}"}"
}
