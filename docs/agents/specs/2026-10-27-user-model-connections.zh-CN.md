# 用户自带模型连接（BYOK）

状态：已实现。推理强度见[上游声明的推理强度](2026-11-05-upstream-reasoning-effort.md)。部署开关 `AGENT_MODEL_CONNECTIONS_ENABLED` 默认开启，运维可以为维护把它关掉（值为 `0`、`false` 或 `no`）。关闭时没有新 turn 能启动，因为连接是唯一的推理来源。

## 1. 决定

用户在自己的账号下添加推理连接：Base URL、API key 和最多 16 个模型 ID。任务直接用这把 key 跑，费用由 key 的主人承担。

- 连接只属于创建它的用户，不随团队共享，Fork 也不会带给别人。
- key 加密存储，对用户接口只写不读。执行所需的凭据只通过认证的内部通道交给对应成员。
- 平台不扣费，也不代设上游预算。运行时上报的 token 数照常显示，未知的上游费用不显示成零。
- 不做的事：通用协议转换器、路由或计费网关、key 池、推理时的试错回退、团队共享连接、管理员代付、私网地址例外。除[协议兼容设计](2026-10-28-model-protocol-compatibility.zh-CN.md)里的两种转换外，模型不提供某个 CLI 的协议，就不能给那个 CLI 用。
- 部署自己提供的模型见[平台模型中继](2026-11-12-platform-model-relay.zh-CN.md)。

## 2. 协议

| 运行时 | 协议 | 模型需要可用的 API |
|---|---|---|
| OpenCode、Kilo、pi | Chat Completions | `chat_completions` |
| Codex | Responses（provider 的 `wire_api` 固定为 `responses`） | `responses` |
| Claude Code | Anthropic Messages | `anthropic_messages` |

这张表是共享词汇 `cloud_agent_shared/model_connections.py` 的 `RUNTIME_PROTOCOLS`。判断「这个 turn 能不能在这个连接上跑」只有一个函数：`domain/model_connections.require_usable`。它依次检查：连接存在且属于本人、未删除、已启用、运行时有对应协议、模型在列表里、该模型在当前 key 和 Base URL 下有验证结果、验证结果给这个运行时找到了路径（原生优先，否则走转换）。每种拒绝都有固定的原因码和给用户看的句子（例如 `model_unverified`、`protocol_unsupported`），都不会退到别的连接或模型。

Base URL 原样使用，不补 `/v1`，因为有的服务商路径是 `/api/v1` 或 `/v1beta/openai`。CLI 自己在后面拼 `/chat/completions` 或 `/responses`。

Anthropic Messages 例外：Claude Code 的 SDK 会在 `ANTHROPIC_BASE_URL` 后面拼 `/v1/messages`。所以这个协议只去掉 Base URL 结尾的一个 `/v1`（`anthropic_base_url`），其他路径原样保留。`https://host`、`https://host/v1`、`https://host/anthropic`、`https://host/anthropic/v1` 都到 `…/v1/messages`。验证用同一个函数算 URL，保证验证和真实 CLI 访问同一个端点。

## 3. 数据与来源

- `agent_model_connections`（迁移 `0106`）：`id`（`mconn_` 加 32 位十六进制）、`user_id`、`name`、`base_url`、`models`、`api_key_ciphertext`、`credential_revision`、`config_revision`、`enabled`、`model_checks`（按模型 ID 保存的验证结果）、`deleted_at`。同一用户未删除的连接不能重名（不区分大小写）。
- `agent_model_connection_checks`：验证和读取模型列表的次数，按所有者计，用于每小时限额。连接尚不存在时的验证也计入。
- `agent_model_connection_scan_keys`：只保存确实绑定过成员常驻进程的 key 版本，按沙箱组、连接和凭据版本加密保留，供该组后续快照和受信发布做秘密扫描。没用过的连接不写入。
- `agent_jobs.model_connection_id` 和 `agent_pending_messages.model_connection_id`：不建外键。删除连接不能把 turn 的来源改成 NULL。
- `agent_model_connection_residencies(thread_id, residency_generation, connection_id, credential_revision)`：当前常驻进程用哪个连接的哪个版本启动。单独成表，是为了让运行中的上一版本对成员表的预编译 `SELECT m.*` 不因列变化失效。关联必须匹配当前常驻代，所以旧连接的轮换不会误关更新的常驻。

来源规则：

- 模型永远和来源（`model_connection_id`）成对出现。请求没给 `model` 时，模型和来源一起继承上一个 turn。只给连接不给模型是非法请求。
- 同一个模型 ID 在不同连接上是不同的选项，存储时不会混。连接上的模型 ID 按服务商原样保存，不做别名解析。
- 用户自己的几个连接之间，同一个模型 ID 只能在一个连接里。新建或修改时加入的模型已在另一个连接，就返回 409 `model_connection_model_taken`，并指出是哪个模型、在哪个连接。
- 排队消息保存发送时的来源。真正发出时再检查一遍，连接已停用或删除就挂起消息并写明原因。
- goal 自动轮次和原生 Fork 复制上一个 turn 的来源。Fork 只能 Fork 自己的会话。

