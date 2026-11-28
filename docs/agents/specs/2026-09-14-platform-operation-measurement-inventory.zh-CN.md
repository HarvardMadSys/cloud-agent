# 平台操作测量清单

状态：沙盒启动、文件浏览与打开、终端打开三类操作的服务端采集、持久化、Admin API 与 UI 已实现。浏览器终点未实现，因此只能声明 server 覆盖，不能回答"用户等了多久"。

在按需开启的 profiling 实验中，每个主要用户操作都应回答：多久可用、是否成功、卡在哪一步、结果是否新鲜。执行中的 CPU、内存和 I/O 继续由 member-window 与 eBPF 体系解释，平台操作的等待由前端和服务端阶段计时解释。每种测量都保留来源、范围和覆盖率。

启动测量的指标、路径、数据模型和 benchmark 方法在[沙盒启动时间测量设计](2026-09-14-sandbox-startup-measurement-design.zh-CN.md)，本文给出共用的开关、操作模型和另外两类操作的契约。新增的平台 profiling 默认关闭，服务于性能定位、基线和优化对比，不是常驻采集。

| 能力 | 已实现 | 尚缺 |
| --- | --- | --- |
| `attempt.startup` | runner claim 前到 prompt 交给 supervisor，四条路径、内部阶段、完整性协议 | CP 排队、浏览器等待、模型首输出；真实四路径线上基线 |
| `files.list`、`files.open` | CP 选择、sandbox manager 阶段、响应片段透传 | 浏览器起点与内容可见终点；纯缓存命中的覆盖 |
| `terminal.open` | list、create、stream 共用操作身份，PTY 阶段，流式响应片段经响应头回传 | xterm 可交互终点、buffer replay 与新输出的区分、首输出观察窗口 |
| 共享采集器 | 默认关闭、run 与期限、单进程预算、call ID、缺失与截断、有界异步导出、schema v3 | 前端适配器、跨实例的名额分配 |
| 展示 | 结构化日志、可选 JSONL、Postgres、Admin API 与 UI | 三类线上基线与浏览器端到端报告 |

## 0. 可选 profiling 开关

默认关闭。关闭时走原业务路径，不初始化 profiler、分配 measurement ID、取计时值、构造 span、序列化或上报 profiling 数据。开启后只对被选中的目标和操作计时。

控制来源是 Admin → Measurements（默认，没有活动实验即不采集，见[Measurement 默认可控制](2026-09-22-measurement-admin-default.zh-CN.md)），或部署时的静态实验（`AGENT_OPERATION_PROFILING_ENABLED` 与 `AGENT_OPERATION_PROFILING_RUN`，细节见 [ENVIRONMENT.md](../../../ENVIRONMENT.md)）。静态实验的 run descriptor 要有明确的范围、绝对截止时间和上限，不合法时 profiling 不生效，只记一条 warning。示意：

```yaml
run_id: workspace-interaction-baseline-01
target: {sandbox_id: "<专用测试沙盒>"}
operations: [files.list, files.open, terminal.open]
expires_at: "<固定的 UTC 截止时间>"
max_operations: 200
```

- 可按测试 session、thread、sandbox 或指定 host 限定目标，host 级实验也必须显式选择。`operations` 选根操作，必要子阶段自动继承。
- 截止时间是 run 的固定属性，重启、重试和刷新页面都不能续期。到达样本上限后不接受新根操作，已选中的可在原截止时间内完成。到期或手动关闭时，未完成的采集在有限的收尾期限内结束并标 partial。
- 名额约束被选中的根操作，子 span 另有数量和字节上限。同一次 interaction 的重试共享选择结果并报告物理请求次数。采样决定发生在根入口，不先完整采集再丢弃。目前名额是单进程预算，不是 fleet 级上限。

关闭时的成本约束：

