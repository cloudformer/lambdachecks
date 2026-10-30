# lambdachecks —— 跑在 AWS Lambda 上的 Healthchecks，空闲 ≈ $0

> **[Healthchecks](https://github.com/healthchecks/healthchecks)**（cron / 定时任务监控）通常跑在一台常开的服务器和数据库上。
> **lambdachecks** 把同一个 Healthchecks 版本**原样**跑在一个 AWS Lambda + Function URL 上，数据库用
> **[s3rds](https://github.com/cloudformer/s3rds)**（S3 上的 SQLite，serverless），一起作为一套部署。没人用时什么都不运行。变的只有宿主。

[English](README.md) · [更新日志](CHANGELOG.md) · [迁回普通部署](MIGRATION.md)

![架构](docs/architecture.svg)

| | Healthchecks 自建 | lambdachecks |
|---|---|---|
| 代码 | Healthchecks v5.0.3 | 同一版本，原样（只加 `hc/local_settings.py`） |
| Web 服务 | gunicorn，常开 | 一个 Lambda + Function URL（一个小 WSGI 适配器） |
| 数据库 | PostgreSQL / SQLite 文件 | 同一套部署里的 s3rds v0.2.1：一个 Lambda + 一个私有 S3 桶（`"ENGINE": "s3rds.sqlite"`） |
| 静态文件 | whitenoise | whitenoise（构建时 collect + compress） |
| 空闲费用 / 月 | 一台 VM + 一个数据库 | ≈ $0 |
| 规模 | 不限 | 小规模：MVP、开发 / 测试（几十个 check） |

## 适用范围：小规模 —— MVP、个人项目、开发 / 测试环境

几十个 check、每分钟几次 ping、同一分钟扎堆几十个：空闲 ≈ $0，使用中每月几美分。每次写（每次 ping）都会上传一次
数据库文件，而且 s3rds 只有一个写入者，所以写会排队：
- 并发的 ping 依次等待（写入者忙时自动重试约 10 秒）；ping 客户端保留 `curl --retry 3`；
- 数据库越大，每次写越慢。

几百个 check、每秒都有 ping、或者告警必须在几秒内发出：换成普通的 Healthchecks + PostgreSQL（[MIGRATION.md](MIGRATION.md)）——
同一个版本，只换宿主和数据库。

## 状态：0.1.0

0.1.0 = Healthchecks 跑在 Lambda 上，UI 能登录（密码登录），数据库是 s3rds。还没有：告警 Lambda（EventBridge 每分钟跑一次
`sendalerts`）、端到端 ping、邮件、`sendreports` / 清理任务、和 Docker 里的 Healthchecks 的对比测试。各自随后续版本加入。

## 快速开始

需要：Terraform、运行中的 Docker（包在 AWS 的 Lambda Python 镜像里构建）、AWS CLI。一次 `terraform apply` 全部建好：
Healthchecks 的两个 Lambda，以及它的数据库——s3rds 发布版服务端作为一个 Lambda 加一个私有 S3 桶，密码自动生成。

### 1. 部署

```bash
cp terraform/example.json terraform/real-lambdachecks.json   # aws_profile、secret_key
terraform -chdir=terraform init
terraform -chdir=terraform apply -var-file=real-lambdachecks.json   # 第一次：在 Docker 里构建两种架构，要几分钟
terraform -chdir=terraform output                                   # url、manage_function、db_url
```

### 2. 在浏览器里初始化

打开 `url`。第一个账号建好之前，所有页面都是 setup 页；每一步都要输入 setup key，即变量文件里的 `secret_key`
（这样别人抢不走刚部署好的实例）：

1. **Run migrations**：在 `lambdachecks-manage` 函数里执行 Healthchecks 和 Django 的迁移（约 200 个）（先在 s3rds 里建库）。每条语句一次
   HTTPS 调用：要几分钟；页面会自动刷新，显示 `已执行 / 总数`。如果撞到函数 15 分钟上限，再点一次，会从停下的地方继续。
2. **Create the account**：第一个（超级）用户的邮箱和密码。建好后跳到登录页。

账号建好后 setup 页就消失。不用浏览器的话：
`aws lambda invoke --function-name lambdachecks-manage --cli-binary-format raw-in-base64-out --payload '{"manage": ["migrate", "--noinput"]}' /dev/stdout`
（任何 `manage.py` 命令都一样）。

### 3. 登录

在 `url` 用上面的邮箱和密码登录。数据库：打开 `db_url`（s3rds 控制台），用 var 文件里的
`db_console_auth_username` / `db_console_auth_password` 登录；Healthchecks 的表在 `healthchecks` 库里。

```bash
terraform -chdir=terraform destroy -var-file=real-lambdachecks.json   # 全部删除，包括数据库（S3 桶）
```

## 和普通 Healthchecks 的区别

- **登录**：目前只有密码登录（没配邮件，没有 magic link）；`registration_open` 默认 false。
- **并发写**：两个 ping 同时写同一个 s3rds 库，可能有一个失败（HTTP 500）；ping 客户端重试即可（`curl --retry 3`）。
  Healthchecks 用 SQLite 时也有同类行为（"database is locked"）。
- **冷启动**：隔一段时间后的第一个请求要加载 Django（几秒）。

## 仓库结构

```
src/server/       lambda_function.py（WSGI 适配器、setup 页、manage 入口）、local_settings.py（-> hc/local_settings.py）、requirements.txt
scripts/          build_server.py：两个包，每种架构一份（Terraform 调用）：Healthchecks + src/server、
                  s3rds 服务端（其发布版，原样）
terraform/        部署：Healthchecks 的 Lambda + s3rds（Lambda + S3 桶），一套
tests/            回归测试（适配器）；tests/compat/ 对比测试（之后）
```

---

**[IdleFree](https://cloudformer.github.io/#zh)** 系列之一 —— 把空闲也计费的 AWS 服务和自建应用，重建成空闲 ≈ $0。MIT。
