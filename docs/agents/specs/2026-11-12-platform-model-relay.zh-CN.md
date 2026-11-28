# 平台提供的模型：模型中继

状态：已实现。本服务对用户不设限额，见第 9 节。

相关：[用户自带模型连接](2026-10-27-user-model-connections.zh-CN.md)、[只保留独立账号](2026-10-30-independent-accounts-only.md)、[GitHub 中继](2026-11-04-agent-native-pull-requests.zh-CN.md)、[公网出口](2026-09-23-agent-public-egress-design.zh-CN.md)、[请求前缀计量](2026-11-05-request-prefix-meter.zh-CN.md)。

## 1. 决定

一个部署可以给每个获准用户提供模型，用户不用设置。用户自带 key 的连接照旧可用。

- **平台的 key 只在控制面。** sandbox 里的 CLI 拿到的是 residency 级的占位凭据。模型请求经过控制面的模型中继，中继校验占位凭据，换成平台的 key，再转发给供应商。这和 GitHub 中继、MCP relay 是同一种结构。
- **对供应商没有额外要求。** 管理员只填名字、Base URL 和一把普通的 key。模型和价格都从这把 key 的模型列表里读。本服务不调用供应商的管理接口，也不在供应商那边建用户或 key。
- **本服务不限额。** 中继只记录每次请求的 token 数，以及按列表价格算出的费用。花多少由供应商对这把 key 的预算和并发限制决定。

名词：

| 名字 | 含义 |
|---|---|
| 平台连接 | 管理员为整个部署配置的一个模型来源。id 是 `oconn_<32 位十六进制>` |
| 用户连接 | 用户自己的 Base URL 和 key，id 是 `mconn_…` |
| 模型中继 | 控制面的一个独立 listener，替运行中的 turn 把占位凭据换成平台 key |

## 2. 为什么不给每个用户一把 key

另一种做法是控制面替每个用户向供应商申请一把 key，当作用户自己的连接交给 sandbox。没有采用，原因有三条。

1. **安全边界。** 那把 key 花的是部署的钱，却放在 sandbox 里，任务代码、依赖和提示注入都能带走它，带走后在额度内任何地方可用。使用占位凭据时，sandbox 里没有可以带走的东西，「平台持有的长期凭据不进 sandbox」这一条不用破例。
2. **对供应商的要求。** 它要求供应商实现建 key 的接口，多数供应商没有。中继对任何 OpenAI 或 Anthropic 兼容的端点都可用。
3. **按人的控制。** 按用户记账和调整，要在供应商那边为每个用户维护一条对应记录。账号导入设计明确不做这种账号对应。

| 被攻破的是 | 每人一把 key | 中继 |
|---|---|---|
| 单个 sandbox | 这个用户的 key，撤销前可在任何地方使用 | 一个占位凭据，离开这个对话正在运行的 turn 就没用 |
| 宿主机 | 机器上每个运行中 turn 的用户 key | 占位凭据，平台 key 不在宿主机上 |
| 控制面 | 建 key 的管理凭据和所有用户的 key | 平台 key |

中继让控制面进入使用平台连接的 turn 的请求路径。用户连接的 turn 仍然直连供应商。中继只转发字节，不改应答。如果实测发现控制面承受不了流量，退路是把换 key 的位置移到宿主机：sandbox 一侧的地址和占位凭据不变，但平台 key 要放到每台宿主机上，所以需要再决定是否接受。

## 3. 模型中继

### 3.1 位置和路径

```
sandbox 里的 CLI（前面是成员自己的 loopback 计量或转换服务）
  │  http://agent-models:<端口>/<Base URL 的路径>/chat/completions 等
  │  凭据是占位凭据，不是 key
  ▼
model-tunnel（宿主机，组桥上的 relay 别名）
  │  ssh -L，独立密钥，permitopen 只到控制面的模型中继端口
  ▼
model-relay（控制面，只含模型中继路由的 listener）
  │  校验占位凭据 → 查平台连接 → 换成平台 key
  ▼
供应商（平台连接的 Base URL，公网 HTTPS）
```

