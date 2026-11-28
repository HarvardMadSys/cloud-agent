# 个人模型连接的协议兼容层（Chat Completions 上游）

状态：已实现。用固定版本的真实 CLI 和本地假上游验证过，没有做真实服务商和 Kata 主机的端到端验收。

## 1. 问题与决定

连接原本按「运行时说什么协议，模型就要有什么协议」匹配，所以只提供 Chat Completions 的服务商（自建 vLLM、各类 OpenAI 兼容服务）只能给 OpenCode、Kilo、pi 用。用户也想用 Codex 和 Claude Code。

决定：在成员自己的沙箱进程里放一个私有的协议转换层，只做两条路径。

| 运行时 | 客户端协议 | 模型只有 `chat_completions` 时 | 模型的原生协议可用时 |
|---|---|---|---|
| OpenCode、Kilo、pi | Chat Completions | 原生直连 | 原生直连 |
| Codex | Responses | 转换：Responses → Chat Completions | `responses` 直连 |
| Claude Code | Anthropic Messages | 转换：Messages → Chat Completions | `anthropic_messages` 直连 |

这不是任意协议之间的 N×N 转换，也不声称上游具备 Responses 或 Messages 的全部能力。不做公开推理服务、通用转发代理、模型或服务商回退、路由池和计费。连接本身见[模型连接设计](2026-10-27-user-model-connections.zh-CN.md)。

## 2. 计划：一个函数决定怎么连

`cloud_agent_shared/model_connections.protocol_plan(runtime, protocols)` 返回 `ProtocolPlan(client_protocol, upstream_protocol)`，`translated` 表示是否转换。`protocols` 是这个模型在当前配置版本下验证为 `supported` 的原生协议。

1. 运行时的客户端协议在其中，直连。
2. 否则，客户端是 Responses 或 Anthropic Messages，且其中有 `chat_completions`，转换。
3. 否则不可用：`protocol_unsupported`，没有当前验证结果时是 `model_unverified`。

原生优先：一个模型的 `responses` 和 `chat_completions` 都可用时，Codex 直连 Responses。同一连接上的不同模型可以各自直连或转换。

保存的验证结果始终描述上游真实提供的协议。转换可用性由它推导，不单独保存，也不会冒充原生。准入、后续消息、排队消息、goal 轮次、认领、常驻凭据、原生 Fork、`/config` 和设置页都经 `domain.require_usable` → `model_paths` → `protocol_plan` 得到同一个计划。

计划随认领载荷交给主机（`ModelConnectionAccess.client_protocol`，直连时没有这个字段），进入启动字段 `inference_client_protocol` 和成员的本地令牌 `inference_local_token`，并参与「能否复用进程」的判断。验证结果变化会改变计划，常驻进程随之重启。常驻响应还带回 `model_connection_plan`（`client>upstream`），主机要求它和认领时一致，否则这次启动失败，不会用认领时的启动参数跑另一条路径。

主机能力：新主机声明 `model-protocol-translation-v1`。转换计划的 turn 只交给声明了它的主机。只声明 `model-connections-v1` 的主机拿到这样的认领会退回，不消耗尝试次数。

## 3. 转换代码：从 HybridInference 抽取的纯协议内核

纯转换代码在 `apps/backend/cloud_agent_shared/protocol_compat/`，只依赖 Python 标准库（沙箱里是 Debian bookworm 的 Python 3.11），不做 I/O，控制面和沙箱用同一份。沙箱里的启动器和本地服务（套接字、线程、HTTPS）是 `apps/backend/cloud_agent_host/protocol_translator.py`，只导入标准库和这个纯包。

- **来源：** `HarvardMadSys/hybridInference` 提交 `df40a9a82a6d5a39a9ffbdfebde3650f94cb5146`，MIT 许可。许可证原文在包内 `LICENSE.hybridinference`，`NOTICE.md` 列出逐文件对应关系和改动。
- **抽取了：** `serving/responses_translator.py`（Responses ↔ Chat）、`serving/adapters/anthropic_translator.py`（Messages ↔ Chat）、`serving/utils/messages.py`、`serving/utils/token_utils.py`。为满足本仓库的复杂度上限，把几个函数的循环体拆成了辅助函数，行为不变。去掉了 Gateway 计费用的 `extract_anthropic_usage_from_sse`。上游的 106 个回归测试迁入 `tests/unit/protocol_compat/test_*_upstream.py`。Gateway 的路由、鉴权、数据库、路由池和计费都不引入。
- **维护：** 这是一次性复制，不是共享依赖。上游修复按 `NOTICE.md` 记录的提交号人工比对后合入。
- **本仓库新写的：** `gate.py`（请求校验：不支持的内容明确拒绝，不悄悄丢弃）、`namespaces.py`（命名空间工具的扁平化与还原）、`stream.py`（上游流完整性守卫）、`protocol_translator.py` 和 `compat_helper.py`（打包与安装）。