- 入口只做廉价的关闭判断。不能每个 span 都解析环境变量、查 DB、读配置，或分配 context manager 后再判断。
- 不启动 exporter 线程、周期任务或 timer，不执行额外的 Docker 或 guest 命令，不生成 profiling 的 HTTP 请求、日志或 DB 行。前端关闭时不加载 profiler 模块，不注册专用的 PerformanceObserver、监听器、timer 或 beacon。
- 浏览器传来的开关或 header 不能直接让 CP 或 sandbox manager 开始采集，服务端独立校验 run 与范围。服务间透传已授权的选择结果，各自再校验有效期与本地开关。任何 producer 未开启都记为局部覆盖，不显示完整端到端 trace。
- 关闭只承诺新增 profiling 路径的开销接近零。现有 eBPF、member resource sampler、host capacity、heartbeat、审计和生命周期记录有各自的职责，不由此开关关闭。已有 resource 采集的启用状态与频率写入实验 manifest，baseline 与 candidate 保持一致。

实验数据的含义：区分 `not_enabled`、`not_selected` 和选中后的 partial 或 failed。前两种不是采集故障，不为每个未选中的请求写空记录。覆盖率的分母是 run 中被选中的操作，选择率另报，实验 cohort 的失败率不冒充全站失败率。实验结束后历史报告仍可查看，并标注 run 起止、范围和版本。

自身开销也要测：(1) 无 profiling 代码的基线与新代码 `off`，检查关闭路径回归。(2) 同版本的 `off` 与 `profiling`，同时看延迟、吞吐、CPU、内存和请求或命令数量。(3) 优化前后两个版本都处于 `off` 的完整操作 benchmark，作为实际收益的主要证据。不把测试噪声下"未观察到差异"说成绝对零开销，报告样本量和不确定性。

## 1. 优先覆盖的操作

这是实施优先级，不代表已建立 SLO 或发现了对应的性能问题。

| 优先级 | 操作 | 用户侧起点 → 完成条件 | 需单独展开的阶段 |
| --- | --- | --- | --- |
| P0 | 新建、唤醒沙盒 | 发起需要 workspace 的操作 → 所需能力可用 | 调度、reservation、group 与锁等待、create 与 start、网络、workspace、setup；用 trigger 区分 task、files、terminal、fork |
| P0 | 打开、新建终端 | 点击 → 终端流连接且输入可用，首输出另记 | list、create、reattach、workspace wake、PTY spawn、xterm 加载、流连接、渲染 |
| P0 | 打开 Files、展开目录 | 点击 → 对应目录条目或确认的空目录可见 | 缓存、授权、sandbox manager、wake、网络检查、exec、遍历、渲染 |
| P0 | 打开文件 | 点击 → 文件内容可见 | 同上，加读取、传输、文本处理与高亮；记录大小区间与是否截断 |
| P0 | 提交第一轮、后续轮 | 点击发送 → 请求被接受，首活动与首文本分别记 | 排队、sandbox、resident 新建、恢复、复用、admission、dispatch、事件送达与渲染 |
| P0 | 打开、切换会话 | 点击 → 最新消息可见且 composer 状态确定 | metadata、历史事件加载与解析、渲染、live stream 接入；历史首屏与追到当前分开 |
| P0 | 停止任务 | 点击停止 → 请求被接受，实际停止另记 | CP cancel、通知送达、CLI interrupt、退出与 turn boundary、settle、UI 更新 |
| P1 | 保存文件 | 点击保存 → 服务端确认且 UI 一致 | 写 admission、wake、exec、响应、refresh。保存确认不叫 fsync durability |
| P1 | 打开 Changes、Git diff | 打开 → diff 可查看 | git status、diff、log、序列化、传输、解析与渲染；区分变更文件数与总字节 |
| P1 | workspace 变化可见 | 变更完成或 refresh 触发 → 展示对应版本 | refresh 调度、debounce、缓存失效、渲染 |
| P1 | 终端持续交互 | input 提交 → PTY write；服务端输出 → 浏览器处理 | batching、HTTP、队列与背压、PTY、xterm。按键到回显只在固定测试终端中测 |
| P1 | 断线重连 | 确认断线 → 重连并追到指定 cursor | backoff、auth、buffer replay、实时恢复，terminal 与 agent stream 分开 |
| P1 | Fork | 点击 → child 会话可用 | 幂等 reservation、group 与 member 等待、native fork、child CLI 初始化、finalize、导航 |
| P1 | 结果发布 | 达到可发布边界 → PR URL 可见 | snapshot 队列、writer freeze、capture、thaw、publish 队列、Git push、平台 API |
| P2 | 登录与首屏、项目与仓库选择、MCP 连接与调用、host 接活与回收、Admin 数据刷新 | 各自的起点与可用终点 | 按实际流程拆分 |

