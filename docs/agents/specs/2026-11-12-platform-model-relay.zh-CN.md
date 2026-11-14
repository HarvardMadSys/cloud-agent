# 平台提供的模型：模型中继与每人的每日额度

日期：2026-11-12。状态：已实现；第 11 节的三处决定已于 2026-11-13 和维护者确认。2026-11-14 修订：用量上限从“每人每模型每天的请求次数”改成“每人每天的金额额度”，第 5 节整节重写，原因见 5.7。哪些做了、哪些没做见第 14 节。

设计核对基线：远端 `dev`，提交 `ccf7e07f1`。

相关：[Operator-provided model connections](2026-11-09-operator-provided-model-connections.md)（本文保留它的第一阶段，取代第二、三阶段）、[用户自带模型连接](2026-10-27-user-model-connections.zh-CN.md)、[只保留独立账号](2026-10-30-independent-accounts-only.md)、[GitHub 中继](2026-11-04-agent-native-pull-requests.zh-CN.md)、[公网出口](2026-09-23-agent-public-egress-design.zh-CN.md)、[请求前缀计量](2026-11-05-request-prefix-meter.zh-CN.md)。

## 1. 结论

一个部署可以给每个获准用户提供模型，用户不用做任何设置。用户自带 key 的模型连接照旧可用。

- **平台的 key 只在控制面。** sandbox 里的 CLI 拿到的是 residency 级的占位凭据。模型请求经过控制面上新增的模型中继，中继校验占位凭据，换成平台的 key，再转发给供应商。这和 GitHub 中继、MCP relay 是同一种结构。
- **每个用户有一个每日额度。** 单位是美元，按 UTC 日计，一个平台连接的所有模型共用。每次请求按它用掉的 token 和这个模型的价格计费。管理员给平台连接设默认额度、给每个模型设价格，也可以给个别用户单独设额度，或单独决定某个模型给不给他用。中继在每个请求上执行它。
- **对供应商没有额外要求。** 只需要一个 Base URL 和一把普通的 key。本服务不调用供应商的管理接口，也不在供应商那边建用户、成员或 key。
- **取代每人一把 key 的方案。** [Operator-provided model connections](2026-11-09-operator-provided-model-connections.md) 的第一阶段（预设）不变；第二、三阶段（控制面替每个用户向供应商申请一把 key）由本文取代。

接受本设计要改三处已经写下来的决定，列在第 11 节。

本文用到的名字：

| 名字 | 含义 |
|---|---|
| 平台连接 | 管理员为整个部署配置的一个模型来源：名字、Base URL、模型、key。id 是 `oconn_<32 位十六进制>`。对应那份设计里的 operator connection。 |
| 用户连接 | 今天的模型连接：用户自己的 Base URL 和 key，id 是 `mconn_…`。不变。 |
| 模型中继 | 控制面的第四个 listener，替运行中的 turn 把占位凭据换成平台 key。 |
| 每日额度 | 一个用户一天内在一个平台连接的所有模型上一共可以花多少钱。 |
| 价格 | 一个模型每百万 token 多少美元：输入、输出，以及供应商单独计价时的缓存读、缓存写。 |
| turn | 一条用户消息和 agent 为它做的工作，代码里是一个 job。 |

## 2. 为什么不做每人一把 key

那份设计的第二阶段让控制面拿一个管理凭据，替每个用户在供应商那里建一把 key，再把这把 key 当作用户自己的连接交给 sandbox。不这样做有三个原因。

1. **安全边界。** 那把 key 花的是部署的钱，却放在 sandbox 里，任务代码、依赖和提示注入都能把它带走。带走以后，在这把 key 的额度以内，它在任何地方都能用。为此要改写 AGENTS.md §7 的“平台持有的长期凭据不进 sandbox”。本设计里 sandbox 没有可以带走的东西，这一条不用动。
2. **对供应商的要求。** 它要求供应商实现一套建 key 的协议。多数供应商没有这样的接口，所以那个功能只对个别供应商可用。本设计对任何 OpenAI 或 Anthropic 兼容的端点都可用。
3. **按人的控制。** 这需要按用户记账，还要能给个别用户单独调整额度和可用的模型。只有本服务认识自己的用户。把这张表放到供应商那边，就得先在那边为每个用户建一条对应的记录并保持同步，也就是账号导入设计（`docs/agents/specs/` 下 2026-11-05 的记录，第 7 节）明确不做的账号对应关系。

| 被攻破的是 | 每人一把 key | 本设计 |
|---|---|---|
| 单个 sandbox | 这个用户的那把 key：撤销之前，在额度以内任何地方可用 | 一个占位凭据：离开这个对话正在运行的 turn 就没用 |
| 宿主机 | 机器上每个运行中 turn 的用户 key | 占位凭据；平台 key 不在宿主机上 |
| 控制面 | 建 key 的管理凭据，以及所有用户的 key | 平台 key |

那份设计评估过中继（它的选项 3），结论是不计划做，理由有三条。本设计这样对待它们：

- **控制面进入每个 turn 的关键路径。** 只有用平台连接的 turn 经过中继，用户连接的 turn 和今天一样直连供应商。中继是独立的 listener，主 API 和其他 relay 重启不影响它；它自己重启时，进行中的请求会断（第 9 节）。中继只转发字节，不解析应答。延迟和并发要在 staging 上实测（第 13 节），结果不可接受时的退路见第 12 节。
- **滥用控制变成本服务的事。** 是的，而且这正是需求：按用户的额度只能由认识用户的一方来定。中继按供应商应答里的 token 数和管理员填的价格算出每次请求的费用，记到用户当天的花费上（第 5 节）；这是额度，不是账单。
- **出口规则要变。** 是的。组桥上多一个固定目的地的 relay，和已有的两个同构；AGENTS.md §6、§7 的列表要加上它。

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
model-relay（控制面，第四个 listener，只含模型中继路由）
  │  校验占位凭据 → 查平台连接和每日额度 → 换成平台 key
  ▼