- **独立 listener。** `create_model_relay_app` 和 `create_mcp_app`、`create_github_relay_app` 用同一个镜像和数据库，只含模型中继路由。它默认只绑在控制面机器的回环地址上（`AGENT_MODEL_RELAY_BIND` / `AGENT_MODEL_RELAY_PORT`），不进公网边缘，不和别的 relay 共用端口。`amr.`、`agh.` 的 token 在这里不能用，反过来也一样。控制面一直运行它，没有启用的平台连接时它拒绝所有请求。
- **独立隧道。** 每台宿主机上的 `model-tunnel` 容器和另两条隧道用同一个脚本（`deploy/host/agent-ssh-tunnel.sh`），有自己的 SSH 密钥和 `permitopen`，挂在 `agent-relays` 网络上。没有目的地址时空转。`cloud_agent_shared/egress.py` 的 `network_from_env` 读 `AGENT_SANDBOX_MODEL_RELAY_URL`，把它加进 relay 列表，再由现有机制接到每个组桥。Admin → Servers → Add server 会为新服务器配好这条隧道：单独的 SSH 密钥、控制面机器上单独的账号（`ca-md-…`，只允许转发到模型中继的端口），以及宿主机配置里的 `AGENT_SANDBOX_MODEL_RELAY_URL` 和 `AGENT_MODEL_TUNNEL_*`。不由 Add server 部署的宿主机，由部署它的一方提供这些值。
- **明文 HTTP，和 MCP relay 一样。** 组桥上只有这个组的 sandbox 和 relay，出了宿主机走的是 SSH。CLI 的 Base URL 本来就可配置，不需要冒充供应商的域名，也就不需要 GitHub 中继那样的证书。
- **名字解析不是安全边界。** sandbox 仍可经公网出口直连供应商，但它手里没有 key。边界是「平台 key 只在中继里」。

### 3.2 占位凭据

- 每个成员建立 residency 时得到一个模型中继 token。它和 `amr.`、`agh.` 同一套 HMAC 格式（`tokens.py` 的 `_mint_residency`），scope 是 `model_residency`，前缀是 `amd.`，三者互不通用。
- 它放在 residency 应答的 `credential` 字段里。CLI 把它当 API key 用，宿主机在持久输出里屏蔽它。它对供应商没有意义。
- 每个请求都按各 relay 共用的 fence 校验（`storage/_sql/fences.py` 的 `live_residency_caller_sql`）：residency 代数一致、状态 hot、租约未过期，对话没删，当前 attempt 在运行且租约未过期，job 在运行且没被要求停止，宿主机还在册。用户、job 和平台连接都从数据库读，不从请求里读。
- 撤销是隐式的：Stop、取消、attempt 被替换、residency 轮换、宿主机移除，从下一个请求起生效。账号停用时，终止流程（`application/owner_termination.py`）停掉它的 turn 并释放 residency。没有东西需要去供应商那边撤销。

### 3.3 一个请求怎么处理

1. 取凭据：`Authorization: Bearer …` 或 `x-api-key`。不是占位凭据就答 401。这里不放行用户自带的凭据，因为用户连接的 turn 不经过这个中继。
2. 按 3.2 的 fence 找到运行中的 turn，得到用户和 job。找不到答 401。
3. 读 job 的平台连接。它必须存在、已启用，而且这个 residency 绑定的修订号等于当前修订号。否则答 403。
4. 核对方法和路径（3.4）。
5. 读请求体，取顶层的 `model`。它必须在平台连接的模型列表里并且有 agent 能用。
6. 换上平台 key，转发给供应商，流式传回应答，同时读出其中的用量记录（第 6 节）。转发用的 HTTP 客户端由所有用户共用，不保存 cookie。
7. 应答结束或 CLI 断开时，记下这次请求的用量和费用，并关闭到上游的连接。CLI 在上游还没给应答头时断开也一样：中继在等上游的同时监听 CLI 的连接。