只有产品真实存在的操作才建主指标。若将来上线 Web app preview，要为启动服务、路由可达和首屏分别定义，不与文件预览共用一个含义模糊的指标。

## 2. 值得注意的共享开销

**终端。** 点击路径除了 server create，还有终端列表、动态加载 xterm、PTY 流连接和浏览器绘制，xterm 的模块预加载使前端 cache hit 与 miss 必须区分。终端 API 返回 terminal ID、stream 的 HTTP 200、首个输出字节、shell 可接收命令，是不同的 milestone。首字节可能是 banner，shell 也可能不输出 prompt，不用匹配 `$` 或 `#` 判断 ready。生产先记可证实的边界：PTY-created、stream-connected、input-enabled、first-output。shell-ready 没有可靠 integration 就留空，受控 benchmark 用固定 shell 与无副作用 marker 验证，不向用户终端插入测试命令。input 的 HTTP 确认只证明写入链路完成，不证明已回显。

**文件。** 文件 API 做 ownership、repo entitlement 与 sandbox manager 解析，sandbox manager 打开 workspace operation、验证网络，再在 guest 内执行固定的 Python helper。读取的 wall time 含容器 exec 与解释器启动，API 总耗时不能叫磁盘 read latency。前端缓存已展开的目录，refresh 重读根目录、已展开目录和当前文件。目录 cache hit、手动 refresh 和点击文件是不同操作，快速切换时丢弃的旧请求是 superseded，不报服务失败或零耗时成功。

**沙盒被其他操作唤醒。** supervisor 的 `operation_opened()` 和 `terminal_opened()` 都会确保容器运行，所以点文件和点终端也可能触发唤醒，而不产生新的 agent attempt，attempt-only 的测量覆盖不全。例如一次打开文件用 3 秒，其中唤醒占 2 秒，文件的用户总等待仍记 3 秒，子阶段说明其中的唤醒成本，不把 2 秒从用户体验指标中扣掉。多个操作共享一次 wake 时，创建一个 wake operation，各自记录等待并 link 到它，资源消耗与执行次数不复制给每个等待者。

## 3. 统一的 operation 模型

操作身份独立于 agent attempt：

```text
interaction_id: 一次用户意图，可含多次请求或重试
operation: 操作类型，如 files.open、terminal.open、sandbox.wake
operation_id: 一次平台操作的唯一 ID；复用现有 measurement_id
parent_operation_id / links: 子步骤或共同等待的共享操作
attempt_id?, thread_id?, sandbox_id?, terminal_id?: 按实际作用域关联
origin: browser | API | background
trigger: user | task | files | terminal | fork | idle | deployment
profiling_run_id, producer, clock_domain_id, schema_version
milestones, spans, outcome, missing_reason, retries
```

浏览器生成的 correlation 只用于追踪，身份、权限和资源归属仍由服务端从认证与存储校验。operation 记录不赋予读写或执行权限。终端和平台文件操作的资源不能自动计入某个 agent member 的 turn，eBPF 与 resource 测量用 link 关联。公共 span 名称包括 frontend-load、auth、DB-pool-wait、sandbox-manager-RPC、group 与 lock-wait、sandbox-wake、exec、operation-work、serialization、render，按实际执行选用。

每个操作契约写明：负责代码、起点、成功终点、失败与取消终点、时钟、包含的等待、分类维度、数据上限和验收场景。新增主要交互时把契约当作设计验收项。

### 3.1 三类操作共用机制，终点分别定义

