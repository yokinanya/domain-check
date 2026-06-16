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

如果 `domain-check` 不在 `PATH`，可以在 `.env` 中配置完整路径：

```bash
DOMAIN_CHECK_BIN=/home/you/.cargo/bin/domain-check
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
export DOMAIN_WATCH_INTERVAL_SECONDS=86400
export DOMAIN_WATCH_EXPIRED_INTERVAL_SECONDS=86400
export DOMAIN_PERIOD=1
export DOMAIN_WATCH_STATE_FILE="domain_watch_state.json"
export DOMAIN_CHECK_BIN="domain-check"
```

监听频率规则：

- `DOMAIN_WATCH_INTERVAL_SECONDS`：普通监听间隔，默认 86400 秒（1 天）。
- `DOMAIN_WATCH_EXPIRED_INTERVAL_SECONDS`：已过期域名的监听间隔，默认 86400 秒（1 天）。
- 默认策略不区分普通期和临近期；域名过期前后都使用低频查询，因为目标域名通常不是热门域名。
- 如果你确实想加快某些过期域名的监听频率，可以把 `DOMAIN_WATCH_EXPIRED_INTERVAL_SECONDS` 改小，例如 `3600`（1 小时）或 `600`（10 分钟）。
- 脚本会通过 `domain-check --info --json` 读取过期时间；只要返回的过期时间仍在未来，就不会调用腾讯云 API。
- 如果某个域名过期时间未知，会打印 `expires_at=unknown`，并继续使用普通监听间隔。
- 如果 `domain-check --info --json` 返回域名状态码，脚本会把状态码写入状态文件；首次记录只建立基线，后续状态码变化时会发送推送通知。
- 已提交注册任务的域名会从监听列表中移除，状态持久化到 `DOMAIN_WATCH_STATE_FILE`，重启后不再重复查询。
- 如果你想让同一个域名重新开始监听，可以手动编辑状态文件，把它从 `active` 加回；或临时删除状态文件。

推送配置使用 `onepush`，不配置 `ONEPUSH_PROVIDER` 时不会推送：

```bash
export ONEPUSH_PROVIDER=bark
export ONEPUSH_PARAMS_JSON='{"key":"你的 Bark key"}'
export ONEPUSH_TITLE_PREFIX='[domain-watch] '
```

`ONEPUSH_PARAMS_JSON` 会原样传给 `onepush.notify()`，不同渠道需要的参数不同，参考 onepush 文档。
推送事件包括腾讯云注册前确认、注册任务提交，以及 RDAP/WHOIS 状态码变化。状态码变化通知只会在已有历史状态后触发，避免首次启动时产生批量通知。

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

脚本启动时会自动读取当前目录的 `.env`。脚本会持续监听，不设置最大轮数。已提交注册任务的域名会从监听列表移除并写入 `domain_watch_state.json`，避免进程重启后重复查询和提交。所有活跃域名都被移除后，监听循环会自动结束。

只测试腾讯云查询接口，不提交注册：

```bash
uv run scripts/check_tencent_domain.py
```

## 验证

```bash
ruff check src tests
uv run -m pytest tests
```
