data "aws_caller_identity" "me" {}

locals {
  role    = "${var.name}-lambda-role"
  db_role = "${var.name}-db-lambda-role"
  prefix  = "db/" # s3rds' fixed layout: s3://<bucket>/db/<db>.sqlite
  db_user = var.name
  env = {
    SECRET_KEY        = var.secret_key
    DB_HOST           = trimsuffix(trimprefix(aws_lambda_function_url.db.function_url, "https://"), "/")
    DB_NAME           = var.db_name
    DB_USER           = local.db_user
    DB_PASSWORD       = random_password.db.result
    REGISTRATION_OPEN = var.registration_open ? "True" : "False"
  }
}

# ---------------- packaging: both Lambdas' code, both architectures ----------------
# scripts/build_server.py stages terraform/.build/{server,db}-<arch> at plan time (Docker; cached after the first run)
data "external" "build" {
  program = ["python3", "${path.module}/../scripts/build_server.py"]
}

data "archive_file" "server" {
  type        = "zip"
  source_dir  = data.external.build.result["server-${var.architecture}"]
  output_path = "${path.module}/.build/server-${var.architecture}.zip"
}

data "archive_file" "db" {
  type        = "zip"
  source_dir  = data.external.build.result["db-${var.architecture}"]
  output_path = "${path.module}/.build/db-${var.architecture}.zip"
}

# ================ the database: s3rds (its released server), private to this deployment ================

# every database file lives in this one private bucket
resource "aws_s3_bucket" "db" {
  bucket        = "${var.name}-db-${data.aws_caller_identity.me.account_id}" # global name; the account id keeps it unique
  force_destroy = true                                                       # destroy must work: it deletes the data too
}

resource "aws_s3_bucket_public_access_block" "db" {
  bucket                  = aws_s3_bucket.db.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# s3rds' user / password: generated, only Healthchecks and you (terraform output) know it
resource "random_password" "db" {
  length  = 32
  special = false
}

resource "aws_iam_role" "db" {
  name = local.db_role
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "db" {
  name = "${local.db_role}-policy"
  role = aws_iam_role.db.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:*:*:*"
      },
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject"]
        Resource = "${aws_s3_bucket.db.arn}/${local.prefix}*.sqlite"
      },
      {
        Effect    = "Allow"
        Action    = ["s3:ListBucket"]
        Resource  = aws_s3_bucket.db.arn
        Condition = { StringLike = { "s3:prefix" = ["${local.prefix}*"] } }
      },
    ]
  })
}

resource "aws_lambda_function" "db" {
  function_name    = "${var.name}-db-lambda"
  role             = aws_iam_role.db.arn
  runtime          = "python3.12" # s3rds' own runtime (scripts/build_server.py)
  architectures    = [var.architecture]
  handler          = "lambda_function.lambda_handler"
  filename         = data.archive_file.db.output_path
  source_code_hash = data.archive_file.db.output_base64sha256
  timeout          = 15
  memory_size      = 256

  # s3rds: one writer globally, no concurrent writes by construction (the ETag CAS in its code is the second guard)
  reserved_concurrent_executions = 1

  environment {
    variables = {
      DB_BUCKET   = aws_s3_bucket.db.bucket
      DB_PREFIX   = local.prefix
      DB_USER     = local.db_user
      DB_PASSWORD = random_password.db.result
      CONSOLE     = "1" # s3rds' console at db_url: Healthchecks' tables and rows
    }
  }
}

# public, like s3rds' default: its user / password is the gate. A public URL needs BOTH permission statements
# (AWS requirement since Oct 2025).
resource "aws_lambda_function_url" "db" {
  function_name      = aws_lambda_function.db.function_name
  authorization_type = "NONE"
}

resource "aws_lambda_permission" "db_url" {
  statement_id           = "AllowPublicFunctionUrl"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.db.function_name
  principal              = "*"
  function_url_auth_type = "NONE"
}

resource "aws_lambda_permission" "db_url_invoke" {
  statement_id             = "AllowPublicFunctionUrlInvoke"
  action                   = "lambda:InvokeFunction"
  function_name            = aws_lambda_function.db.function_name
  principal                = "*"
  invoked_via_function_url = true
}

# ================ Healthchecks ================

# reaches the database over HTTPS with s3rds' credentials: logs, and starting the manage function (setup page)
resource "aws_iam_role" "lambda" {
  name = local.role
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "lambda" {
  name = "${local.role}-policy"
  role = aws_iam_role.lambda.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:*:*:*"
      },
      {
        Effect   = "Allow"
        Action   = ["lambda:InvokeFunction"]
        Resource = aws_lambda_function.manage.arn
      },
    ]
  })
}

# the web: Healthchecks (UI, API, ping URLs) behind a Function URL
resource "aws_lambda_function" "server" {
  function_name    = "${var.name}-lambda"
  role             = aws_iam_role.lambda.arn
  runtime          = "python3.13"
  architectures    = [var.architecture]
  handler          = "lambda_function.lambda_handler"
  filename         = data.archive_file.server.output_path
  source_code_hash = data.archive_file.server.output_base64sha256
  timeout          = 30
  memory_size      = var.memory_size

  environment {
    # SITE_ROOT comes from the first request (lambda_function.py); the setup page starts migrate in the manage function
    variables = merge(local.env, { MANAGE_FUNCTION = "${var.name}-manage" })
  }
}

# public: Healthchecks' own login is the gate for the UI, ping URLs are public by design
resource "aws_lambda_function_url" "server" {
  function_name      = aws_lambda_function.server.function_name
  authorization_type = "NONE"
}

resource "aws_lambda_permission" "url" {
  statement_id           = "AllowPublicFunctionUrl"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.server.function_name
  principal              = "*"
  function_url_auth_type = "NONE"
}

resource "aws_lambda_permission" "url_invoke" {
  statement_id             = "AllowPublicFunctionUrlInvoke"
  action                   = "lambda:InvokeFunction"
  function_name            = aws_lambda_function.server.function_name
  principal                = "*"
  invoked_via_function_url = true
}

# manage.py: the same package, no URL, 15 minutes. The setup page starts migrate here; by hand:
# aws lambda invoke --function-name <name>-manage --payload '{"manage": ["migrate", "--noinput"]}' ...
resource "aws_lambda_function" "manage" {
  function_name    = "${var.name}-manage"
  role             = aws_iam_role.lambda.arn
  runtime          = "python3.13"
  architectures    = [var.architecture]
  handler          = "lambda_function.manage_handler"
  filename         = data.archive_file.server.output_path
  source_code_hash = data.archive_file.server.output_base64sha256
  timeout          = 900
  memory_size      = var.memory_size

  reserved_concurrent_executions = 1 # one migrate at a time; a second click waits for the first

  environment {
    variables = merge(local.env, { SITE_ROOT = trimsuffix(aws_lambda_function_url.server.function_url, "/") })
  }
}

# a failed migrate is not retried behind your back: the setup page shows the progress, you click again
resource "aws_lambda_function_event_invoke_config" "manage" {
  function_name          = aws_lambda_function.manage.function_name
  maximum_retry_attempts = 0
}