| 操作 | 根操作与起点 | 成功终点 | 必须区分的路径 |
| --- | --- | --- | --- |
| `attempt.startup` | runner 在 claim 前创建 measurement | prompt 已交给 supervisor，不等于模型接受或首输出 | `new_group`、`resume_stopped`、`resume_running`、`reuse_live` |
| `files.list` | 浏览器点击 Files 或展开目录；纯 API 请求从 CP 入口开始 | 请求的目录条目或确认的空目录提交到可见 UI，服务端另记响应完成 | root、expand、refresh；cache hit 与 miss；running 与 stopped |
| `files.open` | 浏览器选中文件；纯 API 从 CP 入口开始 | 当前仍选中的文件内容提交到可见 UI，服务端另记响应完成 | 文件大小桶、预览截断、running 与 stopped、请求替换 |
| `terminal.open` | 浏览器打开终端面板、创建或选择终端；纯 API 独立记录 | xterm 已挂载、对应流已连接、平台输入门已打开，三者都成立 | create 与 reattach、模块冷热、buffer replay、running 与 stopped |

浏览器终点记具体来源：React commit 或 xterm 回调是可见性的代理边界，不称为物理绘制完成。自动 refresh 标为 background，用户手动 refresh 才计用户等待。终端可交互不等于 shell 已跑完 rc 文件。

公共记录用 `operation`、`outcome`、`failure_stage`，`startup_path` 等只属于启动的 facts。不得把文件返回成功写成 `startup_outcome=dispatched`。当前是 schema v3：`outcome`、`producer`、`producers`、`timing_sources`、边界、coverage 与完整性，`startup_outcome` 仅作为 `attempt.startup` 的别名保留。CP 与 sandbox manager 是 producer，浏览器 producer 未实现，所以浏览器片段按缺失计。

### 3.2 文件与终端的选择、关联与导出

文件和终端入口走 CP 与 sandbox manager，可能完全没有当前 attempt。`job_id` 用于验证工作区归属，不把该操作归入这个 job 的 agent 启动或资源窗口。

1. CP 在已有业务请求中做选择：先完成认证、ownership、entitlement 与 sandbox manager 或 host 的解析，再确认目标匹配。不为 profiling 增加权限豁免或探测命令。先做开关、操作类型的廉价预过滤，身份与范围确认后才消耗名额，后置绑定不匹配就丢弃候选并归还名额。
2. CP 为选中的操作返回 measurement ID 和有期限的选择引用。一次 `terminal.open` 可含 list、create、stream 多个请求，后续请求继承同一选择，每次 RPC 有独立 call ID。浏览器 ID 只用于关联，不能自行开启测量或声明 host、用户、sandbox 归属。选择缓存是单进程、有数量与 TTL 上限的，并受 run 截止时间约束。重试不重新抽样。跨实例无法恢复原选择时，业务照常，片段标不可用，不用一次新选择伪装连续测量。
3. sandbox manager 校验本地开关、run、操作和继承的期限，取本地与继承期限中较早者。files 与 terminals 路由都支持可选上下文，无效元数据只停用采集，业务权限校验不变。
4. 响应复用启动设计里的片段协议（call ID 回声、`profiling_recorded`、`profiling_truncated`、`stages`）。GET 文件和 SSE 流的片段走可选 metadata 或响应头，不改文件内容或终端字节，不把 PTY 原始输出当测量数据。关闭或未选中时，响应体与响应头和接入前逐字节一致。CP 的 response model 与客户端要显式透传 sandbox manager 的片段，避免被过滤。
5. 浏览器和 CP 各自输出本地时长，用 measurement 与 interaction ID 关联，不把浏览器的 `performance.now` 与服务器单调时钟相减。缺浏览器样本时服务端耗时仍可用，用户端到端覆盖标不完整，纯 API 客户端只要求其声明的 server 范围。
6. 前端采集仅在开启且获准的实验中使用有界异步批次。所有片段有数目、字节和期限上限，上报失败只影响覆盖，不阻塞打开文件或终端输入。

目录纯缓存命中没有 CP 请求，当前没有 server 片段，必须按缺失报告。设计上由前端从可信配置预领一小批有范围、有绝对截止的名额，耗尽即停，后来若发生 HTTP 请求，CP 复用同一名额，不再扣一份。本地命中只要求 browser producer。该方案未实现，前端接入前不能声明覆盖 cache hit。