供应商（平台连接的 Base URL，公网 HTTPS）
```

- **一个只做这件事的 listener。** 新增 `create_model_relay_app`，和 `create_mcp_app`、`create_github_relay_app` 同一个镜像、同一个数据库，只包含模型中继的路由。它默认只绑在控制面机器的回环地址上，不进公网边缘，不和别的 relay 共用端口：`amr.`、`agh.` 的 token 在这里不能用，反过来也一样。
- **独立隧道。** 宿主机上新增 `model-tunnel` 容器，和另外两条隧道用同一个脚本（`deploy/host/agent-ssh-tunnel.sh`），有自己的 SSH 密钥和 `permitopen`，挂在 `agent-relays` 网络上。`cloud_agent_shared/egress.py` 的 `network_from_env` 多读一个 `AGENT_SANDBOX_MODEL_RELAY_URL`，把它加进 relay 列表；之后由现有机制接到每个组桥，端口进入组桥的 `relays` 集合。组桥是 /29，放得下第三个 relay。
- **明文 HTTP，和 MCP relay 一样。** 组桥上只有这个组的 sandbox 和 relay（`chat_sandbox_network.py` 的 `_reconcile_group_members`），出了宿主机走的是 SSH。它不需要 GitHub 中继那样的证书：CLI 的 Base URL 本来就可以配置，不必冒充供应商的域名。
- **名字解析不是安全边界。** sandbox 仍然可以经公网出口直连供应商，但它手里没有 key。安全边界是“平台 key 只在中继里”。

### 3.2 占位凭据

- 每个成员建立 residency 时得到一个模型中继 token：和 `amr.`、`agh.` 同一套 HMAC 格式（`tokens.py` 的 `_mint_residency`），新的 scope `model_residency` 和新的前缀（暂定 `amd.`），三者互不通用。
- 它放在 residency 应答的 `credential` 字段里（`api/worker.py` 的 `worker_establish_shared_sandbox_residency`）。这个字段今天装的是用户自己的 key；CLI 同样把它当 API key 用，宿主机同样在持久输出里屏蔽它。它对供应商没有意义。
- 每个请求都按各 relay 共用的 fence 校验（`storage/_sql/fences.py` 的 `live_residency_caller_sql`）：residency 代数一致、状态 hot、租约未过期，对话没删，当前 attempt 在运行且租约未过期，job 在运行且没被要求停止，宿主机还在册。用户、job 和它用的平台连接都从数据库读，不从请求里读。
- 撤销是隐式的：Stop、取消、attempt 被替换、residency 轮换、宿主机移除，都从下一个请求起生效。账号被停用时，现有的终止流程（`application/owner_termination.py`）会停掉它的 turn 并释放 residency，占位凭据随之失效。没有东西需要去供应商那边撤销。

### 3.3 一个请求怎么处理

1. 取凭据：`Authorization: Bearer …` 或 `x-api-key`（Anthropic 的 SDK 用后者）。不是模型中继的占位凭据就答 401。和 GitHub 中继不同，这里不放行用户自己带来的凭据：用户连接的 turn 不经过这个中继。
2. 按 3.2 的 fence 找到运行中的 turn，得到用户和 job。找不到就答 401。
3. 读 job 的平台连接。它必须存在、已启用，而且这个 residency 绑定的修订号就是当前的修订号（第 4.3 节）。否则答 403。
4. 核对方法和路径（第 3.4 节）。
5. 读请求体，取顶层的 `model`。它必须在平台连接的模型列表里，有价格，并且对这个用户开放；这个用户今天的额度还没有用完。
6. 占并发名额（第 5.5 节），再按请求里的文本估一笔费用，从这个用户的额度里预留出来（第 5.4 节）。名额或额度不够就拒绝。
7. 换上平台 key，转发给供应商，把应答流式传回，同时读出应答里的用量记录（第 5.2 节）。转发用的 HTTP 客户端由所有用户的请求共用，它不保存 cookie：一次应答设的 cookie 不会跟着下一个请求发出去。
8. 应答结束或 CLI 断开时，先把这次请求的费用记到用户当天的花费上，再释放预留的额度和并发名额，并关闭到上游的连接。CLI 在上游还没给出应答头时断开也一样：中继在等上游的同时监听 CLI 的连接，不等到上游应答或读超时。

### 3.4 转发什么，拒绝什么

记平台连接 Base URL 的路径为 `P`（比如 `/v1`），`A` 是 `P` 去掉末尾一个 `/v1` 后的路径（`cloud_agent_shared/model_connections.py` 里 `anthropic_base_url` 的规则）。只放行下面这些：

| 方法和路径 | 计费 |
|---|---|
| `POST P/chat/completions`、`POST P/responses`、`POST A/v1/messages` | 是 |
| `POST A/v1/messages/count_tokens` | 否 |
| `GET P/models`、`GET A/v1/models` | 否 |

请求的原始路径要和其中一条逐字节相同，带转义或点段的路径自然不匹配。其他路径答 404。按整条路径而不是前缀放行，是因为前缀会把同一前缀下的其他接口一并放出去，比如供应商保存的 Responses 记录。

- **上游地址。** 平台连接 Base URL 的 origin，加上请求的原始路径和查询串。目的地由平台连接决定，请求不能选择。拨号沿用连接验证的规则：只连公网 IPv4 的 HTTPS，地址固定，不跟随重定向，不读环境里的代理。
- **请求头。** 只转发一个白名单里的头：`accept`、`content-type`、`user-agent`、`anthropic-version`、`anthropic-beta`、`openai-beta`。其余的一律不转发，包括 `cookie`、`x-forwarded-*`、`OpenAI-Organization`、`OpenAI-Project`：有些供应商会按请求头决定这把 key 记到哪个组织或项目、算作哪个应用的调用，sandbox 借的是 key 的使用，不是这些权限。新 CLI 需要供应商看到别的头时，往白名单里加。`accept-encoding` 由中继自己写成 `identity`，不管 CLI 带的是什么：应答原样流回去，中继要在途中读出其中的用量记录，所以要求供应商不压缩。平台 key 用 CLI 原来的那种方式带上：原来是 `Authorization: Bearer` 就还是它，原来是 `x-api-key` 就还是它。第 3.5 节配置的用户标签头由中继自己写（CLI 自己带的不算数）。
- **请求体。** 推理请求整体读入后再转发，上限 32 MiB，超过答 413。只读顶层的 `model`，不改内容；它里面的文本用来估算费用（第 5.2 节）。
- **应答。** 原样流式传回，包括供应商的错误和错误体，CLI 靠它们决定重试和压缩上下文。中继只读其中的用量记录，不改一个字节。例外是上游答 401：那是平台 key 被拒绝，CLI 手里的占位凭据没有错，所以改答 502 并记到平台连接的健康状态里，管理页显示“平台 key 被供应商拒绝”。
- **不记录内容。** 日志只有结构化事件：job、对话、用户 id、模型、状态码、耗时、拒绝原因，以及计费时的 token 数和费用。请求体、应答体、key 和占位凭据都不进日志。

拒绝的应答用 CLI 认识的错误形状：路径是 `…/messages` 时用 Anthropic 的 `{"type":"error","error":{…}}`，其余用 OpenAI 的 `{"error":{…}}`。

| 情况 | 状态码 | `code` |
|---|---|---|
| 凭据不是占位凭据，或对话没有运行中的 turn | 401 | `invalid_credential` |
| 平台连接已停用、已删除或已变更 | 403 | `provided_unavailable` |
| 模型不在列表里、没有价格，或不对这个用户开放 | 403 | `model_not_allowed` |
| 今天的额度用完 | 403 | `allowance_exhausted` |
| 剩下的额度都被进行中的请求预留了 | 429，`Retry-After: 2` | `budget_reserved` |
| 并发名额已满 | 429，`Retry-After: 1` | `concurrency_limited` |
| 请求体不是 JSON 或没有 `model` | 400 | `invalid_request` |
| 路径不在放行列表里 | 404 | `not_found` |
| 请求体超过 32 MiB | 413 | `request_too_large` |
| 供应商连不上，或平台 key 被拒绝 | 502 | `provider_unreachable`、`provider_refused_credential` |
| 数据库不可用，或平台 key 解不开 | 503 | `relay_unavailable` |

额度用完用 403 而不是 429：各家 SDK 会自动重试 429，而额度在当天重置之前重试没有意义。并发已满和额度被预留都是马上就会变的状态，用 429：进行中的请求结束后，实际费用通常比预留的少，同一个请求再发就可能通过。

### 3.5 供应商看到什么

供应商看到的是平台 key 和 CLI 的请求。平台连接可以另外配置一个用户标签头（`end_user_header`，比如 `X-End-User`）。配置以后，中继在每个请求上加这个头，值是账号 id 在设置根密钥的一个独立域下的 HMAC，取前 24 位十六进制。它不含邮箱、登录名和账号 id；管理员可以为一个账号算出它，在供应商那边查这个用户的记录。

这个头让供应商可以按终端用户归因或限额。本服务不依赖它，不配置也能工作。

## 4. 平台连接

### 4.1 数据

两个只做新增的迁移：`0118_operator_model_connections` 建表，`0120_operator_model_budgets` 加上价格、额度和费用。下面是两者之后的样子。金额一律是整数，单位是百万分之一美元（`…_microusd`，5000000 是 5 美元）；价格是每百万 token 多少个百万分之一美元（2800000 是每百万 token 2.80 美元）。这样一次请求的费用是整数运算，四舍五入到百万分之一美元。

```sql
CREATE TABLE agent_operator_model_connections (
    id TEXT PRIMARY KEY CHECK (id ~ '^oconn_[0-9a-f]{32}$'),
    name TEXT NOT NULL,                        -- 用户看到的名字
    base_url TEXT NOT NULL,                    -- https，规则同用户连接
    models TEXT[] NOT NULL,                    -- 1 到 16 个
    default_model TEXT,                        -- models 之一；没有历史的用户预选它
    is_default BOOLEAN NOT NULL DEFAULT FALSE, -- 整个部署至多一个
    enabled BOOLEAN NOT NULL DEFAULT FALSE,    -- 新建时关闭
    api_key_ciphertext TEXT,                   -- 只写；设置根密钥下的独立域；删除时清空
    end_user_header TEXT,                      -- 可选，见第 3.5 节
    key_refused_at TIMESTAMPTZ,                -- 供应商从什么时候起拒绝这把 key；正常时为空
    user_concurrency INT NOT NULL DEFAULT 4 CHECK (user_concurrency BETWEEN 1 AND 64),
    user_daily_budget_microusd BIGINT CHECK (user_daily_budget_microusd >= 0),
                                               -- 每人每日额度的默认值；NULL 是还没选
    model_checks JSONB NOT NULL DEFAULT '{}',  -- 管理员验证的结果，形状同用户连接
    config_revision BIGINT NOT NULL DEFAULT 1,
    credential_revision BIGINT NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ
);

