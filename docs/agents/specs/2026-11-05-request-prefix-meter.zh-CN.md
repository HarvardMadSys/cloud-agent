# 请求前缀计量：测量 system prompt 和工具定义

状态：已实现。

## 问题

Context 面板的 Token Stats 圆环和 Context Browser 里，「系统提示词」和「工具定义」一直显示 not recorded：

- 平台不保存这两样的原文。
- 没有 CLI 在输出里报告它们的大小。
- 平台不再处在模型请求的路径上（[决定](2026-10-30-independent-accounts-only.md)）。

每个请求都带着它们，MCP 工具多时还会占掉大部分上下文，面板却给不出数字。

## 约束

- 不恢复网关层。平台不做 token 额度，配额和计费归模型厂商。
- 不碰对话通道，不向 CLI 的 stdin 问 `/context`（理由见 [context_snapshot.py](../../../apps/backend/cloud_agent_host/runtimes/context_snapshot.py) 的模块说明）。
- 不用「厂商报的输入减平台记录的内容」去推算（`context_request.py` 的规则）。
- 计量只是诊断，不能让请求变慢、失败或被拦下。

## 设计

### 在成员自己的 loopback 服务里测

翻译计划（Codex、Claude Code 跑在 Chat Completions 连接上）本来就在 CLI 前面放了一个成员私有的 loopback 服务（`protocol_translator`），能读到完整请求体。现在它有两种模式：

| 计划 | 模式 | 做什么 |
|---|---|---|
| 翻译 | translated | 翻译请求，同时测量 CLI 发出的请求体 |
| 直连 | passthrough（`model_passthrough.py`） | 原样转发，同时测量 |

passthrough 的要点：

- **绑定一个上游 origin。** launcher 只拿到 `https://<authority>`，没有 key、token 或 model。CLI 的 URL 改成 `http://127.0.0.1:<port>/<authority><原路径>`。服务只接受这个前缀下的路径，把其余部分转发给 origin。
- **主机名留在 URL 里。** pi 这类 CLI 按 URL 文本识别厂商（如 `deepseek.com`），保留 authority 可以让识别结果不变。
- **原样转发。** 请求和响应原样传递，只处理逐跳头、`Host` 和 body 分帧（chunked 请求改为带 Content-Length，无长度的响应改为 chunked）。厂商的错误和错误体原样交给 CLI，CLI 靠它们决定重试和压缩。
- **响应中途断开，CLI 立刻看到。** 厂商声明了长度却没发完就断开时，服务马上关闭和 CLI 的连接，CLI 像直连一样报错重试，不会等到超时。
- **重定向。** 3xx 交给 CLI 处理。`Location` 是 origin 上的路径时，补回 `/<authority>` 前缀，否则会解析到 loopback 根路径变成 404。完整 URL 和相对引用原样传递。
- **复用上游连接。** 每个 CLI 连接保持自己的一条上游连接。空闲时被厂商关掉的连接，在使用前检出并替换。
- **先转发，后测量。** 请求体发出后才解析，正好和厂商处理时间重叠。解析失败或 body 超过 16 MiB 时不测，照常转发。
- **启动失败不挡 turn。** 服务建好 server 后通过管道通知 launcher 就绪，launcher 才把 CLI 指向 loopback。服务起不来（bind 失败、子进程初始化失败、10 秒内未就绪）时，launcher 把占位符换回原始 URL，CLI 直连厂商。翻译模式无法直连，此时启动直接报错。

### 哪些直连不计量

Claude Code 连 `api.anthropic.com` 时不计量。它按 base URL 的主机名判断是不是官方 API，比如非官方主机默认关闭 MCP tool search，所以改 URL 会改变它的行为。规则在 `runtimes/base.py` 的 `FIRST_PARTY_HOSTS`。其他组合本来就是非官方主机，或靠 URL 文本识别厂商（路径里保留 authority），所以照常计量。

### 测什么：`cloud_agent_shared/request_prefix.py`

每个请求只记大小，不记原文：

- **system prompt**：协议规定位置的内容，对话里出现的不算。
  - Anthropic：`system`。
  - Responses：`instructions`，加上 `input` 开头连续的 system/developer 消息。
  - Chat Completions：`messages` 开头连续的 system/developer 消息。
- **工具定义**：`tools` 的每一项，按发送时的紧凑 JSON 计。
- 每部分记字符数，以及 ASCII 和 CJK 字符数（`chars_cjk_v1` 估算的输入）。每个工具单独记名字和大小，只保留最大的 200 个，其余只计数。
- 前缀指纹：对 system 和 tools 原值取 sha256。

同一个服务进程里，指纹第一次出现时写完整记录，之后只写 `{"fp", "repeat": true}`。

写 stderr 不能卡住请求：请求线程只把记录放进有界队列，由单独的线程写出。队列满（比如 stderr 没人读）就丢掉这条记录，同时让计量器忘掉这个指纹，下次重新写完整记录，免得 host 收到无法还原的「重复」。

### 怎么报出来

记录写在 CLI 的 stderr 上，行首是 `\x1emodel-request-prefix `。stdout 被 CLI 协议占用，成员进程又由 `docker exec` 启动，只有标准输入输出可用。

- **host 端拆分**（`request_prefixes.PrefixRecords`，由 `SandboxProcess.sieve_stderr` 调用）：把记录从诊断用的 stderr 尾部取出，免得挤掉真正的报错。每条记录逐项校验并有大小上限。重复行按指纹还原成完整记录。
- **按 turn 上报**（`TurnPrefixes`，在 `ResidentTurn.lines()` 里）：每个 turn 对每种前缀发一次 `lifecycle` 事件，`phase: "request_prefix"`。这样每个 attempt 都知道自己的请求带了什么，不论 CLI 多久之前第一次发出这个前缀。
- 记录是 CLI 进程 stderr 上的文字，CLI 或同组成员都能伪造，所以只用来给 owner 展示，平台不据此做任何决定。

### 控制面怎么用

- **profile reducer**（`context_profile.py`，版本 6）：收集每个 attempt 里不同的前缀，最多 20 个，记 system 和 tools 的估算 token 数。`main_prefix()` 选第一个带工具的前缀：turn 总是先由主 agent 回答 prompt，生成标题、摘要这类旁路请求不带工具，子 agent 的请求在后面。
- **Token Stats**（`context_dashboard.session_tokens`）：取最近一个测到主前缀的 turn，把 `system_prompt` 和 `tool_schemas` 放进 `recorded`。没测到的分类留在 `not_recorded`。
- **Context Browser**（`RequestComposition.prefix`）：显示该请求所在 attempt 的主前缀，工具可展开并按大小排列。前端组件是 `PrefixRows.tsx`。

### 兼容与上线

- host 先于控制面上线。旧控制面不校验生命周期事件的 phase，照常存储；前端隐藏不认识的 phase。
- 计量 helper 和翻译 helper 是同一个 zipapp，每个容器只装一次（`install_once`，key 带内容 digest），直连成员启动不多一次 exec。
- 旧会话在新 host 上的第一个 turn 之前仍显示 not recorded。

## 没有做的

- **计时**（TTFT、生成时间）：passthrough 已在路径上，但接入 Timing Stats 需要按请求关联，留待后续。
- **精确到每个请求**：Claude Code 的 `usage_id` 能对上响应里的 message id，目前只按 attempt 选主前缀。
- **开关**：没有 host 设置项。计量服务启动失败会自动直连，系统性问题就回滚 host 版本。