### 支持的子集

转换是无状态的，每次整段重放历史。

**Responses → Chat（Codex）**

- 支持：`instructions`；`input` 中的 `message`（`input_text`、`output_text`、`input_image`）、`function_call`、`function_call_output`；`function` 工具，以及 `namespace` 工具里的 function 工具（Codex 0.145.0 的子 Agent 工具 `multi_agent_v1` 和平台转发的 MCP 工具 `mcp__session`）；`tool_choice`（含指向命名空间工具的强制调用）、`text.format`、`max_output_tokens`、`temperature`、`top_p`、`parallel_tool_calls`；流式与非流式。
- 命名空间工具在上游用扁平名 `<命名空间>__<名称>`。名字含非法字符、过长或与其他工具重名时追加摘要后缀，加了后缀仍冲突就拒绝。返回的流事件和响应里的调用还原成原来的 `name` 加 `namespace`，历史里的调用按同一规则映射。
- 拒绝（400，说明原因）：`previous_response_id`、`conversation`、`background`、`prompt` 模板；非 function 工具（`web_search`、`custom`、`local_shell`、`file_search` 等，包括命名空间里的）；`input_file`、`refusal`、音频；未知输入项（如 `item_reference`、`custom_tool_call`）。
- 不转发但如实说明：`reasoning` 输入项（加密的推理内容，Chat 没有对应字段）和 `reasoning`、`include`、`store`、`client_metadata`、`prompt_cache_key` 等元数据。输出里不产生推理项。
- 转换模式下 Codex 带 `web_search="disabled"` 启动：0.145.0 默认请求带托管的 `web_search` 工具，Chat 上游无法提供。子 Agent 等其他功能不关闭。

**Messages → Chat（Claude Code）**

- 支持：`system`（文本块）；`text`、`image`、`tool_use`、`tool_result` 内容块；无类型的自定义工具；`tool_choice`（含 `none`）、`max_tokens`、`temperature`、`top_p`、`stop_sequences`；流式与非流式。
- 拒绝：服务端工具（如 `web_search_20250305`）、`document`、`server_tool_use`、各类服务端工具结果、工具结果里的 `search_result`、`mcp_servers`、`container`。
- 不转发但如实说明：历史中的 `thinking` 和 `redacted_thinking` 块（签名只对原服务商有效）、`thinking`、`context_management`、`output_config`、`metadata`（含设备标识，不发给服务商）。上游的 `reasoning_content` 以无签名的 thinking 块返回给 CLI。推理强度见[上游声明的推理强度](2026-11-05-upstream-reasoning-effort.md)。
- 转换模式下 Claude Code 带 `--disallowedTools WebSearch` 启动，因为它依赖 Anthropic 的服务端工具。
- `count_tokens`、`/v1/models`、`HEAD /api/hello` 返回 404，CLI 允许它们缺失。

**工具返回的图片。** Chat Completions 的 `role: "tool"` 消息只能放文本，但 Claude Code 读截图、Codex 的 `view_image` 都会在工具结果里返回图片。图片不会被替换成占位文字：工具消息保留文本并注明图片附在后面，图片按原顺序、注明来自哪个调用，以 `image_url` 内容放进这组工具结果之后的一条用户消息。并行的多个工具结果仍然相邻、完整。两种协议用同一个映射（`protocol_compat/tool_images.py`）。不支持图片的上游会返回自己的错误，CLI 如实报告，不会在模型没看到图片时继续。

**模型锁定。** 请求里的 `model` 必须等于启动时绑定的模型，否则 400，不改写成别的模型。

**上游流完整性。** 只有 0 号 choice 以 Chat Completions 定义的 `finish_reason` 结束、且之后没有出错，才转成客户端的正常结束。下面这些一律转成客户端格式的失败（Responses 的 `response.failed` 加 `error`，Messages 的 `error` 事件），绝不当成成功：`error` 载荷、非对象的 choice 或 delta、非 0 号 choice、未知或非字符串的 `finish_reason`、形状不对的 `tool_calls` 或 `content`、读到一半断开或解析失败（即使已出现过 `finish_reason`）、没有结束就断流。读到 `[DONE]` 立即停止并关闭上游请求，不等上游关连接。

## 4. 进程与生命周期

```
setsid（已有包装） ──> python3 -I model-protocol-translator.pyz <CLI argv>   （常驻进程 PID 不变）
                         ├─ 绑定 127.0.0.1:随机端口，监听
                         ├─ fork：子进程 = 本地兼容服务（同一成员 UID、同一进程组）
                         │      PR_SET_PDEATHSIG + 每秒检查父进程，CLI 退出它就退出
                         └─ exec CLI：占位符换成 http://127.0.0.1:<端口> 和成员令牌，
                                       删掉全部 AGENT_COMPAT_* 变量（包括上游 key）
```