-- 每个模型的条件：价格、给谁用、并发。启用平台连接之前，每个模型都要有输入价和输出价。
CREATE TABLE agent_operator_model_limits (
    operator_connection_id TEXT NOT NULL REFERENCES agent_operator_model_connections (id),
    model TEXT NOT NULL,
    input_price BIGINT CHECK (input_price >= 0),               -- 可以是 0：免费的模型
    output_price BIGINT CHECK (output_price >= 0),
    cache_read_price BIGINT CHECK (cache_read_price > 0),      -- NULL：缓存读按输入价算
    cache_write_price BIGINT CHECK (cache_write_price > 0),    -- NULL：缓存写按输入价算
    named_only BOOLEAN NOT NULL DEFAULT FALSE,                 -- 真：只给点名的用户
    global_concurrency INT CHECK (global_concurrency >= 1),    -- NULL 不限
    user_daily_requests INT CHECK (user_daily_requests >= 0),  -- 0118 的请求次数上限，不再读
    PRIMARY KEY (operator_connection_id, model)
);

-- 个别用户自己的每日额度。有这一行就用它，没有就用平台连接的默认值。
CREATE TABLE agent_operator_user_budgets (
    operator_connection_id TEXT NOT NULL REFERENCES agent_operator_model_connections (id),
    user_id TEXT NOT NULL,
    daily_budget_microusd BIGINT NOT NULL CHECK (daily_budget_microusd >= 0),  -- 0 不提供
    PRIMARY KEY (operator_connection_id, user_id)
);

-- 个别用户在某个模型上自己的答案。有这一行就用它，没有就看模型的 named_only。
CREATE TABLE agent_operator_model_user_access (
    operator_connection_id TEXT NOT NULL REFERENCES agent_operator_model_connections (id),
    user_id TEXT NOT NULL,
    model TEXT NOT NULL,
    offered BOOLEAN NOT NULL,
    PRIMARY KEY (operator_connection_id, user_id, model)
);

-- 中继记的用量。
CREATE TABLE agent_operator_model_usage (
    operator_connection_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    model TEXT NOT NULL,
    day DATE NOT NULL,                                         -- UTC
    requests BIGINT NOT NULL DEFAULT 0,                        -- 供应商答复了的请求
    cost_microusd BIGINT NOT NULL DEFAULT 0,                   -- 它们的费用
    input_tokens BIGINT NOT NULL DEFAULT 0,                    -- 全部输入 token，含缓存的
    cache_read_tokens BIGINT NOT NULL DEFAULT 0,
    cache_write_tokens BIGINT NOT NULL DEFAULT 0,
    output_tokens BIGINT NOT NULL DEFAULT 0,
    estimated_requests BIGINT NOT NULL DEFAULT 0,              -- 其中按估算计费的请求
    PRIMARY KEY (operator_connection_id, user_id, model, day)
);