### 3.4 转发什么，拒绝什么

记平台连接 Base URL 的路径为 `P`，`A` 是 `P` 去掉末尾一个 `/v1` 后的路径（规则同 `cloud_agent_shared/model_connections.py` 的 `anthropic_base_url`）。只放行这些：

| 方法和路径 | 记用量 |
|---|---|
| `POST P/chat/completions`、`POST P/responses`、`POST A/v1/messages` | 是 |
| `POST A/v1/messages/count_tokens` | 否 |
| `GET P/models`、`GET A/v1/models` | 否 |

请求的原始路径要和其中一条逐字节相同，带转义或点段的路径不匹配，其他路径答 404。按整条路径放行而不是按前缀，是因为前缀会把同一前缀下的其他接口（比如供应商保存的 Responses 记录）一并放出去。

- **上游地址。** 平台连接 Base URL 的 origin，加请求的原始路径和查询串。目的地由平台连接决定，请求不能选。拨号沿用连接验证的规则：只连公网 IPv4 的 HTTPS，地址固定，不跟随重定向，不读环境代理。
- **请求头。** 只转发白名单里的头：`accept`、`content-type`、`user-agent`、`anthropic-version`、`anthropic-beta`、`openai-beta`。其余一律不转发，包括 `cookie`、`x-forwarded-*`、`OpenAI-Organization`、`OpenAI-Project`，因为有些供应商按请求头决定这把 key 记到哪个组织或项目。新 CLI 需要供应商看到别的头时，往白名单里加。`accept-encoding` 由中继写成 `identity`，因为应答原样流回去，中继要在途中读用量记录。平台 key 用 CLI 原来的方式带上（`Authorization: Bearer` 或 `x-api-key`）。
- **请求体。** 整体读入后再转发，上限 32 MiB，超过答 413。只读顶层的 `model`，不改内容。
- **应答。** 原样流式传回，包括供应商的错误和错误体，CLI 靠它们决定重试和压缩上下文。例外是上游答 401：那是平台 key 被拒绝，CLI 的占位凭据没有错，所以改答 502，并在平台连接上记下 `key_refused_at`，管理页显示「平台 key 被供应商拒绝」。
- **不记录内容。** 日志只有结构化事件：job、对话、用户 id、模型、状态码、耗时、拒绝原因、token 数和费用。请求体、应答体、key 和占位凭据都不进日志。

拒绝的应答用 CLI 认识的错误形状：路径是 `…/messages` 时用 Anthropic 的 `{"type":"error","error":{…}}`，其余用 OpenAI 的 `{"error":{…}}`。

| 情况 | 状态码 | `code` |
|---|---|---|
| 凭据不是占位凭据，或对话没有运行中的 turn | 401 | `invalid_credential` |
| 平台连接已停用、已删除或已变更 | 403 | `provided_unavailable` |
| 模型不在列表里，或没有 agent 能用 | 403 | `model_not_allowed` |
| 请求体不是 JSON 或没有 `model` | 400 | `invalid_request` |
| 路径不在放行列表里 | 404 | `not_found` |
| 请求体超过 32 MiB | 413 | `request_too_large` |
| 供应商连不上，或平台 key 被拒绝 | 502 | `provider_unreachable`、`provider_refused_credential` |
| 数据库不可用，或平台 key 解不开 | 503 | `relay_unavailable` |

## 4. 平台连接

### 4.1 数据