## 4. 前端

- 选择项始终是「模型 + 来源」二元组。草稿、请求和 API 状态分别保存 `model` 与 `model_connection_id`，只有选择器内部用编码后的键区分同名选项。
- 一个模型只出现在它当前能运行的 Agent 下，依据是它自己的验证结果（`/config` 的 `model_runtimes`），和执行时的检查一致。标签只写模型和连接名，不写协议。
- 草稿或「上次使用」指向的来源现在不能运行（换了 Agent、连接停用或删除、不再列出该模型）时，选择器仍显示它并标注「不可用」，运行按钮禁用并提示原因。它不会退到别的来源的同名模型。
- 后续消息把模型和来源一起与上一个 turn 比较，只换来源也会同时发送 `model` 和 `model_connection_id`。排队消息的编辑只改正文，服务端保留原来源。
- 「立即发送」遇到消息明确选了不同模型或来源的情况时，存储层返回 `model_mismatch`（409），消息留在队列里，作为下一轮运行。没指定模型的消息继承当前 turn，照常可以插入。
- 用量：连接上的 turn 只显示运行时上报的 token，并说明费用由服务商计费。会话合计在含这类 turn 时标为 `incomplete`。

## 5. 凭据生命周期

1. **写入。** key 先用 `AGENT_SOURCE_CONTROL_ENCRYPTION_KEY` 在独立的加密域下加密再入库。接口只返回 `has_api_key`。校验失败的 422 只说哪个字段错了，不回显提交的值，所有响应带 `no-store`。
2. **认领。** 每次认领都按任务所有者重新读连接并调用 `require_usable`。主机没声明 `model-connections-v1` 能力，或部署读不出 key，认领退回队列，不消耗尝试次数。连接已停用、删除或不再适用，任务直接以对应原因失败。认领响应里的 `model_connection` 带 key，runner 把它加入输出脱敏表。
3. **常驻。** 成员进程启动前，控制面再查一次连接，在连接行的锁下把「连接 + 版本」写到成员行上，再把 key 作为这个成员的凭据返回。此刻连接若刚被改过，写入失败，常驻被释放，任务以「连接在启动过程中发生变化」失败。runner 还会核对返回的版本和认领时一致。
4. **进程。** key 只进成员自己的进程环境变量和激活 tmpfs 里的 CLI 配置，不进命令行参数，不进共享工作区。启动参数固定了来源 `连接@版本`，所以连接被轮换后，下一个 turn 不复用旧进程，而是带新 key 原生恢复。换连接同样换进程。
5. **失效。** 经过验证的新 key 或新 Base URL、停用、删除都会提升 `credential_revision`（删除还抹掉密文，只留墓碑）。是否「改了」由仓库在 `UPDATE` 里和库中已存值比较决定：提交的值与原值相同不算改动，重新填入同一个 key 也会被忽略。所以重命名或移除模型不提升版本，不关闭进程，也不清掉其他模型的验证结果。提交后，所有绑定在旧状态上的常驻立即释放，主机关闭对应进程，走的是删除会话时的同一条路径。正在跑的 turn 会被中断。主机联系不上时，下一次常驻心跳被拒绝，进程照样退出。绑定和失效在连接行上互斥：先绑定后失效会被找到并关闭，先失效后绑定会被拒绝。
6. **账号隔离。** 每个用户的会话、成员 UID、私有家目录和 tmpfs 本来就分开。连接查询全部按所有者过滤，别人的连接 ID 一律当作不存在。

## 6. 信任边界

这是用户自己提供的推理凭据，和「平台持有的长期凭据不得进入沙箱」不冲突，没有把任何平台密钥重新归类。它的含义是：

- 仓库代码、依赖和 Agent 本身都能读到这把 key：它在 CLI 的环境变量里，CLI 启动的命令会继承它，CLI 配置文件在成员的 tmpfs 里。它们可以把 key 写进文件、模型上下文，或通过公网出口发出去。平台只做加密存储、只写不读，并在 runner 的持久输出里屏蔽已知值，这些措施防不住有意的外泄。
- 同组其他成员读不到它（不同 UID、私有 tmpfs、不进参数）。但成员共享可写工作区，别人种下的 Git 钩子或构建脚本会以执行者的身份运行，从而读到执行者的 key。
- 受信的快照和发布路径会扫描该沙箱组实际用过的 key，包括同组 Fork 成员、轮换前的版本和已删除连接的已用版本。扫描副本用独立加密域，执行路径不会选用它，但解密后仍是真实的上游 key。副本随沙箱组记录保留，不会把同一用户其他沙箱的 key 送进本组。超过扫描数量上限（200）时拒绝扫描，不静默漏掉旧版本。
- 扫描和脱敏防不住主动外泄，也防不住沙箱内直接 Git 推送。删除连接只阻止平台继续使用它，不能撤销服务商那边的 key。出现泄露时，要在服务商处撤销或轮换。
- 网络边界不变：沙箱只能访问公网 IPv4 的 TCP 80/443，连接的 Base URL 必须是公网可达的 HTTPS（443 端口）。没有私网例外，也没有代理。