### 3.3 文件接入

调用顺序：browser → CP → sandbox manager → `supervisor.operation_opened()` → 网络验证 → retained 文件 helper → CP 响应 → browser UI。`operation_opened` 在 sandbox manager 内部，可能唤醒容器，不经过 `runner.run_once()`。

| 位置 | 记录的边界 |
| --- | --- |
| 前端文件 hook 与组件 | 点击、目录缓存命中、request generation、响应接收、对应内容的 UI commit |
| CP 文件路由 | 认证、归属与 entitlement、sandbox manager 解析、RPC、响应构造 |
| sandbox manager 的 files | 上下文与片段透传、workspace admission、`operation_opened`、group 等待与 wake、网络检查（阶段 `admission`、`network_phase`、`exec`） |
| `retained_workspace` | exec 总耗时、helper 执行、读取或遍历，返回字节与条目数的大小桶 |

docker exec、Python 启动与 read 作为一个组合阶段，不称纯磁盘读取。要再拆，让固定 helper 在选中时返回自己计的子时长，不增加第二次 exec。路径与内容不进入 profiling 记录。

终点处理：目录缓存命中也记用户可见时间；请求被较新选择替代记 `superseded`；响应晚到且未被 UI 使用不算成功可见；页面离开记 `abandoned` 或 unknown；错误、超时与空文件分别处理；请求发现目标其实是目录时，记录实际返回的 kind 与 UI 结果。预览截断（`content_truncated`）与计时截断（`measurement.truncated`）用不同字段。

### 3.4 终端接入

一次 `terminal.open` 从用户意图开始，包含终端列表和前置的 workspace-ready 等待。HTTP 入口是 `/jobs/{job_id}/terminals` 与 `/{terminal_id}/stream`，以及对应的 sandbox manager 路由。

| 位置 | 记录的边界 |
| --- | --- |
| 前端 TerminalWorkspace 与 terminalRuntime | 点击、前置 ready 等待、list、create、reattach、模块加载冷热、选中的 terminal ID |
| CP terminal API 与 sandbox manager 客户端 | ownership、entitlement、sandbox manager 解析、每次 list、create、stream 请求与重试 |
| sandbox manager 的 create_terminal 与 `terminal_opened` | admission、锁等待、wake、网络检查、PTY spawn、descriptor 返回（阶段 `admission`、`network_phase`、`pty_spawn`、`list`、`stream_attach`） |
| 前端 TerminalPane 与 stream 客户端 | xterm mounted、stream connected、input enabled、首条输出接收、xterm 写入回调。buffer replay 与新输出分开 |

stream 的"连接已建立"来自对应请求的成功握手，不等首条输出。安静的 shell 可以正常可交互而没有 first-output，这时单独标"未观察到"，不把终端打开算超时，首输出回调也不是 shell-ready 证据。create 与 reattach 分开聚合，reattach 不生成 PTY-spawn 阶段，仍可得到完整片段。测量在可交互、失败、取消或截止点结束，首输出可在同一 run 截止前用一个短观察窗口，不把整段终端会话算进打开的 span。断线重连是后续操作。异步模块完成和旧 stream 的事件必须匹配本次 interaction 与 terminal，组件重挂载不能重复完结同一条记录。生产用户终端不注入 marker、按键或命令。

### 3.5 验收矩阵

| 场景 | 通过条件 |
| --- | --- |
| profiling off | 无新增计时、测量对象、导出线程、前端 profiler 加载、监听或上传，业务行为一致 |
| 开启但未选中 | 入口预过滤直接跳过。后置绑定不匹配则丢弃候选并归还名额，不导出范围外数据 |
| 配置错误、任一 producer 未开启、旧客户端 | 业务正常，已选中的操作按范围报告缺失，不伪造完整 |
| 目录缓存命中、空目录、空文件、大文件 | 用户侧终点正确。缓存命中不要求 server 片段，内容截断不混同计时截断 |
| 文件快速切换、后台 refresh、页面离开 | 旧请求不误报可见，`superseded`、background、`abandoned` 分开 |
| 终端新建、热复用与 reattach、安静 shell、模块冷热 | 正常跳过仍完整，首字节不是可交互的前提，首输出与 replay 单列 |
| 文件或终端触发 stopped 容器 wake，并发共同等待 | 原操作含全部等待，wake 因果关联，不重复累计共享执行成本 |
| 调用失败后重试、应答 call ID 不匹配 | 每次物理调用独立，后一次成功不掩盖前一次缺失 |
| 等锁时过期、sandbox manager 期限更短、阶段跨截止点 | 已应答但部分，保留先前阶段，空列表不抹掉 skipped 或 truncated |
| exporter 队列满、线程启动失败、文件或日志阻塞、前端上传失败 | 有界丢样可见，业务不失败、不等待导出 |
| browser 未回报、纯 API 调用 | 分别判断 browser 与 server 所需覆盖，server 延迟不标为"点击到可用" |

