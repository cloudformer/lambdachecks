output "url" {
  description = "Healthchecks: open in a browser; ping URLs are <url>ping/<uuid>"
  value       = aws_lambda_function_url.server.function_url
}

output "manage_function" {
  description = "aws lambda invoke --function-name <this> --payload '{\"manage\": [...]}' runs manage.py"
  value       = aws_lambda_function.manage.function_name
}

output "db_url" {
  description = "s3rds' console: Healthchecks' tables and rows (sign in with db_user / db_password)"
  value       = aws_lambda_function_url.db.function_url
}

output "db_user" {
  value = local.db_user
}

output "db_password" {
  description = "terraform -chdir=terraform output -raw db_password"
  value       = random_password.db.result
  sensitive   = true
}