## 7. 先验证再保存

「保存」会让控制面去拨用户填的地址，所以沿用 MCP 连接的防护。

**网络防护**

- 只接受 `https`、443 端口、主机名或 IPv4（不接受 IPv6 字面量）。URL 里不能带用户名密码、查询串或片段。
- 解析出的每个地址都要通过 MCP 的公网策略（拒绝回环、私网、链路本地和元数据、CGNAT、组播、保留段、文档段）以及沙箱的受保护网段（含 `AGENT_EGRESS_PROTECTED_CIDRS`），且至少有一个 IPv4。
- 实际连接钉在刚检查过的 IPv4 上，防 DNS 重绑定。不跟随重定向，不读环境里的代理。
- 上游的正文不回显，只保留 HTTP 状态码和从流里解析出的事实。读取有大小上限。
- 每个用户每小时最多 20 次检查（验证一个模型算一次，读取模型列表也算一次）。次数在数据库里原子预留，覆盖多副本并发。一次保存需要的次数一次性全部预留，不够就一个不占并返回 429，不调用服务商。调用期间不持有数据库事务。

**每种 API 的检查**

1. 发一个带一个工具的流式请求，要求模型调用它。第一步必须恰好有一次调用，且调用的是提供的工具。
2. 把工具结果带回去发第二个流式请求，要求模型给出包含工具结果的最终回答。第二步不能再请求任何工具。

检查还要核对调用 ID、工具参数、预期结果和完整的终止事件。Responses 的第二步保留第一步的输出上下文。Anthropic Messages 用原生格式（`x-api-key`、`anthropic-version`、`tool_use`/`tool_result`），第二步原样带回助手内容，包括带签名的 thinking。它的流必须依次有 `message_start`、每个内容块的开始、增量和结束、带正常停止原因的 `message_delta` 和 `message_stop`。`error` 事件、块类型与增量不符、`message_delta` 之后还有内容、`max_tokens` 或 `refusal` 等停止原因、未关闭的块都不算通过（`application/model_connection_anthropic.py`）。

结果分三类：

- `supported`：两步都成功。它只说明这个模型在这个 API 上可用，不代表同一连接上的其他模型。
- `unsupported`：服务商能连上但做不到其中一步（404、400/422、不是流、没调工具、没给最终回答）。
- `failed`：无法判断，如连不上、证书错、重定向、401/403、429、5xx、回答过大。

单纯 `GET /models` 成功不算通过。

**验证就在保存请求里。** 没有凭证（receipt），也没有草稿表。`POST` 和 `PATCH` 自己验证将要提交的配置再提交，所以客户端不能跳过验证、伪造能力或重放旧结果。

1. 校验并规范化输入。`PATCH` 读出当前行，算出真正的变化。
2. 决定验证哪些模型：新建、或改了 Base URL 或 key 时，验证全部要保存的模型。否则只验证新加的模型。重命名、移除模型、启用、停用、无变化时不验证，也不调用服务商。
3. 需要验证时，要求部署开关已开，并一次性预留检查次数。
4. 对每个模型调用 `verify_model`，最多同时四个。每个模型是三种原生检查，每种最多两次请求，用上面的固定解析地址传输。总时限 240 秒，超时返回 504 `model_connection_verification_timeout`，什么都不写。部署自带的边缘代理允许这个时长，额外的代理要另外确认。
5. 成功条件：每个要保存的模型至少有一个 Agent 能用。否则返回 422 `model_connection_verification_failed`，附上每个模型的状态，什么都不写。失败的模型不会被悄悄去掉，也不会换成别的模型。
6. 提交。新建时一条 `INSERT` 写入行、密文和验证结果（版本 1），密文只在验证成功后才生成，失败的新建不会存下 key。名称冲突返回 409。编辑时用一条带条件的 `UPDATE`：当前行必须仍是未删除，且 `credential_revision`、`config_revision`、模型列表和已存验证结果都和验证开始时读到的一致。期间发生的轮换、改地址、停用、删除或模型编辑都会让它落空，返回 409 `model_connection_changed`，原连接、key、结果和常驻进程不变。期间的重命名不冲突，因为更新只写本请求改动的字段。
7. 不需要验证的编辑走原来的更新路径，SQL 里限制为只能改名称、启用状态，以及把模型列表改成原列表的子集。

