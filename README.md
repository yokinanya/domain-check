# domain-watch

监听 `.env` 中配置的域名，当域名可注册时通过腾讯云域名注册 API 自动提交注册任务。

脚本流程：

1. 使用 `domain-check` CLI 做 RDAP/WHOIS 可用性预检查。
2. 对预检查可用的域名调用腾讯云 `CheckDomain` 做注册前确认。
3. 对腾讯云确认 `Available=true` 的域名调用 `CreateDomainBatch`，使用账户余额自动注册。

## 安装

安装 `domain-check` CLI：

```bash
cargo install domain-check
```

安装 Python 依赖：

```bash
uv sync
```

## 配置

复制示例配置后填写真实值：

```bash
cp .env.example .env
```

必填环境变量：

```bash
export TENCENTCLOUD_SECRET_ID="你的 SecretId"
export TENCENTCLOUD_SECRET_KEY="你的 SecretKey"
export TENCENT_DOMAIN_TEMPLATE_ID="已实名审核通过的信息模板 ID"
export DOMAIN_WATCH_DOMAINS="example.com,example.net"
```

可选环境变量：

```bash
export DOMAIN_WATCH_INTERVAL_SECONDS=3600
export DOMAIN_NEAR_EXPIRY_INTERVAL_SECONDS=5
export DOMAIN_EXPIRY_ACCELERATION_DAYS=7
export DOMAIN_PERIOD=1
```

监听频率规则：

- `DOMAIN_WATCH_INTERVAL_SECONDS`：普通监听间隔，默认 3600 秒。
- `DOMAIN_NEAR_EXPIRY_INTERVAL_SECONDS`：临近过期时的监听间隔，默认 5 秒。
- `DOMAIN_EXPIRY_ACCELERATION_DAYS`：距离过期多少天内切换到高频监听，默认 7 天。
- 脚本会通过 `domain-check --info --json` 读取过期时间；只要返回的过期时间仍在未来，就不会调用腾讯云 API。
- 如果某个域名过期时间未知，会打印 `expires_at=unknown`，并继续使用普通监听间隔。

推送配置使用 `onepush`，不配置 `ONEPUSH_PROVIDER` 时不会推送：

```bash
export ONEPUSH_PROVIDER=bark
export ONEPUSH_PARAMS_JSON='{"key":"你的 Bark key"}'
export ONEPUSH_TITLE_PREFIX='[domain-watch] '
```

`ONEPUSH_PARAMS_JSON` 会原样传给 `onepush.notify()`，不同渠道需要的参数不同，参考 onepush 文档。

注册参数固定为：

- `PayMode=1`：使用账户余额付费
- `AutoRenewFlag=0`：关闭自动续费
- `UpdateProhibition=0`：不开启更新锁
- `TransferProhibition=0`：不开启转移锁
- `ChannelFrom=pc`
- `OrderFrom=common`

## 运行

```bash
uv run domain_watch.py
```

脚本启动时会自动读取当前目录的 `.env`。脚本会持续监听，不设置最大轮数。已在当前进程提交过注册任务的域名会记录在内存里，避免同一进程重复提交。

只测试腾讯云查询接口，不提交注册：

```bash
uv run scripts/check_tencent_domain.py
```

## 验证

```bash
ruff check .
uv run -m py_compile domain_watch.py domain_check_info.py env_loader.py push_notify.py tencent_domain.py scripts/check_tencent_domain.py
uv run -m pytest tests
```