- `agent_operator_model_connections`（迁移 `0118`，`0120` 扩展）：`id`（`oconn_…`）、`name`、`base_url`（https，规则同用户连接）、`models`（1 到 16 个）、`enabled`（新建时关闭）、`api_key_ciphertext`（只写，删除时清空）、`key_refused_at`（供应商从何时起拒绝这把 key，正常为空）、`model_checks`（管理员验证的结果，形状同用户连接）、`config_revision`、`credential_revision`、`deleted_at`。
- `agent_operator_model_usage`：按平台连接、用户、模型、UTC 日期累计的请求数、全部输入、缓存读、缓存写和输出 token 数、费用（百万分之一美元）和按估算计费的请求数。
- `agent_operator_connection_residencies`：一个 residency 的进程是在平台连接的哪个修订号上启动的。它和 `agent_model_connection_residencies` 用途相同，但不能共用，因为那张表的 `connection_id` 有指向用户连接表的外键。
- 迁移留下了 `0118`、`0120` 引入的额度、并发和单独设置的列和表（`agent_operator_model_limits`、`agent_operator_user_budgets`、`agent_operator_model_user_access` 等）。代码不读它们，待之后的迁移删除。
- 平台 key 用设置根密钥（`crypto/domain_cipher.py` 的 `settings_root_key`）在域 `cloud-agent/operator-model-connection-key/v1` 下加密。它只留在控制面：任何 claim、residency、`/config`、日志、审计详情和接口应答里都没有它。
- 带 `user_id` 的表会被账号擦除自动覆盖（`storage/_sql/local_erasure.py` 按列名找表）。

### 4.2 模型和价格从 key 的列表里来

保存时，用这把 key 读供应商的模型列表（`GET …/models`），列表里有什么就提供什么，顺序照列表。供应商那边这把 key 能用哪些模型，由管理员在供应商处限定，这里不再挑。

- 列表读不出来、是空的或超过 16 个，都拒绝保存。超过时不截断，因为该提供哪几个只有 key 说了算。
- 每个模型照旧验证（规则同[用户连接](2026-10-27-user-model-connections.zh-CN.md)）。一个模型没有 agent 能用，就留在连接上带着验证结果，但不提供给任何人。至少有一个模型可用才保存。用户自己的连接则要求每个模型都能用，因为模型是用户挑的。这与 `api/admin_operator_models.py` 一致。
- 换 key 或 Base URL 时重新读列表并验证。`POST …/{id}/refresh-models` 用存着的 key 再读一次：新出现的模型验证后加上，没了的去掉。
- 同一个模型 ID 只能由一个平台连接提供。新建、换 key、换 Base URL 或重新读取时，若新出现的模型已由另一个平台连接提供，返回 409 `operator_model_model_taken`，什么都不改。办法是在供应商处限定 key，让两边不重叠。
- hybridInference 一系的推理服务在列表的 `pricing` 里给出 `prompt`、`completion`，可能还有 `input_cache_reads`、`input_cache_writes`，单位是美元每百万 token。没有价格的模型，用量只记 token，费用记 0。价格只用于用量表里的显示，不决定任何事，所以单位不同的供应商最多让这一列不准，不会多放或少放请求。

### 4.3 turn 怎么引用平台连接

- **job 直接写平台连接的 id。** `agent_jobs.model_connection_id` 和 `agent_pending_messages.model_connection_id` 没有外键和格式约束，可以存 `oconn_…`。不为每个用户派生一行连接，因为这里没有属于单个用户的秘密，派生行只会带来扇出。
- **一个解析函数分流。** `resolve_turn_connection`（`application/model_connections.py`）按前缀分流：`mconn_` 按所有者读用户连接，`oconn_` 读平台连接。两者交给同一个 `require_usable`。新任务、后续消息、排队消息、claim、residency 和 Fork 都经过它。
- **拒绝原因。** 除用户连接的那些，还有 `provided_unavailable`（平台连接被停用或删除）和 `provided_model_unavailable`（模型被移出列表，或没有这个 agent 能用的 API）。代码里还留着 `allowance_none`、`allowance_exhausted`，因为旧版本可能已把它们写进 turn 或排队消息。
- **不回退。** 平台连接不可用时，turn 不会换到用户自己的连接，反过来也一样。
- **总开关。** `AGENT_MODEL_CONNECTIONS_ENABLED` 关掉时，平台连接和用户连接一样不能用。
- **按 turn 的所有者算。** Fork 出来的成员、团队环境里别人的 turn，用量都记在那个 turn 的所有者名下。