每个连接最多 16 个模型，换 key 或 Base URL 要重新验证全部。请求里带 `protocols` 会得到 422，没有手动声明 API 的入口。

**按模型生效。** `model_checks` 是一个 JSON 对象：键是模型 ID，值是该模型最近一次验证 `{"revision", "checked_at", "protocols": {<api>: {"status", "category", "http_status"}}}`。结果只在 `revision == config_revision` 时有效。只保存上游的原生事实，能否经转换使用由 `model_paths` 推导，不落库。`require_usable` 只看这个模型自己的结果：一个模型验证通过不会让同连接的其他模型变得可用，新加或改名的模型从未验证状态开始。准入、后续消息、排队消息、goal 轮次、认领、常驻和 Fork 都调用这个函数，所以界面提供的和执行时接受的一致。

**什么变化让什么失效**

| 实际变化 | `credential_revision`（进程复用、释放常驻） | `config_revision` 与 `model_checks` |
|---|---|---|
| Base URL 或 key | +1 | +1，全部清空 |
| 停用 | +1 | 不变 |
| 模型列表 | 不变 | 删掉的模型连同结果一起去掉，新模型没有结果 |
| 名称，或提交了相同的值 | 不变 | 不变 |

验证结果改变了某个模型的路径时，不提升 `credential_revision`：计划本来就是进程复用身份的一部分，下一个 turn 会换进程。认领和常驻之间，由常驻响应里的 `model_connection_plan`（`client>upstream`）和认领比对，路径在这期间变了就让这次启动失败。

## 8. 表单内获取模型 ID

`POST /v1/agent/model-connections/discover-models` 帮表单填模型 ID，不写入任何连接、key 或模型，也不说明列出的模型能用。保存时照常验证选中的模型。

- 请求体是 `base_url`，加 `api_key`（刚填的）或 `connection_id`（已保存的连接）二者之一，都给或都不给是 422。
- `api_key` 只和本次 `base_url` 一起用，绝不换成已保存的 key。`connection_id` 必须是本人未删除的连接（否则 404，不暴露是否存在），且规范化后的 `base_url` 必须等于该连接当前保存的地址，否则返回 422 `model_connection_key_required`，已保存的 key 不会发往改过的地址。
- 路由先校验这些条件，再原子预留一次检查，之后才调用 `discover_models`：同样的网络防护，最多两次请求，先试 OpenAI 风格的 `GET /models`，没有或被拒绝时再用 Anthropic 的认证头试 `GET …/v1/models`。
- 失败只返回类别（`model_connection_<category>`）和固定说明，不回显服务商内容。key 只在 JSON 请求体里，不进查询串、日志或响应。

前端只在用户点击时请求，请求带 `AbortSignal`。Base URL 或 key 改变、发起新请求、开始保存、取消或离开表单都会中止在途请求，迟到的结果按请求序号和内容比对后丢弃。

## 9. 主机侧

- 认领载荷带 `model_connection`，runner 解析后加入脱敏表。
- `ResidentLaunch` 和沙箱管理器的启动配置带 `inference_base_url`、`inference_protocol`、`inference_source`，并参与「能否复用进程」的判断。连接的 Base URL 原样保留。
- 运行时适配器用 `ExactBaseUrl` 区分「原样使用的地址」和需要规范化的地址。Anthropic Messages 连接是 `MessagesBaseUrl`。OpenAI 协议的连接只设 `OPENAI_*`，不设 `ANTHROPIC_*`。Codex 改 provider 的 `base_url`。Claude Code 只接受 `MessagesBaseUrl`，设置 `ANTHROPIC_BASE_URL`（去掉结尾一个 `/v1`）和 `ANTHROPIC_API_KEY`，不设 `ANTHROPIC_AUTH_TOKEN` 和任何 `OPENAI_*`。类型不匹配时适配器直接拒绝。
- `credential` 和 `mcp_credential` 不出现在启动配置的 repr 里。

## 10. 部署顺序

1. 先升级主机（runner 和沙箱管理器）。新主机声明 `model-connections-v1`，旧主机不声明，所以不会认领连接任务。
2. 迁移数据库并升级全部控制面副本。旧控制面不认识来源列，有个人来源数据后新旧控制面混用可能误解来源，所以不能宣称兼容。
3. 有个人连接或来源历史时，不要回退到旧控制面。完成的会话也可能被恢复或 Fork，迁移的 downgrade 会拒绝这些数据。关闭开关只停止新使用，不让历史来源消失。
4. 配置 `AGENT_SOURCE_CONTROL_ENCRYPTION_KEY` 并保留可恢复的备份。