-- 一个 residency 的进程是在平台连接的哪个修订号上启动的。
CREATE TABLE agent_operator_connection_residencies (
    thread_id TEXT PRIMARY KEY
        REFERENCES agent_thread_sandbox_memberships (thread_id) ON DELETE CASCADE,
    residency_generation BIGINT NOT NULL CHECK (residency_generation >= 1),
    operator_connection_id TEXT NOT NULL REFERENCES agent_operator_model_connections (id),
    credential_revision BIGINT NOT NULL CHECK (credential_revision >= 1)
);
```

- `0118` 还建了 `agent_operator_model_user_limits`（个别用户在某个模型上的请求次数）。`0120` 把它和 `user_daily_requests` 里“给谁用”的信息搬过来：默认 0 的模型成为只给点名用户的模型，用户自己的数字成为他自己的答案（0 是不给，其余是给）。请求次数没有价格，所以不从它推出额度或价格。这张表和这一列留着不读，由之后的迁移删掉。
- 上一个版本和这个版本同时运行时，它照旧按自己读到的请求次数上限执行。这个版本第一次给一个模型写条件时，把 `user_daily_requests` 写成 0，上一个版本读到的就是“默认不提供”，不是“不限”。
- 最后一张表和现有的 `agent_model_connection_residencies` 是同一个用途。不能共用那张表，因为它的 `connection_id` 有指向用户连接表的外键（迁移 `0106`）。
- 平台 key 用设置根密钥（`crypto/domain_cipher.py` 的 `settings_root_key`）加密，域是 `cloud-agent/operator-model-connection-key/v1`。和管理员的 web search key 一样，它只留在控制面：任何 claim、residency、`/config`、日志、审计详情和接口应答里都没有它。
- 带 `user_id` 的表不用另外登记就会被账号擦除覆盖：`storage/_sql/local_erasure.py` 按列名找表。

### 4.2 turn 怎么引用平台连接

- **job 直接写平台连接的 id。** `agent_jobs.model_connection_id` 和 `agent_pending_messages.model_connection_id` 没有外键和格式约束（迁移 `0106` 有意如此），可以直接存 `oconn_…`。接口 schema 里的 `MODEL_CONNECTION_ID_PATTERN`（`schemas/jobs.py`、`schemas/messages.py`）和共享的 `CONNECTION_ID` 要接受这个前缀。
- **不为每个用户派生一行连接。** 那份设计需要派生行，是因为每个用户有自己的 key。这里没有属于单个用户的秘密，派生行只会带来“管理员改了连接，要在一个事务里改写所有用户的行”这样的扇出。
- **一个解析函数分流。** `application/model_connections.py` 的 `resolve_turn_connection` 按前缀分流：`mconn_` 照旧按所有者读用户连接；`oconn_` 读平台连接。两者都交给同一个 `require_usable`，因为平台连接的行有同样的字段（`enabled`、`deleted_at`、`models`、`model_checks`）。新任务、后续消息、排队消息、claim、residency 和 Fork 都经过这个函数，所以这些入口不用各自改。
- **多四个拒绝原因**，各有给用户看的一句话：`provided_unavailable`（平台连接被停用或删除）、`provided_model_unavailable`（这个模型被移出列表，或没有这个 agent 能用的 API）、`allowance_none`（这个模型没有对这个用户开放，或他在这个平台连接上的额度是 0）、`allowance_exhausted`（今天的额度用完，写明重置时间）。用户连接上“去设置里启用、验证”那几句话对平台连接不成立，所以单独给。
- **不回退。** 平台连接不可用时，turn 不会换到用户自己的连接上；反过来也一样。现有的“不回退”规则扩展到来源之间。
- **总开关照旧。** `AGENT_MODEL_CONNECTIONS_ENABLED` 关掉时，平台连接和用户连接一样不能用：`require_connection_source` 先查它。
- **按 turn 的所有者算。** Fork 出来的成员、团队环境里别人的 turn，花的都是那个 turn 的所有者自己的额度。

### 4.3 claim 和 residency

- **claim。** `ModelConnectionAccess` 增加 `relayed`。为真时不带 `api_key`；`base_url` 仍是平台连接真实的 https 地址（路径和进程身份要用它）；`connection_id` 是 `oconn_…`；`revision` 是平台连接的 `credential_revision`。转换计划里成员 token 的推导不变。
- **residency。** `residency_credential` 对平台连接返回第 3.2 节的占位凭据，并在平台连接那一行的锁下把修订号写进 `agent_operator_connection_residencies`。先后协议和 `bind_residency` 一样：先绑定的，之后的变更会找到它并关闭进程；先变更的，绑定被拒绝。不写扫描副本，因为没有 key 进入 sandbox。
- **宿主机的核对不变。** `claimed_job.py` 的 `require_residency_connection` 照旧比对 claim 和 residency 的修订号与计划。

### 4.4 宿主机和 sandbox 的改动

- **能力字符串 `model-relay-v1`。** 宿主机配置了 `AGENT_SANDBOX_MODEL_RELAY_URL` 才通告它（`service/host_capabilities.py`）。没有它的宿主机拿到平台连接的 claim 会被退回重排，和 `claim_model_connection` 今天对 `model-connections-v1` 的做法一样。
- **接受 `oconn_`。** 共享的 `CONNECTION_ID`、`resident_launch_config.py` 里 `inference_source` 的格式都要接受它；`launch_fields` 多一个 `inference_relayed`，它是进程固定启动参数的一部分。
- **成员的 URL。** 宿主机把 Base URL 的 origin 换成自己的模型中继地址，路径不变。所以 Anthropic 那条“去掉末尾一个 `/v1`”的规则原样适用。
- **loopback 服务多接受一个上游。** 成员的计量服务和转换服务今天只接受 https 上游（`model_passthrough.py`、`protocol_translator.py`）。对 `relayed` 的启动，它们多接受一个：本机配置的模型中继地址，精确匹配，明文 HTTP。别的 http 上游照旧拒绝。
- **请求前缀计量照常。** 它在成员自己的 loopback 服务里，只是上游从供应商换成了中继。
- **已知限制。** Claude Code 按 URL 的主机名判断是不是自家端点（`runtimes/base.py` 的 `FIRST_PARTY_HOSTS`）。经过中继后它看到的是中继的名字，所以平台连接指向 `api.anthropic.com` 时，Claude Code 按第三方端点的方式工作。

### 4.5 管理员改了平台连接以后

| 改动 | 运行中的进程 |
|---|---|
| 换 key（先验证） | 保留。key 只在中继里，下一个请求就用新的。 |
| 换 Base URL（先验证） | 关闭。`credential_revision` 加一，绑定旧修订号的 residency 被释放。 |
| 增加模型（先验证）、移除模型、改名、改默认模型、改额度、改价格 | 保留。中继下一个请求读到新值；进行中的请求按它开始时读到的价格计费。 |
| 停用、删除 | 关闭，同时中继开始拒绝。 |
| 启用 | 无。 |

换 key 不必关进程，是这个结构带来的：进程拿的是占位凭据，和 key 无关。

## 5. 每日额度和计费

### 5.1 规则

- **额度**的对象是“一个用户、一个平台连接”，这个平台连接的所有模型共用。**窗口**是 UTC 日。**单位**是美元。
- **有效额度**：这个用户在这个平台连接上有单独设置就用它，否则用平台连接的默认值。取值是不小于 0 的金额。
- **默认额度必须明确选。** 没选过默认额度的平台连接不能启用，也不向任何人提供。没有“不限”这个值；确实不想限制的部署填一个足够大的数，或者把模型的价格设成 0。
- **0 是准入开关。** 默认额度是 0、个别用户设成正数，这个平台连接就只给点名的用户用；反过来，给一个用户单独设 0，就是不给他用。
- **每个模型有价格。** 输入价和输出价必填，单位是美元每百万 token，可以是 0。缓存读、缓存写的价格可选：不填时，这部分 token 按输入价算。没有价格的模型不向任何人提供，每个模型都有价格以后平台连接才能启用。
- **每个模型可以只给点名的用户。** 模型默认对所有有额度的用户开放；标成“只给点名的用户”后，只有管理员单独允许的用户能用。管理员也可以单独不让某个用户用某个开放的模型。

### 5.2 一次请求花多少钱

供应商的应答里本来就带着这次请求的用量。三种协议的位置不同：

| 协议 | 普通应答 | 流式应答 |
|---|---|---|
| Chat Completions | 顶层的 `usage` | 带 `usage` 的那个 chunk，通常是 `[DONE]` 之前的最后一个 |
| Responses | 顶层的 `usage` | 结束流的事件（`response.completed` 等）里 `response.usage` |
| Messages | 顶层的 `usage` | `message_start` 给输入一侧，`message_delta` 给输出 token 数 |

中继在把应答原样传回的同时读它（`domain/model_usage.py` 的 `UsageReader`）。流式应答按行读，从每个事件里取用量记录，并累计应答已经生成的文本的字节数（正文、推理、工具调用），供估算用；普通应答在结束时解析一次。除了一行未读完的内容或一个普通应答（都以 8 MiB 为限，超过的不读，这次请求按估算计费），它不保存应答里的任何东西，读完即弃。读用量这件事不会让应答失败：应答里无论是什么，最坏的结果是读不到用量。

读出来的数归成四个：全部输入 token（含缓存的部分）、其中的缓存读、缓存写，以及输出 token。两处细节：

- **Messages 的输入数不含缓存。** 它的 `input_tokens` 是既没读缓存也没写缓存的那部分，另外两项单独给。归一时把三项加起来才是全部输入。另外两种协议的输入数本来就包含缓存的部分。
- **推理 token 只算一次。** 各家对“输出 token 数里含不含推理 token”做法不一。总数表明已经包含时不再加；总数缺失或对不上时加上，取贵的那种读法。
- **缓存命中的几种写法都认。** OpenAI 的 `prompt_tokens_details.cached_tokens`、hybridInference 自己应答里的 `cache_read_tokens`、一些供应商的 `cached_tokens`、DeepSeek 的 `prompt_cache_hit_tokens`。

费用的算法、取整和 hybridInference 一致（`serving.storage.utils.calculate_cost`、`serving.spend_ledger.counted_amount`），只是换成了整数：

```
普通输入 = 全部输入 − 缓存读（有缓存读价格时）− 缓存写（有缓存写价格时）
费用 = 普通输入 × 输入价 + 输出 × 输出价 + 缓存读 × 缓存读价 + 缓存写 × 缓存写价
```

没有单独价格的缓存 token 留在“普通输入”里，按输入价算。每次请求的结果四舍五入到百万分之一美元，和 gateway 的计数器每笔入账时一样。gateway 自己的计费测试用例原样搬进了 `tests/unit/test_operator_models_domain.py`，读用量、算费用两步串起来，每个用例的结果都和 gateway 记的一致。

**应答里读不到用量时按估算收费。** 这包括：应答没有带用量记录；CLI 中途断开（用户按了 Stop），应答没走到带用量的那一步；上游中途断开；供应商不顾要求压缩了应答；中继把请求交给上游以后自己出了错。估算的办法和 gateway 给中途断开的流计费一样（`StreamSession._cancelled_usage`）：输入是请求里的文本，输出是应答断开前已经生成的文本（正文、推理和工具调用）。文本按每 4 个字节一个 token 算，这是 gateway 在没有分词器时的做法；请求里的图片、音频、视频不论大小，分别按 gateway 的常数 85、200、1000 个 token 算，不在媒体块里的 base64 文件按图片算。媒体要单独处理，是因为它是 base64：带着几张截图的对话，请求体有几兆字节，按字节算会被当成上百万 token 的文本。如果应答在中断前已经给出了一部分用量（Messages 在第一个事件里就给了输入一侧），输入就用这部分，输出取已给出的和已生成的两者中较大的，同样记作估算。用量表里这类请求单独计数。

估算偏向哪一边要说清楚：它不知道缓存命中，所以输入按全价算，对缓存命中率高的 agent 请求是多收；按字节估的 token 数和真实分词也有出入。所以“快结束时故意断开”占不到便宜：断开的请求输入按全价、输出按已生成的计。

**不收费的情况。** 请求没有到达供应商（被中继拒绝、连不上、在收到应答头之前超时）不收费。供应商答了错误（4xx、5xx）或重定向不收费。列模型和数 token 这两条路径不收费。

### 5.3 在哪里执行

**接受新 turn 时。** 新任务、后续消息和排队消息发出时都经过 `job_admission.py` 的 `require_connection_source`：模型不对这个用户开放或额度是 0，答 `allowance_none`；今天的额度已经用完，答 `allowance_exhausted`。排队消息按现有“连接不可用”的方式挂起并带上原因。

**claim 时。** 同样的两项检查，不通过就以这个原因结束这个 turn。goal 的自动轮次不在创建时查连接（它在存储层继承上一轮的来源），对它生效的是这一处。goal 控制器要把 `allowance_exhausted` 当作暂停的理由，不能接着开下一轮。

**residency 和 Fork** 只查模型是否开放、额度是不是 0，不查剩余额度：turn 已经在启动。

**每个请求。** 中继解析占位凭据时一并读出这个用户的有效额度、今天在这个平台连接上已经花掉的金额（用量表当天各行费用之和）和这个模型的价格。已花掉的不小于额度，就答 `allowance_exhausted`。应答结束时用一条语句把这次请求记进用量表：

```sql
INSERT INTO agent_operator_model_usage AS u
    (operator_connection_id, user_id, model, day, requests, cost_microusd,
     input_tokens, cache_read_tokens, cache_write_tokens, output_tokens, estimated_requests)
