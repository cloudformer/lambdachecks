# ---- deploy credentials ----
variable "region" {
  type    = string
  default = "us-east-2"
}

variable "aws_profile" {
  description = "Local AWS CLI profile to use; \"\" = default credential chain"
  type        = string
  default     = ""
}

variable "assume_role_arn" {
  description = "Deploy role to assume; \"\" = none. Pick this OR aws_profile."
  type        = string
  default     = ""

  validation {
    condition     = !(var.assume_role_arn != "" && var.aws_profile != "")
    error_message = "Set either aws_profile or assume_role_arn, not both."
  }
}

# ---- app ----
variable "env" {
  description = "Environment label (tags)"
  type        = string
  default     = "dev"
}

variable "project_name" {
  description = "Project name; resources are <project_name>-lambda, <project_name>-manage, <project_name>-db-lambda, <project_name>-db-<account id> (bucket), roles"
  type        = string
  default     = "lambdachecks"
}

variable "secret_key" {
  description = "Django's SECRET_KEY (sessions, CSRF, signed links); any long random string, keep it stable"
  type        = string
  sensitive   = true
}

variable "registration_open" {
  description = "Let anyone sign up from the login page (Healthchecks' REGISTRATION_OPEN). false = accounts via createsuperuser only."
  type        = bool
  default     = false
}

# ---- the database: s3rds, deployed with this stack ----
variable "db_name" {
  description = "Healthchecks' database in s3rds; the manage function creates it if missing"
  type        = string
  default     = "healthchecks"
}

variable "db_console_auth_username" {
  description = "s3rds' HTTP Basic user: Healthchecks connects with it, and you sign in to db_url's console with it"
  type        = string
}

variable "db_console_auth_password" {
  description = "s3rds' HTTP Basic password (Healthchecks and db_url's console)"
  type        = string
  sensitive   = true
}

# ---- the Lambda ----
variable "architecture" {
  description = "The architecture this deployment runs: arm64 (Graviton, ~20% cheaper) or x86_64; both are built"
  type        = string
  default     = "arm64"

  validation {
    condition     = contains(["arm64", "x86_64"], var.architecture)
    error_message = "architecture must be arm64 or x86_64."
  }
}

variable "memory_size" {
  description = "MB; more memory = more CPU = faster Django cold starts and password hashing"
  type        = number
  default     = 1024
}
