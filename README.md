# domain-watch

根据域名的 RDAP 生命周期预测释放窗口，并在腾讯云确认可注册后自动使用账户余额提交注册。

## 工作方式

1. 从 IANA bootstrap 发现域名后缀对应的权威 RDAP 服务。
2. 新域名立即查询一次过期时间，然后等待到精确 `expires_at`。
3. 到期后根据 RDAP 状态独立调度：过期/赎回期默认 6 小时，`pendingDelete` 默认 5 分钟。
4. 用首次发现 `pendingDelete` 的时间区间加默认 5 天，估算释放窗口；窗口开始后每 5 秒同时用权威 RDAP 和腾讯云 `CheckDomain` 交叉确认，不再等 RDAP 的 404 —— 注册局 RDAP 有缓存，404 可能滞后数小时，只等它就会错过秒级释放窗口。
5. 腾讯云 `CheckDomain` 是只读查询，不产生费用（只有提交注册的 `CreateDomainBatch` 才扣费），因此窗口内每 5 秒查询一次是安全的；一旦腾讯云确认可注册，立即逐域提交注册。
6. 持续查询腾讯云异步任务，只有详情状态为 `success` 才停止监听该域名。

RDAP 404 只表示注册局不存在该对象，不保证域名可售。`pendingDelete` 时长也可能因后缀策略不同而变化，因此释放窗口是估算值，可按 TLD 覆盖。

## 安装与配置

```bash
uv sync
cp .env.example .env
```

必填配置：

```bash
TENCENTCLOUD_SECRET_ID=你的SecretId
TENCENTCLOUD_SECRET_KEY=你的SecretKey
TENCENT_DOMAIN_TEMPLATE_ID=已审核的信息模板ID
DOMAIN_WATCH_DOMAINS=example.com,example.cc
```

核心调度配置：

```bash
DOMAIN_WATCH_REDEMPTION_INTERVAL_SECONDS=21600
DOMAIN_WATCH_PENDING_DELETE_INTERVAL_SECONDS=300
DOMAIN_WATCH_DROP_INTERVAL_SECONDS=5
DOMAIN_WATCH_RETRY_INTERVAL_SECONDS=900
DOMAIN_WATCH_REGISTRATION_POLL_SECONDS=30
DOMAIN_WATCH_TLD_PENDING_DELETE_DAYS_JSON={"com":5,"cc":5}
```

RDAP 配置：

```bash
RDAP_BOOTSTRAP_URL=https://data.iana.org/rdap/dns.json
RDAP_BOOTSTRAP_CACHE_FILE=rdap_bootstrap_cache.json
RDAP_BOOTSTRAP_TTL_SECONDS=86400
RDAP_REQUESTS_PER_SECOND=1
RDAP_HOST_LIMITS_JSON={}
RDAP_HTTP_TIMEOUT_SECONDS=10
DOMAIN_WATCH_PROXY=http://127.0.0.1:7890  # 可选，仅用于 RDAP
```

限流按 RDAP 主机共享。收到 429 时优先遵守 `Retry-After`，缺失时冷却 15 分钟；冷却期间明确改用腾讯云查询。腾讯云查询、注册提交和任务详情使用互相独立的限流器。

RDAP 查询失败不一定会打断调度。在「热追」状态下 —— 释放窗口内，或 RDAP 已判定未注册的 `available` 阶段 —— 超时、连接错误和 5xx 都会记录后立即改用腾讯云 `CheckDomain`，域名保持原有 5 秒节奏，不被计入失败退避。远离掉落的 `scheduled` 阶段仍只做退避：此时 RDAP 抖动一次不会有损失，而切换到 15 分钟轮询反而会丢掉精确的 `expires_at` 唤醒。超时和 5xx 的区别在于「有没有拿到回答」—— 两者都不产生新的域名状态，因此都不会覆盖已有的调度。

推送仍使用 onepush：

```bash
ONEPUSH_PROVIDER=bark
ONEPUSH_PARAMS_JSON={"key":"你的Bark key"}
ONEPUSH_TITLE_PREFIX=[domain-watch]
```

程序只推送状态转换、限流、错误和注册任务事件，不会在 5 秒窗口重复推送相同的“不可注册”结果。

腾讯云兜底查询失败不会影响 RDAP 调度。窗口内每轮都调用腾讯云，失败会单独记录并只推送一次（相同错误不重复推送），域名保持原有的 5 秒或 15 分钟节奏继续重试，不会被计入 RDAP 的失败退避 —— 否则一次腾讯云抖动就会把域名踢出释放窗口。

## 状态与安全语义

`domain_watch_state.json` 使用版本化的逐域状态，保存下一次查询、RDAP 状态、预测窗口、主机冷却和腾讯云 LogId。写入使用原子替换，旧版 `active/removed/statuses` 文件会自动迁移。

提交腾讯云请求前会先保存 `registering` 意图。如果进程在取得 LogId 前中断，重启后该域名会进入 `indeterminate` 并停止自动重试，以避免重复下单。需先在腾讯云确认真实结果，再人工修正对应状态。

注册参数保持：余额支付、1 年默认注册期、关闭自动续费和域名锁。当前不限制溢价或最高价格。

## 运行与验证

```bash
uv run domain_watch.py
```

仅测试腾讯云查询，不提交注册：

```bash
uv run scripts/check_tencent_domain.py
```

```bash
ruff check src tests scripts domain_watch.py
uv run -m pytest tests
```