VALUES ($1, $2, $3, (clock_timestamp() AT TIME ZONE 'UTC')::date, 1, $4, $5, $6, $7, $8, $9)
ON CONFLICT (operator_connection_id, user_id, model, day) DO UPDATE
    SET requests = u.requests + 1,
        cost_microusd = u.cost_microusd + EXCLUDED.cost_microusd,
        ...
```

同一个用户、同一个模型同时结束的请求只在自己那一行上短暂排队，不会丢。

**写不进去的费用留着重写**（`api/_model_relay_charges.py`），思路和 gateway 的 `SpendLedger` 一样：没写进去的钱继续计入额度，稍后再写。应答照常传回；这笔费用作为预留计入这个用户的额度，按 1、2、5、10、30、60、120、180、240 秒的间隔再写，前后约十一分钟。写进去了就释放预留；次数用完还没写进去，就记一条 error 级日志（`agent_model_relay_charge_lost`）并带上金额。中继进程停止前，每笔还没写进去的费用会立即再试一次。

只有确定没写进去的才重写：拿不到数据库连接，或者服务器拒绝了这条语句。语句发出去以后连接断了，可能已经记上了，重写会让用户为一个请求付两次钱，所以不重写，只记一条 error 级日志（`agent_model_relay_charge_unknown`）。这一点也和 gateway 一样：它对“结果未知”的写入不重试。

跨过 UTC 零点的请求记在它结束的那一天。

### 5.4 进行中的请求

费用在应答结束时才知道。如果只看用量表，同时进行的几个请求会各自看到同一个剩余额度，一起通过。所以每个请求被接受时预留一笔估算的费用（估算办法同 5.2，上限是当时剩下的额度），结束时先记实际费用、再释放预留。下一个请求看到的是“已花掉的 + 别的请求预留的”。

- 剩下的额度都被预留时，新请求答 429 `budget_reserved`，不是 403：额度没有用完，进行中的请求结束后，实际费用通常比预留的少。CLI 会自动重试 429。
- 预留是中继进程内的计数，不写数据库，和并发名额一样。只有一个中继进程时它是准确的；跑多个进程或副本时每个各自计数。
- 所以额度可以被超出，超出的量大约是进行中的请求的实际费用超过它们预留的部分：估算不含输出，也可能低估输入。在每人并发 4 的默认值下，这是几次请求的费用。

### 5.5 并发

- **每个用户进行中的请求数**：平台连接上的 `user_concurrency`，默认 4。
- **每个模型所有用户进行中的请求数**：模型上的 `global_concurrency`，可选。供应商对某个模型有并发上限时用它。名字里的 global 指跨用户，不是跨进程。

两者都是中继进程内的计数，不写数据库，超出答 429。部署文件只起一个中继进程，这时上限就是填的数；跑多个进程或副本时每个各自计数，实际上限是它的倍数。管理页、API 字段说明和运维文档都这样写明。要它严格等于供应商的并发上限而又要跑多个中继时，得把计数挪到共享状态里，这一版不做。

### 5.6 用户和管理员看到什么

- **用户**：模型选择器里，平台模型显示今天还剩多少额度；额度用完后这个平台连接的模型都不可选，并写明重置时间。Settings → Models 最上面是一个没有标题的列表，只列模型名字，不说这些模型从哪来，没有可编辑的内容；额度和用量不在这里重复。部署提供了模型时，用户自己的模型连接折叠在列表下面，点标题展开，从“去设置连接”的链接进来时直接展开，页面已经打开时点这种链接也会展开；展开过的内容折叠后只是隐藏，填到一半的表单不会丢。部署没有提供模型时，页面和以前一样只有模型连接，不折叠。还不知道部署是否提供模型时页面显示“加载中”；几秒内没有答复就按没有提供处理，先把模型连接展开给用户，答复晚到时再把列表补在上面，不把已经展开的部分折回去。
- **管理员**：平台连接的页面上设每人每日额度的默认值；每个模型的价格、是否只给点名的用户、并发；给个别账号单独设额度，单独允许或不允许某个模型。用量表按用户、模型和日期列出请求数、输入和输出 token 数、费用，以及其中按估算计费的请求数。这些数是中继在 sandbox 之外从供应商的应答里读的；Usage 页上的 token 仍然是 agent 自己报的数（`runtime_reported`），两者互不相加。

### 5.7 为什么从次数改成金额，以及它不是什么

第一版按请求次数设上限，理由是本服务没有价格、不想解析应答。上线前核对供应商的模型目录后，这个理由不成立：一个平台模型的上下文可以到一百万 token，一次请求的输入费用从不到一美分到几美元，相差上千倍。次数既不能限制花费，也无法向用户解释一个任务会用掉多少。按金额设额度是推理服务自己对它的用户的做法，管理员在两边用的是同一种单位。

改成金额以后仍然要说清它不是什么：

- **它是额度，不是账单。** 没有人会收到账单。费用是中继用管理员填的价格和供应商报的 token 数算出来的，只用来决定一个用户今天还能不能继续用。
- **它可能和供应商的账对不上。** 供应商按时段调价，或者把一个模型分到价格不同的线路上时，它实际扣的钱和这里算的不一样。按估算计费的请求也对不上。要逐笔一致，得由供应商在应答里给出这次请求的费用，中继只累加；这要供应商支持，本文不依赖它（第 12 节）。
- **钱的总闸仍然在供应商。** 平台连接应当用供应商那边一个专门的账号或 key，由供应商限制它的预算和可用模型。本服务的额度可以被进行中的请求超出一点（5.4）；数据库长时间不可用或进程在重写前退出时，个别请求的费用会漏记（5.3）。
- **只有每日额度。** 没有每月额度，也没有按模型分别设的金额上限。用量表按天存，之后要加每月额度不用改它。

## 6. 接口和界面

**用户接口**

- `GET /v1/agent/config`（`api/config.py`）增加 `provided_sources`：每个已启用的平台连接的 id、名字、是否默认、默认模型，对这个用户开放的每个模型（可用的 agent、推理强度），以及这个用户在这个平台连接上的每日额度、今天已花掉的金额和重置时间。这个用户的额度是 0，或没有一个模型对他开放时，这个平台连接不出现。有默认平台连接并且至少一个模型对这个用户开放时，`model_connection_required` 为假。
- 新任务和后续消息提交 `oconn_…` 作为 `model_connection_id`，其余不变。
- 默认预选沿用那份设计的建议：最近一次 turn 的来源仍然可用就用它；否则用默认平台连接的 `default_model`。
- `JobUsageResponse` 现有的 `external_provider` 只表示“跑在连接上”。增加 `source`（`personal` 或 `provided`）和来源名字，界面据此显示不同的说明。

**管理接口**（前缀 `/admin/agent/operator-model-connections`，和其他管理路由一样依赖 `verify_admin_access` 并答 `no-store`）

- 列表、新建、修改、删除。改 Base URL、换 key 或增加模型时，在保存的请求里用平台 key 直接验证，规则和用户连接一样；key 字段只写。管理员的验证不占用任何用户每小时的验证次数。
- 新建和修改接受 `user_daily_budget_microusd`，每人每日额度的默认值。启用之前它必须选过，每个模型也必须有价格，否则答 409 `operator_model_terms_missing`，说明缺什么。
- `PUT …/{id}/terms` 设一个模型的价格、是否只给点名的用户、并发。模型 id 里可以有斜杠，所以放在请求体里，不放在路径里。
- `GET …/{id}/users?cursor=` 分页列出有单独设置或今天有用量的用户，每人带上自己的额度、对各模型的单独答案、今天花掉的金额和请求数。
- `PUT …/{id}/user-budgets` 给一个账号单独设额度（账号用 id 或邮箱指定），`DELETE …/{id}/user-budgets?account_id=` 清除。
- `PUT …/{id}/user-access` 单独允许或不允许一个账号用某个模型，`DELETE …/{id}/user-access?account_id=&model=` 清除。
- `GET …/{id}/usage?from=&to=` 按用户、模型、日期给出请求数、token 数、费用和按估算计费的请求数，最多 92 天。
- 列表的应答里带 `relay_hosts`：有多少台在册宿主机通告了 `model-relay-v1`。

**审计**

- 平台连接的新建、修改、启用、停用、删除各一个动作（`agent.operator_model_connection.*`），类别 `settings`；换 key 单独一个动作，值永远是 `[redacted]`；模型条件的修改一个动作（`set_terms`）。验证发生在新建和修改的请求里，不单独记。
- 用户单独额度的设置和清除（`agent.operator_model_user_budget.*`）、单独允许或不允许某个模型的设置和清除（`agent.operator_model_user_access.*`），类别 `accounts`，目标是那个账号。
- 第一版的 `set_limit` 和 `agent.operator_model_user_limit.*` 不再写入，但留在登记表里：已有的记录还要能读。
- 新动作在 `domain/audit.py` 里声明。`tests/unit/test_audit_registry.py` 还要求迁移里的冻结副本和 Audit 页的清单与它一致。

**界面**

- 模型选择器（`composer/modelSource.ts`）里一个选择本来就是“模型 + 连接 id”的一对，平台连接不需要新的选择模型。来源分成“由本部署提供”和“你的连接”两组；两边有同名模型时，标签带上来源名字。
- 现在写死某个供应商名字的引导文案（`lib/i18n/dictionaries/modelSources.ts`）改成读平台连接的名字。有默认平台连接时，首次使用清单里“添加模型连接”一项视为已完成。
- 管理页在 Admin → Settings 下新增 Models；额度、价格、账号和用量表都在这一页。金额在界面上按美元输入和显示，接口里是整数。

## 7. 安全边界的变化

**要改的文字**

- AGENTS.md §6：sandbox 网络的列表加上“组桥上模型中继的端口”；“Model connection”一条加上平台连接；“There is no per-task dollar budget and no local quota or billing”一句改成：只有平台连接有按用户的每日额度，中继按每次请求用掉的 token 和管理员填的价格记账；这是额度，不是账单，没有人被计费。
- AGENTS.md §7：第一条的可达列表加上模型中继；新增一条“平台提供的模型”，内容是下面“不变的”和“接受的”两组。
- SECURITY.md 的 “It holds the owner's own provider key for the turn it runs, and short-lived scoped tokens” 补上：用平台连接时只持有占位凭据。它在文档站上，要更新中文目录并跑 `make docs-verify`。
- README 的 How it works 和那张图、`ENVIRONMENT.md`（新变量）、`docs/developer/operations.md` 的 The sandbox network 和 Owner model connections 两节。

**不变的**

- 平台持有的长期凭据不进 sandbox，也不到宿主机。
- 组桥上的 relay 都是固定目的地，没有由调用者选择目的地的代理。模型中继的上游由平台连接决定，路径和模型都有白名单，不是占位凭据的请求一律拒绝。
- AGENTS.md §5：没有 Gateway 依赖。中继按 URL 对接任何供应商，不含针对某个供应商的客户端；登录、准入和 turn 的生命周期都不调用供应商。

**接受的**

- **在自己运行中的 sandbox 里转发。** 用户可以在 turn 运行期间从 sandbox 里开代理，把中继借给外面的程序用。这需要一个运行中的 turn，花的是他自己的额度，日志里记在他名下。
- **额度可以被超出一点。** 进行中的请求只预留了估算的费用，超出的量大约是这几次请求的实际费用超过预留的部分（第 5.4 节）。
- **同组成员。** 同一个组里的兄弟成员共用工作区，一个成员留下的脚本可能被另一个成员执行并用到它的占位凭据。这和 MCP、GitHub 的 residency 凭据是同样的已知情况，消耗的是那个 turn 的所有者的额度。
- **内容经过控制面。** 平台连接的请求和应答流经模型中继。中继不保存也不记录它们，但控制面在这条路径上，这是用户连接没有的。

## 8. 下游发行版要配置什么

这一节只是示例，值由部署自己定。

- 在供应商那边准备一个专门的账号和一把 key，限制它能用的模型，给它设预算。这把 key 只填进管理页，不进任何文件。
- 宿主机部署打开 `model-relay` profile，给 `model-tunnel` 配独立的 SSH 密钥和 `permitopen`。
- 在 Admin → Settings → Models 新建平台连接：

```yaml
name: <供应商的名字>
base_url: https://<供应商的推理 API>/v1
models: [<模型 a>, <模型 b>]
default_model: <模型 a>
end_user_header: X-End-User          # 供应商支持按终端用户归因时
user_concurrency: 4
user_daily_budget: 5.00              # 每人每天 5 美元，所有模型共用
terms:                               # 价格是美元每百万 token，照供应商对这把 key 的价格填
  <模型 a>: {input: 2.80, output: 8.80, cache_read: 0.80}
  <模型 b>: {input: 0.15, output: 0.50, named_only: true, global_concurrency: 8}