- 启动器在常驻 CLI 前面，以同一个成员 UID 运行。`exec` 之后 pidfile 里的 PID 就是 CLI 自己，所以进程组终止、成员进程回收（`member-process-reaper` 按 UID 清理）和容器停止都会带走兼容服务子进程。没有单独的守护进程，也没有跨 turn 存活的孤儿监听。
- Claude Code 驱动按 `--model` 的位置固定模型，启动器前缀不影响它。启动器也从这个参数取锁定的模型。
- 能否复用常驻进程由启动字段决定（含 `inference_client_protocol`、`inference_local_token`、`inference_source`）。停用、删除、轮换、被取代、取消和回收时 CLI 被关闭，兼容服务随之退出。原生 Fork 给源成员和新成员各发一份启动字段，各带自己的令牌，新成员有自己的兼容服务。
- 取消：请求一发出，就有一个线程盯着 CLI 一侧的连接。CLI 关闭请求（turn 取消）时，立刻关闭上游套接字，不管上游是还没返回响应头、正在分块输出，还是以关闭连接结束。
- 保留容器的更新：每次启动转换模式成员前，主机把由本仓库源码确定性打包的 zipapp 按 SHA-256 校验后，以 root 拥有、0444 权限装到 `/usr/local/libexec/model-protocol-translator.pyz`（与 MCP 适配器共用 `trusted_files.install_trusted_file`），不依赖重建镜像。zipapp 只含 `cloud_agent_host/protocol_translator.py`、`cloud_agent_shared/protocol_compat/`、两个包的 `__init__.py`、许可证和 NOTICE。

## 5. 凭据与网络

- 上游 key 仍加密保存、只写、按所有者读取，认领和常驻时交给主机，runner 在持久输出里屏蔽，扫描副本按沙箱组记录保存。转换模式下 key 只交给启动器（环境变量，不进 argv），`exec` CLI 前从环境删掉。CLI 拿到的是本地令牌。
- 本地令牌由控制面签发。`translator_token` 是连接 key 所用 `CredentialCipher` 的平台 MAC（HMAC-SHA256，密钥由 `USER_CREDENTIAL_ENCRYPTION_KEY` 按域派生，从不下发）对 `"cloud-agent/model-protocol-translation/token/v1" ‖ 所有者 ‖ 成员线程 ID ‖ <连接>@<版本> ‖ 客户端协议 ‖ 模型`（以 0 字节分隔）的十六进制。
  - 它不是上游 key 的函数：同组里用同一个 key 的 Fork 兄弟成员，知道 key 和对方的线程 ID 也算不出对方的令牌。
  - 令牌随转换模式的认领（`ModelConnectionAccess.local_token`）和 Fork 的每个成员启动字段下发，经 `SharedResidentLaunchConfig.inference_local_token` 到主机，再经 `AGENT_COMPAT_TOKEN` 交给启动器。这些字段都不进 repr。
  - 同一个成员进程的每次认领和 Fork 算出同一个令牌。runner 把令牌和 key 一起加入脱敏表。令牌是进程复用身份的一部分，所以平台密钥轮换或任何输入变化都会重启进程。不新增持久化。
  - 令牌只由启动器写进 CLI 的环境变量，argv 里出现令牌占位符会拒绝启动。
- 兼容服务只认 `Authorization: Bearer <令牌>` 或 `x-api-key: <令牌>`（常量时间比较），其他请求一律 401。它只服务客户端自己的那一个路径，没有健康检查或配置查询端点。
- 只转发到启动时绑定的 `<Base URL>/chat/completions`：HTTPS、系统证书校验、不读环境代理、不跟随重定向，调用方不能指定地址或 key。沙箱原有的 nftables 出站规则不变，兼容服务和 CLI 走同一条出站路径，不需要额外的主机网络权限，也没有控制面代理。测试用的假上游只能由测试代码在进程内替换连接工厂（`protocol_translator.UPSTREAM_CONNECTION`），没有环境变量或启动参数可配。
- 上游错误只把状态码映射成客户端格式的错误，不回显上游正文。日志只写类别，不写正文、key 或令牌。

## 6. 验证与保存

设置页不让用户选择或声明 API。用户点保存时，控制面对每个要保存的模型在三种 API 上各跑一次原生的两步工具调用检查，只有每个模型都有 Agent 能用才保存。哪些 Agent 经转换可用，由这些原生事实推导。转换层本身在验证时不探测，它靠单元测试和固定版本真实 CLI 的外部测试（`tests/external/test_model_protocol_compat_cli.py`，Codex 0.145.0 和 Claude Code 2.1.220）证明。