### 4.4 claim、residency 和宿主机

- **claim。** `ModelConnectionAccess.relayed` 为真时不带 `api_key`。`base_url` 仍是平台连接真实的 https 地址（路径和进程身份要用它），`connection_id` 是 `oconn_…`，`revision` 是平台连接的 `credential_revision`。
- **residency。** `residency_credential` 对平台连接返回 3.2 的占位凭据，并在平台连接那一行的锁下把修订号写进 `agent_operator_connection_residencies`。先绑定的，之后的变更会找到它并关闭进程。先变更的，绑定被拒绝。不写扫描副本，因为没有 key 进入 sandbox。
- **能力。** 宿主机配置了 `AGENT_SANDBOX_MODEL_RELAY_URL` 才通告 `model-relay-v1`（`service/host_capabilities.py`）。没有这个能力的宿主机拿到平台连接的 claim 会被退回重排。`inference_source` 接受 `oconn_`，`launch_fields` 多一个 `inference_relayed`，它是进程固定启动参数的一部分。
- **成员的 URL。** 宿主机把 Base URL 的 origin 换成自己的模型中继地址，路径不变，所以 Anthropic 的「去掉末尾一个 `/v1`」规则照旧适用。
- **loopback 服务。** 成员的计量和转换服务只接受 https 上游，对 `relayed` 的启动多接受一个：本机配置的模型中继地址，精确匹配，明文 HTTP。请求前缀计量照常工作，只是上游从供应商换成了中继。
- **已知限制。** Claude Code 按 URL 主机名判断是不是官方端点（`runtimes/base.py` 的 `FIRST_PARTY_HOSTS`）。经过中继后它看到的是中继的名字，所以平台连接指向 `api.anthropic.com` 时，它按第三方端点的方式工作。

### 4.5 管理员改了平台连接以后

| 改动 | 运行中的进程 |
|---|---|
| 换 key（先验证） | 保留。key 只在中继里，下一个请求就用新的 |
| 换 Base URL（先验证） | 关闭。`credential_revision` 加一，绑定旧修订号的 residency 被释放 |
| 重新读取模型、改名 | 保留。中继下一个请求读到新值 |
| 停用、删除 | 关闭，同时中继开始拒绝 |
| 启用 | 无 |

换 key 不必关进程，因为进程拿的是占位凭据，与 key 无关。

## 5. 接口和界面

**用户接口。** `GET /v1/agent/config` 的 `provided_sources` 列出每个已启用的平台连接：`id`、`name`、模型列表、`model_runtimes`、`model_efforts`。只列有 agent 能用的模型，顺序照供应商的列表。新任务和后续消息提交 `oconn_…` 作为 `model_connection_id`。

**模型选择器。** 平台提供的模型和用户自己连接的模型放在同一个列表里，不分组。选择本身仍是「模型 + 连接 id」一对。同一个模型名在列表里只出现一次：

- 用户自己的几个连接之间，同一个模型 ID 只能在一个连接里（409 `model_connection_model_taken`）。
- 平台连接之间同理（409 `operator_model_model_taken`）。
- 用户自己的连接和平台连接撞名时，用户自己的优先：这个用户自己已启用、有 agent 能用的连接里有这个模型，平台那份就不提供给这个用户。保存时不因此拒绝，因为用户拿自己的 key 接平台也有的模型是合理的。平台连接里的模型都被盖住时，这个平台连接对该用户不列出，别的用户照常能看到。上一个 turn 跑在一个现在被盖住的平台模型上时，新任务不会自动换到用户连接里的同名模型，因为两个来源计费的账户不同，而是回到部署的起始模型。
- 服务端照旧接受指名平台来源的请求。列表只决定列出什么，不收回已经能跑的东西。
- 这些规则出现之前存下的连接可能有重名，所以选择仍是「模型 + 连接 id」一对，重名时标签带上来源名字。