每类操作都要有真实业务路径的 on 与 off 对照。文件 helper 的微基准与启动 recorder 的微基准不能代替 CP → sandbox manager → 前端整段采集的开销。

## 4. 统一报告四类结果

1. **Latency**：成功达到对应 milestone 的 P50 与 P95，样本量与慢样本，样本足够再解释 P99。首次与复用、running 与 stopped、数据大小、并发程度分开。
2. **Outcome**：成功、失败、timeout、用户取消、被新操作替代、页面离开或结果未知。后端成功但页面已离开时，两个视角都保留。
3. **Freshness 与 continuity**：状态多久没更新，断线多久，恢复到哪个 cursor，数据是否过期或截断。输出长时间静默可能只是模型或命令正常运行，不凭字节间隔判定故障。
4. **Pressure 与 overhead**：DB pool wait、sandbox manager 队列与锁等待、线程池排队、host slots、CPU、内存与 I/O 压力、stream buffer 与丢弃量、每次操作的 Docker exec 与 inspect 数量。复用已有 host 快照，它是环境上下文，不是本次请求独占的资源账单。

开启的实验同时显示覆盖率。已选中的排队任务、超时请求和丢失的浏览器样本不能悄悄消失，latency 分布按完整样本给出，失败与未知另列。范围外和关闭时的操作不是 missing measurement。

采集规则：

- 浏览器测用户动作到本地可见状态，服务内部用各自的单调时钟。UI commit、输出处理回调和实际 paint 不能混称同一个显示时刻。
- 跨机器只关联，不未经校准相减时间戳。父 span 与并行子 span 不重复求和，不把阶段 P95 相加得总 P95。
- 浏览器的 intent 是总等待的起点。只在发 HTTP 时才开始，会漏掉 debounce、前端模块加载、前置条件等待和重试 backoff。
- 默认不采集路径全文、文件内容、终端输入输出、prompt、token 或原始命令。指标 label 只用操作名、结果、大小桶等有界维度，实体 ID 只用于受权的明细关联。
- 采集异步、有界，不阻塞执行。浏览器观测是不可信的诊断数据，不能用于计费或授权。
- 减少后端工作若导致页面显示旧数据，必须在 freshness 与正确性检查中显现。不能只凭 HTTP P95 下降就说用户操作变快。

## 5. 落地与优化

首批三类操作的完成标准是同时具备：可信的边界、按需采集、阶段归因、完整性信息，以及能跑通真实操作的验证。服务端部分已就绪，浏览器终点仍待做，做完前不宣称三类端到端基线、缓存命中覆盖或线上优化结果。随后补 P0 的提交、会话打开、停止闭环，再按 §1 推进 P1 与 P2。第一版用结构化日志、有界存储与导出验证，不以建成通用 tracing 平台或全量仪表盘为前提。

每项操作配显式开启的真实操作 profiling 与固定 benchmark：小与大文件、少与多目录条目、冷与热前端模块、running 与 stopped 容器、新建与重连终端、短与长会话、单并发与满容量。使用独立测试 workspace，不清生产缓存、不改用户 shell、不向生产任务注入测试输入。每次优化对照相同场景的 baseline 与 candidate，验证用户总等待与目标子阶段同步变化，保留失败、超时、缺失率和原始样本。回归门槛在第一轮基线后确定。