```

供应商那边不需要实现任何东西。价格是管理员照供应商的价目表填的：供应商调价以后要跟着改，否则这里算出来的费用和供应商实际扣的不一样。

## 9. 失败情况

- **模型中继或隧道不可用。** 平台连接的请求连接失败，CLI 按自己的规则重试；一直不恢复，turn 以 CLI 报的错误结束。用户连接的 turn 不受影响。每个组启动前，现有的 guest 网络检查会逐个探测 relay 列表里的端口（`network/guest_network_check.py`），模型中继加进列表后就在其中。
- **模型中继重启**（控制面发布时会发生）。进行中的应答会断，CLI 重试。这是用平台连接的代价，要写进运维文档。
- **供应商故障。** 错误原样传回，turn 以它结束。不回退到别的来源。管理员可以停用平台连接并写明原因，模型选择器显示它。
- **平台 key 被供应商拒绝。** 中继答 502，管理页显示健康状态和开始时间，记 error 级日志。
- **数据库不可用。** 中继不能校验 fence，答 503。应答进行中数据库才不可用时，应答照常传回，费用留在进程里稍后重写（第 5.3 节）。
- **应答被中途截断。** 模型中继重启、上游断开或用户按 Stop 时，这次请求读不到完整的用量，按估算计费（第 5.2 节）。

## 10. 上线顺序和兼容

宿主机先于控制面发布，每一边只兼容上一个版本（AGENTS.md §4）。

1. **宿主机**：新镜像认识 `relayed` 的 claim 和 `oconn_`，loopback 服务接受模型中继这个上游。Compose 里 `model-tunnel` 在 `model-relay` profile 后面，和 `github-tunnel` 的做法一样。没配模型中继的宿主机不通告能力，行为和今天完全一样。
2. **控制面**：迁移只新增表；新增 `model-relay` 服务，同样在 profile 后面；平台连接新建时是关闭的。上一个版本的控制面不会读到这些表，也不会发出 `oconn_`。
3. **启用**：所有控制面副本都运行新版本以后，管理员才能启用平台连接；没有任何在册宿主机通告 `model-relay-v1` 时，管理页给出警告。平台连接的 turn 只会被有这个能力的宿主机领走。
4. **降级**：只要存在平台连接或引用它的 turn，迁移的降级就拒绝执行，和 `0106` 的做法一样。

发布清单：两个新服务要登记进 `deploy/release/artifact_compose.py` 的 `STACKS`。`deploy/DEPLOY_PROTOCOL` 不用改，加 `github-tunnel` 时也没有改。`ENVIRONMENT.md` 增加 `AGENT_SANDBOX_MODEL_RELAY_URL`、`AGENT_MODEL_TUNNEL_*` 和 `AGENT_MODEL_RELAY_BIND` / `AGENT_MODEL_RELAY_PORT`。

## 11. 需要定的事

**2026-11-13 的决定。** 和维护者讨论后，确定按本设计做，下面三处改动成立。同一天合进 `dev` 的另一条路线，是给每个获准用户在推理服务上开一个账号、发一把只能用 agent 模型的 key，由用户自己填进个人连接（#1155 和推理服务那边的配套改动）。它和本设计解决的是同一个需求，走的是第 2 节说的每人一把 key 的路线，还要求推理服务认识本服务的用户。按这个决定，它在本设计合并以后由后续的 PR 撤回。

接受本设计，就要改这三处：

1. **组桥上再加一个 relay。** [只保留独立账号](2026-10-30-independent-accounts-only.md)删掉了组桥上的推理中继，那份设计也把中继列为不计划做。本设计加回一个，但它不依赖任何一个供应商，也不带回登录、准入和 grant。
2. **本服务开始执行用量上限。** AGENTS.md §6 现在写的是没有本地额度。本设计只对平台连接加请求次数和并发的上限，金额仍然不管。
3. **平台模型的请求内容经过控制面。**

另外要选的：

4. 配置入口只做管理页，还是同时支持那份设计里由部署声明的文件。建议第一版只做管理页。
5. 窗口只做 UTC 日，还是也要更短的滚动窗口。建议第一版只做 UTC 日。

**2026-11-14 的修订。** 用量上限改成按金额：每人在每个平台连接上一个每日额度，所有模型共用；每次请求的费用由本服务用管理员填的价格和应答里的 token 数算，不改推理服务。第 2 项相应改成“本服务开始执行每日额度并为它记账，仍然不计费”。其中两处是作者的选择，要在实现的 PR 里和维护者确认：额度是每人一个总数而不是每个模型各一个；费用由本服务自己算而不是由供应商在应答里给出（第 12 节列了另一种做法）。
6. `user_concurrency` 的默认值。建议 4。
7. 默认预选和“自带优先”的规则，沿用那份设计的建议（它的第 5、6 项）。

## 12. 放弃的做法

- **每人一把 key。** 见第 2 节。
- **平台的 key 直接放进每个 sandbox。** 那份设计已经否决：一处泄露花掉所有人的额度，也无法只停一个用户。
- **在宿主机上换 key**（那份设计的 3b）。模型请求不经过控制面，但平台 key 会出现在每台跑 sandbox 的机器上，宿主机上也要多一个持有秘密、改写请求的组件；今天宿主机上的隧道都只是原样转发。如果实测表明控制面承受不了流量，这是退路：sandbox 一侧看到的地址和占位凭据都不变，只是换 key 的位置从控制面移到宿主机，到那时再决定是否接受 key 上宿主机。
- **按来源地址放行。** 让供应商只接受从宿主机出口地址来的请求。宿主机的出口地址由它所在的网络决定，可能和别的机器共用，托管的宿主机还会更换；控制面也不记录这些地址。
- **在供应商那边为每个用户建成员或账号。** 这是账号对应关系加持续同步，账号导入设计明确不做。
- **按请求次数设上限。** 第一版的做法，见第 5.7 节。
- **由供应商在应答里给出每次请求的费用。** 这样和供应商的账逐笔一致，管理员也不用填价格。它要求供应商在三种协议的普通应答和流式应答里都带上费用，只对实现了它的供应商有效。中继读用量的那部分两种做法是共用的：以后供应商给出费用时，读到就用它，读不到再自己算，数据和界面都不用改。
- **每个模型各自的金额上限。** 改动最小，但管理员要为每个模型猜一个数，用户看到的也是几个互不相通的余额。一个总额度加上“这个模型给谁用”的开关能表达同样的限制。
- **把预留写进数据库。** 这样多个中继进程之间也准确，但中继重启会留下收不回的预留，要另外清理。和并发名额一样，这一版只在进程内计数。

## 13. 验证

**单元**：新 scope 的 token 签发和解析，三种 token 互不通用；路径白名单（含点段、转义、前缀冒充）；两种带凭据方式的换头；从三种协议的请求体取 `model`；有效额度的解析（单独设置、默认值、0、没选）和模型给谁用（开放、只给点名的、单独的答案）；费用的算法（缓存 token 有无单独价格、四舍五入，以及 hybridInference 自己的计费用例）；从三种协议的普通应答和流式应答读用量（分片边界、扩展字段、推理 token 只算一次、中途截断、读不到、过长的行）；三种协议的拒绝形状。

**服务端**（和 `tests/servers/test_github_relay.py` 一样，用 `httpx.MockTransport` 充当上游）：

- 不是占位凭据、没有运行中的 turn、Stop 之后、residency 轮换之后，都被拒绝。
- 模型不在列表里、没有价格、不对这个用户开放、额度用完，各自的状态码和 `code`。
- 三种协议的应答各按读到的用量计费，流式应答原样到达 CLI；读不到用量、供应商擅自压缩时按估算计费。
- 上游答错误（4xx、5xx）、连不上、应答头之前超时，不收费。请求交给上游之前中继自己出错的不收费，交出去之后出错的按估算收费。
- 费用写不进数据库时应答照常传回，费用作为预留计入额度并稍后重写，写进去后释放；重试用完了记 `lost`；结果未知的不重写；进程停止前每笔再试一次。
- 断开的流按估算的输入和已生成的输出计费；gateway 自己的计费用例经读用量和算费用两步，结果和 gateway 记的一致。
- 进行中的请求预留估算的费用，结束时换成实际费用；预留不超过剩下的额度；额度都被预留时新请求答 429，预留释放后同一个请求通过。
- 只有白名单里的请求头到达上游；`OpenAI-Organization`、`OpenAI-Project`、`cookie`、`x-forwarded-for` 之类到不了。不管 CLI 带什么 `accept-encoding`，上游收到的都是 `identity`。
- 流式应答逐帧到达，CLI 断开时上游被关闭；CLI 在应答头到达之前断开、上游在应答中途出错，并发名额和预留的额度同样被释放，请求按估算或已读到的部分用量计费。
- 共用的上游客户端不把一次应答设的 cookie 带到之后的请求里。
- 平台 key 不出现在任何 claim、residency、`/config`、应答和日志里。
- 平台连接的 turn 走完新任务、后续消息、排队消息、goal 轮次和 Fork，每处的拒绝原因一致。
- 管理接口只有管理员能用，`no-store`，每个动作一条审计，key 是 `[redacted]`。

**数据库**（`make test-db-local`）：`0118` 的升级和降级拒绝；`0120` 把“给谁用”搬过来、不凭空造出额度，降级后再升级；同时结束的请求的费用都记上；一个额度跨模型累计，按 UTC 日归零；绑定和失效的两种先后顺序；账号擦除后这个用户的额度、单独答案和用量行都不在了；`scripts/check_rolling_upgrade.py` 通过。

**前端**（`make ci-frontend`）：模型选择器的分组、剩余额度、用完后的状态；Settings 的只读列表和模型连接的折叠；管理页的默认额度、模型价格、账号的单独额度和单独答案、用量表；金额的输入和显示；文案里没有写死的供应商名字。

**端到端**（Playwright，沿用 `e2e/service.mjs` 的接口夹具）：新用户登录后不做任何设置就能开始任务；额度用完后新任务被拒绝并显示重置时间；管理员给他加额度后可以继续。

**staging 实测**：

- 五个运行时各跑一个真实的 turn，经过中继，Kata 宿主机。
- 长应答：持续几分钟的流式应答不被隧道或中继截断。
- 延迟：同一个供应商，直连和经中继各测首字节时间和总时间。
- 并发：若干个长应答同时进行时的吞吐。
- 重启：有请求在进行时重启 `model-relay`，记录五个 CLI 各自的表现。
- 记账：五个运行时各跑一个真实的 turn 后，用量表里的 token 数和费用与供应商后台同一把 key 的记录对比；按估算计费的请求占多少。

**每个实现 PR 的门禁**：`make check`；涉及存储时 `make test-db-local`；涉及前端时 `make ci-frontend`；改了文档站页面时翻译并 `make docs-verify`。

## 14. 实现状态

和本文在同一个 PR 里的：

- 控制面：迁移 `0118`；平台连接的解析、claim 和 residency（`application/model_connections.py`）；模型中继和它的 listener（`api/model_relay.py`、`create_model_relay_app`）；管理接口和审计（`api/admin_operator_models.py`）；`/v1/agent/config` 的 `provided_sources`；用量应答的 `source`。
- 宿主机：`model-relay-v1` 能力；`relayed` 的启动把地址换成本机的模型中继；成员的 loopback 服务只在启动标明上游是模型中继时接受明文 HTTP。
- 部署：控制面的 `model-relay` 服务和宿主机的 `model-tunnel`，都在 `model-relay` profile 后面。
- 界面：模型选择器分组和剩余用量，Settings 的只读列表和折叠的模型连接，Admin → Settings → Models。
- 文档：AGENTS.md §2、§6、§7，SECURITY.md，README，ENVIRONMENT.md，运维和升级说明。

和设计不同的地方：

- 管理接口里模型和账号放在请求体或查询串里，不放在路径里（第 6 节已按实现改写）。
- 多一个拒绝原因 `provided_model_unavailable`（第 4.2 节）。
- 平台连接表多一列 `key_refused_at`，`api_key_ciphertext` 在删除后为空（第 4.1 节）。
- 用户的单独额度在平台连接的管理页上设，不在账号详情页；用量表也在这一页，不在 Admin → Usage。

2026-11-14 的修订（按金额的每日额度）在随后的一个 PR 里：

- 控制面：迁移 `0120`；读用量和估算（`domain/model_usage.py`）、算费用（`domain/operator_models.py`），规则、取整和估算都对齐 hybridInference，并带上它的计费测试用例；中继在应答结束时记账、为进行中的请求预留额度，写不进去的费用留着重写（`api/_model_relay_charges.py`）；管理接口的 `terms`、`user-budgets`、`user-access` 和带费用的用量；`/v1/agent/config` 的每日额度和今天已花掉的金额。
- 界面：模型选择器的剩余额度；管理页的默认额度、模型价格、账号和用量表。
- 文档：AGENTS.md §2、§6、§7，README，运维和升级说明。

没做的：

- 第 13 节的 Playwright 端到端用例和 staging 实测。延迟、长应答、并发和中继重启时五个 CLI 的表现都还没有数据。
- 第 11 节第 4 项里“由部署声明的文件”这个配置入口。只有管理页。
- 托管计算节点的自动配置。本 PR 没有改它，所以自动扩容出来的宿主机不带 `model-tunnel`，也就领不到平台连接的 turn。