有默认的平台连接时，首次使用清单里「添加模型连接」一项视为已完成。`JobUsageResponse` 带 `source`（`personal` 或 `provided`）和来源名字。

**管理接口**（前缀 `/admin/agent/operator-model-connections`，依赖 `verify_admin_access`，答 `no-store`）：

- `GET` 列表、`POST` 新建（只收 `name`、`base_url`、`api_key`）、`PATCH` 修改（这三项加 `enabled`）、`DELETE`。改 Base URL、换 key 时在保存请求里用平台 key 直接验证，key 字段只写。管理员的验证不占用任何用户的每小时验证次数。
- `POST …/{id}/refresh-models` 重新读取模型。
- `GET …/{id}/usage?from=&to=` 按用户、模型、日期给出请求数、token 数、费用和按估算计费的请求数，最多 92 天。
- 列表应答带 `relay_hosts`：有多少台在册宿主机通告了 `model-relay-v1`。没有任何宿主机通告时，管理页给出警告。

**审计。** 新建、修改、启用、停用、删除各一个动作（`agent.operator_model_connection.*`），类别 `settings`。换 key 单独一个动作，值永远是 `[redacted]`。新动作在 `domain/audit.py` 声明，`tests/unit/test_audit_registry.py` 要求迁移里的冻结副本和 Audit 页的清单与它一致。

**管理页。** Admin → Settings → Models。

## 6. 用量记录

供应商的应答里本来就带着用量，位置因协议而异：

| 协议 | 普通应答 | 流式应答 |
|---|---|---|
| Chat Completions | 顶层 `usage` | 带 `usage` 的 chunk，通常是 `[DONE]` 之前的最后一个 |
| Responses | 顶层 `usage` | 结束流的事件（`response.completed` 等）里的 `response.usage` |
| Messages | 顶层 `usage` | `message_start` 给输入，`message_delta` 给输出 token 数 |

中继在原样传回应答的同时读它（`domain/model_usage.py` 的 `UsageReader`）。流式应答按行读，并累计已生成文本的字节数（正文、推理、工具调用）供估算用。一行未读完的内容或一个普通应答超过 8 MiB 时不读。读出的数归成四项：全部输入 token（含缓存）、缓存读、缓存写、输出 token。

- Messages 的 `input_tokens` 不含缓存，另外两项单独给，所以三项加起来才是全部输入。另两种协议的输入数本来就含缓存。
- 推理 token 只算一次：总数已经包含时不再加，缺失或对不上时加上，取贵的读法。
- 缓存命中的几种写法都认：OpenAI 的 `prompt_tokens_details.cached_tokens`、hybridInference 的 `cache_read_tokens`、`cached_tokens`、DeepSeek 的 `prompt_cache_hit_tokens`。

费用的算法和取整与 hybridInference 一致（`serving.storage.utils.calculate_cost`、`serving.spend_ledger.counted_amount`）：

```
普通输入 = 全部输入 − 缓存读（有缓存读价格时）− 缓存写（有缓存写价格时）
费用 = 普通输入 × 输入价 + 输出 × 输出价 + 缓存读 × 缓存读价 + 缓存写 × 缓存写价
```

没有单独价格的缓存 token 留在普通输入里，按输入价算。先按浮点数算出美元，再四舍五入到百万分之一美元。金额一律是整数（`…_microusd`）。

**读不到用量时按估算。** 包括应答没带用量记录、CLI 中途断开（用户按了 Stop）、上游中途断开、供应商不顾要求压缩了应答、中继自己出错。估算办法和 gateway 给中途断开的流计费一样（`StreamSession._cancelled_usage`）：输入按请求体的文本量估，输出按已生成的字节数估。它不知道缓存命中，所以输入按全价算，对缓存命中率高的请求是多收。

