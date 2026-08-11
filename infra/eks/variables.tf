variable "aws_region" {
  description = "AWS region in which to create the lab."
  type        = string
  default     = "us-east-1"
}

variable "aws_profile" {
  description = "Optional local AWS CLI profile. Leave null for the normal AWS credential chain."
  type        = string
  default     = null
  nullable    = true
}

variable "cluster_name" {
  description = "EKS cluster and resource-name prefix."
  type        = string
  default     = "tcp-lab"
}

variable "kubernetes_version" {
  description = "EKS Kubernetes minor version."
  type        = string
  default     = "1.33"
}

variable "vpc_cidr" {
  description = "CIDR allocated to the lab VPC."
  type        = string
  default     = "10.42.0.0/16"
}

variable "single_nat_gateway" {
  description = "Use one NAT gateway to reduce lab cost. Set false to remove this deliberate single-AZ dependency."
  type        = bool
  default     = true
}

variable "valkey_max_data_gb" {
  description = "Maximum ElastiCache Serverless data storage."
  type        = number
  default     = 10
}

variable "valkey_max_ecpu_per_second" {
  description = "Maximum ElastiCache Serverless processing capacity."
  type        = number
  default     = 5000
}

variable "nlb_source_cidrs" {
  description = "IPv4 CIDRs allowed to connect to the public gateway NLB. Restrict this before applying workloads."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "tags" {
  description = "Additional AWS resource tags."
  type        = map(string)
  default     = {}
}
