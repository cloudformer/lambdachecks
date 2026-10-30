output "url" {
  description = "Healthchecks: open in a browser; ping URLs are <url>ping/<uuid>"
  value       = aws_lambda_function_url.server.function_url
}

output "manage_function" {
  description = "aws lambda invoke --function-name <this> --payload '{\"manage\": [...]}' runs manage.py"
  value       = aws_lambda_function.manage.function_name
}

output "db_url" {
  description = "s3rds' console: Healthchecks' tables and rows (sign in with db_console_auth_username / db_console_auth_password)"
  value       = aws_lambda_function_url.db.function_url
}