**不记用量的情况。** 请求没有到达供应商（被中继拒绝、连不上、收到应答头之前超时），供应商答了错误（4xx、5xx）或重定向，以及列模型和数 token 两条路径。

**写入。** 应答结束时用一条 `INSERT … ON CONFLICT DO UPDATE` 累加到用量表当天的行，同一用户同一模型同时结束的请求只在自己那一行上短暂排队。跨过 UTC 零点的请求记在它结束的那一天。确定没写进去的（拿不到数据库连接，或服务器拒绝了语句）留在进程里，按 1、2、5、10、30、60、120 秒的间隔重写（`api/_model_relay_charges.py`）。语句发出后连接断了的，可能已经记上，不重写，只记一条 error 日志（`agent_model_relay_charge_unknown`），避免重复计入。

## 7. 安全边界

不变的：

- 平台持有的长期凭据不进 sandbox，也不到宿主机。
- 组桥上的 relay 都是固定目的地，没有由调用者选目的地的代理。模型中继的上游由平台连接决定，路径和模型都有白名单，不是占位凭据的请求一律拒绝。
- 没有 Gateway 依赖。中继按 URL 对接任何供应商，不含针对某个供应商的客户端。

接受的：

- **在自己运行中的 sandbox 里转发。** 用户可以在 turn 运行期间从 sandbox 里开代理，把中继借给外面的程序用。这需要一个运行中的 turn，用量记在他名下，上限是 key 自己的限额。
- **共用同一把 key 的预算。** 一个用户用得多，可能把这把 key 当天的预算用完，或占住并发，影响所有用户。本服务不限制，所以部署应当在供应商那边给这把 key 设紧一点的预算和并发。
- **同组成员。** 同组的兄弟成员共用工作区，一个成员留下的脚本可能被另一个成员执行并用到它的占位凭据。这和 MCP、GitHub 的 residency 凭据是同样的已知情况，消耗的是那个 turn 所有者的用量。
- **内容经过控制面。** 平台连接的请求和应答流经模型中继。中继不保存也不记录它们，但控制面在这条路径上，这是用户连接没有的。

## 8. 失败情况

- **中继或隧道不可用。** 请求连接失败，CLI 按自己的规则重试，一直不恢复，turn 以 CLI 报的错误结束。用户连接的 turn 不受影响。每个组启动前，guest 网络检查（`network/guest_network_check.py`）会探测 relay 列表里的端口，模型中继在其中。
- **中继重启**（控制面发布时会发生）。进行中的应答会断，CLI 重试。这是用平台连接的代价，要写进运维文档。
- **供应商故障。** 错误原样传回，turn 以它结束，不回退到别的来源。
- **平台 key 被供应商拒绝。** 中继答 502，管理页显示健康状态和开始时间，记 error 日志。
- **数据库不可用。** 中继不能校验 fence，答 503。应答进行中数据库才不可用时，应答照常传回，用量留在进程里稍后重写。
- **应答被截断。** 这次请求读不到完整用量，按估算记。

## 9. 不在本服务里限额

按人的限额以后放到用户管理里，做成每个用户自己的设置。目前中继、准入和 claim 都不检查额度或并发。数据库里与额度有关的列和表保留不读。

## 10. 上线顺序

宿主机先于控制面发布，每一边只兼容上一个版本。

1. **宿主机**：新镜像认识 `relayed` 的 claim 和 `oconn_`，loopback 服务接受模型中继这个上游。没配模型中继的宿主机不通告能力，行为不变。
2. **控制面**：迁移只新增表。平台连接新建时是关闭的。上一个版本的控制面不会读到这些表，也不会发出 `oconn_`。
3. **启用**：所有控制面副本都运行新版本后，管理员再启用平台连接。平台连接的 turn 只会被有这个能力的宿主机领走。
4. **降级**：只要存在平台连接或引用它的 turn，迁移的降级就拒绝执行，和 `0106` 的做法一样。
