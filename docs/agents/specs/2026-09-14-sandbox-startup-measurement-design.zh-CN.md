# 沙盒启动时间测量设计

状态：已实现启动测量。该测量由 runner 与 sandbox manager 采集，经 CP 存入 Postgres，在 Admin → Measurements 读取。浏览器终点、CP 排队之外的用户端到端边界、真实四路径线上基线仍未做。

这是按需 profiling，默认关闭：下文的"每次启动"都限于显式开启并被选中的启动。开关、选择、期限和关闭路径成本见[平台操作测量清单 §0](2026-09-14-platform-operation-measurement-inventory.zh-CN.md#0-可选-profiling-开关)，Admin 控制见[Measurement 默认可控制](2026-09-22-measurement-admin-default.zh-CN.md)，环境变量见 [ENVIRONMENT.md](../../../ENVIRONMENT.md)。文件、终端等其他操作共用同一套采集器，见测量清单。

目标：对每次被选中的启动回答三个问题：总共等了多久，时间花在哪里，某个优化是否真的有效。

## 1. 为什么现有记录不够

- activity 的 `created_at` 是 CP 接收时间，两个事件的间隔含 HTTP、重试和接收延迟，不是阶段耗时。
- `agent_attempts.started_at` 在 claim 插入时产生，不是容器启动或 CLI 开始执行的时刻。
- runner 轮询空闲时会 sleep，排队可能含轮询等待，不能算进沙盒创建。
- 创建、启动、网络检查、workspace 准备、resident CLI 复用或重开、prompt 前的 census 与 observer 准备，都在 `docker start` 之后。`context_status` 本身不足以判断实际走了哪条路径。

这些是埋点依据，不是已确认的性能瓶颈。镜像构建、拉取、host preflight 与 enrollment 与每个任务的启动分开测，运行时若发生镜像缺失或获取，必须显式记录。

## 2. 指标与边界

不用一个含义不明的 `startup_time`。每个指标都写明起点、终点和计时来源：

| 指标 | 起点 → 终点 | 回答的问题 |
| --- | --- | --- |
| `queue_wait_ms` | 当前入队周期的 DB 入队时间 → DB 分配 attempt | 等待调度多久（含同 chat 前序 turn、容量、host pin、轮询） |
| `claim_rpc_ms` | runner 发出 claim → 解析出成功响应 | 领取多慢；CP 侧再拆 DB claim、grant、repo entitlement、clone credential |
| `sandbox_prepare_ms` | runner 得到 claim → workspace、runtime 检查及 guest 网络验证通过 | 分配后多久具备运行条件。沙盒优化的主指标 |
| `resident_prepare_ms` | 首次开始 resident 获取 → 得到最终 resident descriptor | CLI 复用或初始化多久，含失败的复用尝试和重试 |
| `host_to_dispatch_observed_ms` | runner 得到 claim → 收到 sandbox manager 的 prompt dispatch 通知 | 平台多久把本轮任务送进 CLI |
| `first_visible_ms` | 浏览器提交 → 首个本轮 assistant 文本或工具活动被渲染 | 用户实际等多久（浏览器终点未实现） |

`sandbox_prepare_ms` 表示平台现有检查通过，不声称 CLI 协议握手已完成。生产每次启动不新增 Docker probe。benchmark 可另跑固定的无副作用命令，耗时单列为 `readiness_probe`。

`resident_prepare_ms` 带 `readiness_source`：`protocol_ack`（driver 收到明确初始化或 native session 握手）、`process_alive`（只确认进程未退出，Claude 属于此类）、`reused_process`（复用同一 process generation 的存活 transport）、`unknown`。不同来源分别聚合，不为计时发送额外的模型 prompt。`prompt_dispatched` 只表示 transport 已发出，不能称为"模型收到请求"。有 native ack 才另记 `prompt_accepted`。

sandbox manager 另有两个诊断指标：`dispatch_to_first_activity_ms`（允许工具活动）和 `dispatch_to_first_text_ms`（仅 assistant 非空文本）。lifecycle、system init、heartbeat、usage、历史 replay 和原样回显的 prompt 都不触发它们。两者含 CLI、工具和模型等待，不叫 Gateway TTFT。真正的模型 TTFT 只来自 Gateway 的请求记录。

这些边界给出的是因果顺序，不是跨机器时间戳相减得到的精确时轴。`claim_rpc` 与 DB 分配前的等待有重叠，不能相加成端到端时间。

## 3. 埋点

runner 记外层调用区间，sandbox manager 与 driver 返回子区间。每项等待都计入 wall duration。主要阶段（名称以代码为准）：

| 阶段 | 子阶段 |
| --- | --- |
| `claim` | `db_claim`、`attempt_grant`、`repo_entitlement`、`clone_credential` |
| `supervisor_acquire` | 锁等待、group 转移等待、manifest、容器查找 |
| `container_create`、`container_start` | Docker 命令本身，与外层验证分开 |
| `group_network`、`guest_network_check`、`guest_network_sync` | 组网桥、guest 自检、hosts 同步 |
| workspace | `workspace_reset`、`checkout`、`source_import`、`workspace_permissions`、`native_workspace_resume` |
| `setup` | 只记 ran 或 skipped，不记脚本、env 或 stdout |
| `runtime_preflight` | 版本与二进制检查 |
| `resident_start` | reuse check、residency grant、成员锁、旧进程关闭、成员状态准备、`cli_spawn`、协议初始化 |
| `prompt_admission` 等 | process census、observer、window、prompt 写入、可选 ack |
| 控制面回执 | mark running、终端 suspend/resume、record process、事件上传 |

每次调用有自己的 `span_id`。重试增加 ordinal，retry backoff 单独计时，不覆盖第一次失败。setup 未配置记 `skipped`。失败、超时和取消在 `finally` 关闭已打开的区间。进程被杀导致未闭合的区间标 `incomplete`，结束时间和耗时为空。

## 4. 启动路径

路径由实际执行的动作决定，不靠 claim 时的 `context_status` 猜。supervisor 在 lease 里返回本次对容器的动作（`create`、`start`、`reuse`、`unknown`）和对 member CLI 的动作（`new`、`resume`、`reuse`、`unknown`），runner 据此得出 `startup_path`：

| container_action | resident_action | `startup_path` |
| --- | --- | --- |
| `create` | 任意 | `new_group` |
| `start` | 任意 | `resume_stopped` |
| `reuse` | `reuse` | `reuse_live` |
| `reuse` | `new` 或 `resume` | `resume_running` |
| 其他 | 其他 | `unknown` |

- 动作由真正执行它的代码上报，不从容器状态事后推断。`create` 优先于紧随其后的 `start`。
- 动作不依赖 profiling 开关，因为它是事实不是测量。动作缺失时不猜：`reuse` 加未上报的 resident 动作得 `unknown`。
- `resume_running` 要回答为什么没复用活着的 CLI。租约说 resident 是 `live` 时，runner 先发一个不带凭证的探测，被拒后才申请新 residency 并重开进程。结果记为 `resident_probe=accepted|refused|skipped`，被拒时另记 `resident_probe_us`、`resident_probe_status`、`resident_probe_differs`（sandbox manager 列出的字段名，如 `model,residency_generation`，其他原因记 `other`）。只记字段名不记错误原文，因为测量记录不经过脱敏。
- 同时保留 `workspace_action`（`prepare`、`reuse`、`base_import`、`unknown`）和 `member_origin`（`origin`、`fork`）。Fork 本身是独立操作。共享 group 的创建 span 不复制给每个 sibling，等待已有 group 准备的 member 记自己的等待 span 并 link 到同一次创建。旧容器丢失按 LOST 规则失败，测量不引入重建路径。

冷热也不是布尔值：镜像（digest 是否已存在，是否实际获取）、workspace（新准备、已有、补 base objects）、依赖（setup 是否运行）、host page cache（生产默认 `uncontrolled`，实验记录预热步骤）。

每条摘要记录 deployment SHA、sandbox image digest、CLI version、sandbox runtime、execution profile、setup 是否存在、repo 基准版本、model 与 MCP 配置类别，以及启动时最近的 host capacity 样本及其年龄、slots used/total、同 group 活跃 member 数。后者读已有快照，不额外执行 `docker stats`，它用来解释干扰，不是本次启动独占的资源消耗。

## 5. 时钟与数据模型

生产者用 `time.monotonic_ns()` 求差，存相对 offset 和 duration（整数微秒），展示为毫秒。wall clock 只用于查询和关联。每个 CP、runner、sandbox manager 进程有独立的 `clock_domain_id`，不同 domain 的 monotonic 值不相减，尤其不能减 host 与 Kata guest 的时间。RPC 的包围区间与服务端自测区间都保留，差值只能称为 transport、排队或调度余量。跨 domain 的子区间用独立相对时轴，由父 RPC 关联。因此另一个进程返回的子阶段只带 duration 和 `source`，不带 `start_offset_us`。

排队指标使用数据库的 wall clock，记录 `queued_since_at`、`queue_epoch` 和实际分配边界 `allocated_at`，每次重新入队更新周期，异常负差标无效。开始 profiling 前已入队的任务，队列起点可缺失，不从 claim 时回推。

一个被选中的 attempt 一份摘要，保存有界的 span 片段，同一 job 的重试靠 job id 关联。记录主要字段：

```text
attempt_id, job_id, profiling_run_id, measurement_id, schema_version
host_id, sandbox_id?, queue_epoch
startup_path, container_action, resident_action, workspace_action
runtime_id, runtime_version, sandbox_runtime, image_digest, deployment_sha
readiness_source, cache_facts, host_capacity_reference
milestones, durations, producers, producer fragments
startup_outcome: dispatched | failed | cancelled | lease_lost | incomplete
failure_stage?, measurement_complete, dropped_spans
```

`startup_outcome=dispatched` 不等于任务最终成功，模型执行失败不回写成沙盒启动失败。`measurement_complete` 要求启动边界齐全、未被截断、没有缺失片段，被明确跳过的步骤不要求有值。

每个 attempt 的 span 与大小有上限（128 个 span、64 KiB JSON），超限时优先保留摘要、关键 milestone 和失败区间，并标记截断。聚合维度只用 stage、path、runtime、profile、outcome 等枚举。job、repo、owner、host、digest 只用于受权的明细过滤。不记录 prompt、token、环境变量、命令行或原始错误文本。Span 的父子与状态可映射到 OpenTelemetry，日后接 exporter 不重定义边界。

## 6. 采集与可靠性

只有 profiling run 有效且根操作被选中时才采集。默认关闭或未选中时，不创建 collector、关联 ID、片段、frame、exporter 或存储行，也不是先计时再丢弃。选择结果传给子阶段，重复请求不重抽样。

- 选择发生在 runner 的根操作处。名额先预留，`discard()` 归还未选中的名额（空 claim、目标不匹配、claim 异常），`finish()` 才提交，所以一次空轮询不会耗掉 `max_operations`。预算是单进程的，不是跨 replica 配额。
- 目标过滤要求每个配置字段都匹配，缺失值算不匹配，未知字段使整个 run 被拒绝。
- 截止时间由 run descriptor 的绝对 `expires_at` 在首次读取时转为单调期限，重启不续期。到期后不再记录，跨过截止点的 span 在截止点截断并标 `truncated=true`，根记录标 `measurement_complete=false` 与 `profiling_stopped=expired`，已采到的部分保留。
- 日志与 JSONL 走同一条有界队列和一个后台线程，队列满时丢弃并计数。同一份冻结记录另经有界队列进入 Postgres，JSONL 不是入库前提。exporter 线程启动失败时，该进程改用永久禁用的 exporter，丢弃并计数，业务照常。测量代码自身失败不成为任务失败条件。
- 长驻 host 的父子进程路径保持同一条记录：父进程在 claim 前选择并计时 `claim`，把 measurement id、起点、已完成的 span 和消耗预算的 admission instance 交给 turn 子进程，子进程不重新抽样。
- finish 前允许有严格上限的 best-effort flush，失败不阻止 finish，不改变任务结果，不延长 lease。lease 丢失或 finish 之后迟到的写入仍被拒，CP 把选中但缺失的记录标 partial。失败率与覆盖率的分母是本 run 选中的 attempt，不只是成功上传的。
- 不引入新的网络出口，不把 SDK credential 放进沙盒，不改变 Kata、网络、成员隔离或凭据边界。

### 片段协议

sandbox manager 一次只看到一个调用，不知道它属于哪个 attempt，所以不自己决定是否记录。runner 把选中的上下文（run id、operation、绝对墙钟截止时间）随请求发出，sandbox manager 用自己的开关、run descriptor 和继承的截止时间校验后才记录。过期、错 run、非选中 operation 一律拒绝，容器动作照常上报。畸形的测量上下文只让该次调用不记录，绝不使 acquire 以 422 失败。`run_id` 长度在读取 descriptor 时统一限制，超限视为 profiling 关闭并记一条 warning。

片段等于一次调用，完整性按 call id 判断，不按阶段名或生产者：

| 问题 | 字段 |
| --- | --- |
| 这是哪次调用 | 请求与响应各带 `call_id`，runner 在调用前铸造并登记 |
| 是否完成采集 | 响应的 `profiling_recorded`（建过 recorder）与 `profiling_truncated`（因自身截止时间提前停止） |
| 是否正常跳过 | `recorded=true`、`truncated=false` 且 `stages=[]`，才是"本次无需执行该阶段" |

重试是第二个片段，各自交代，后一次成功不掩盖前一次缺失。复用 CLI 时 `cli_spawn` 不存在，`resident_action=reuse` 加空 `stages` 是完整片段。sandbox manager 的期限可能比 runner 短（例如等成员锁就耗尽期限），之后各子阶段被跳过，此时片段"已应答但被截断"，保留已测到的阶段，根记录标 `truncated=true` 与 `truncated_fragments`，既不算缺失也不算完整。被截断的区间是下界，不是真实耗时。缺失形如 `missing_fragments=["sandbox manager:c2:resident_start"]`。sandbox manager 未开启时，它的调用都缺席。旧 sandbox manager 或 runner 的缺失字段降级为 unavailable。新 frame 需要 reader 显式支持、capability 与 version 协商，不假设旧 reader 能安全忽略未知 frame。

详细数据建议保留 30 天，之后按日保存有界分布统计（容量待验证的初值）。明细按 `attempt_id` 取，摘要按时间和路径取，沿用 Admin 的授权。专用 benchmark 可在显式指定的 run 内选 100% 采样。报告必须显示 run 范围、有效期、选择率和采集状态。

## 7. 用数据定位瓶颈

Admin 的 attempt 明细对采集过的实验显示 Startup 区域，未开启时显示 profiling 未启用，不显示错误或伪造的零。数据也可导出为 JSONL 或 CSV。

- 顶部：启动路径、沙盒准备、prompt dispatch、readiness source、outcome、覆盖率。
- 时间线：runner 外层阶段，可展开的 sandbox manager 子阶段，失败、重试和未闭合阶段。跨时钟域的来源单独标注，没测到的步骤不画成零长度。
- 汇总：每个 cohort 的样本数、成功、失败、取消、未完成数，P50 与 P95，慢样本链接和版本。样本足够才解释 P99，始终显示样本量。
- 尾部分析：取同 cohort 主指标最慢的 5%，再看它们的阶段分布。不把各阶段的 P95 相加，也不把"总 P95 减某阶段 P95"当成优化收益。

同一时钟域内，父区间的 self time 等于父耗时减去子区间的时间并集，并行子区间不能求和。串行顶层阶段应能解释主指标，剩余显示为 `unattributed`。优化优先级看"慢样本中关键路径耗时 × 出现频率"。例如观察到 clone 慢才细看 fetch、checkout 和 import；观察到并发下 acquire 慢才分析锁等待；观察到网络检查慢，先区分各子阶段。

## 8. 可重复的 benchmark

| 层 | 内容 | 用途 |
| --- | --- | --- |
| A：基础设施 | 固定 digest，无业务仓库和 setup，经真实 manager 创建、启动并执行固定 probe | 隔离 Docker、Kata 与网络成本，命名为 `infrastructure_benchmark`，不冒充完整任务启动 |
| B：平台完整启动 | 固定 repo commit、setup、runtime、model、MCP，走正常 claim → sandbox manager → driver → dispatch | 主回归基准，含真实 grant、源码准备和 observer 开销 |
| C：用户体验 | 相同的固定小任务，从正常请求到首个可见活动或文本 | 验证启动变快是否真的缩短等待，模型与外部服务波动单列 |

A 使用真实隔离与网络约束，B 和 C 不绕过认证、admission 或 observer。fake transport 只用于验证计时正确，其结果不称为真实 CLI 启动性能。

场景：四条路径都要覆盖，主矩阵在与生产相同的 Kata 上跑，trusted Docker 另成 cohort。每条路径先测无 setup 的小 repo，再测固定 commit、锁定依赖的代表 repo。并发取 1、约半容量、满配置容量，跨 group 与同 group sibling 的并发分开。固定并记录 host 规格、Docker 与 Kata 版本、guest kernel、资源限额、deployment SHA、镜像 digest、CLI version、repo commit、setup、model、MCP 类别、并发数、host 负载、profiling 配置、resource collector 状态和实验顺序。

- 用专用可丢弃的 benchmark group 和固定测试 repo。`new_group` 每次用新 group，默认镜像已存在（镜像缺失的 host provision 实验单列）。`resume_stopped` 先做规定的 warm-up turn，经安全的 idle-stop 流程停止，再确认原容器存在。`resume_running` 在测试设施内停掉 member transport 后恢复同一 native session。`reuse_live` 要校验 process generation 未变。没达到前置状态的试验记为 preparation failure。
- 不在同一 chat 里连发 200 次并称其为同条件 warm start，因为 native context 会增长。每个样本先用同样步骤建立等长上下文，再只测指定的一轮。不依赖平台并不存在的 transcript replay 或 checkpoint restore 来重置实验。
- 生产环境不清 page cache，不删除生产容器来模拟冷启动。

样本与比较：

1. 每个关键 cohort 先做 20 次 pilot，检查边界、前置状态和缺失率，pilot 不与正式结果混合。
2. 正式 A/B 初始建议每个 cohort、每个版本至少 200 次，报告 P50、P95、分布和 bootstrap 95% CI。CI 不稳定就继续采集。P99 在约 1000 个样本前只作探索值。
3. 同类 host 内按时间 block 随机交替 baseline 与 candidate，每次只改一个优化。涉及 image 的变更分别预取对应 digest。CI 按 host 和时间 block 重采样。
4. 保存每次结果，包括失败、超时、取消、准备失败和未上报。只有达到对应 milestone 的样本进入该 latency 分布，同时展示失败率和覆盖率。timeout 不丢弃，也不填成成功耗时。排队等待另列。
5. 比较绝对变化（ms）和相对变化（%）。profiling 阶段对比解释来源，实际收益由两个版本都关闭 profiler 的完整路径 benchmark 验证。同时检查失败率、上下文保持、网络隔离、资源压力和其他路径有无回归。
6. 阈值在看到 candidate 结果前，基于 baseline 确定，例如"某 cohort 的 P95 降低至少 X ms，启动失败率不超过约定容忍差"。不承诺任意的 SLO，也不拿一次最快值下结论。

报告包含环境 manifest、实验顺序、全部 attempt ID、每个 cohort 的样本、失败和缺失数、分布及 CI、阶段解释、优化前后差值和限制条件，原始结果应能重新聚合出同一份报告。测试数据不带真实用户 prompt 或服务凭据。

## 9. 当前实现与缺口

采集器在 `apps/backend/cloud_agent_shared/operation_profiling.py`，runner 侧接入在 `cloud_agent_host/turn/`，sandbox manager 侧的片段校验在 `cloud_agent_host/operation_call.py`，CP 侧在 `cloud_agent/measurement/`。`scripts/bench_startup_profiling_overhead.py` 测埋点自身的开销，`scripts/bench_operation_overhead.py` 在真实 HTTP 和 Postgres 上测服务端操作的开销，并用真实 Docker 容器产生四条启动路径。验收预算：开启后新增启动 P95 不超过 10 ms 或基线的 1%（取较大者），按逐操作的 `max(10 ms, baseline p95 × 1%)` 给出结论。

尚未实现：

- 选择在 runner，`max_operations` 是单进程预算，没有跨 replica 的统一名额。
- 浏览器终点（`first_visible_ms`）、CP 排队到首输出的完整用户边界，以及首输出的 post-dispatch 片段。
- `setup_script` 脚本内部的分解、`prompt_ack`，以及各 runtime 的 `readiness_source` 差异。
- 真实 agent CLI 初始化、生产 Kata 隔离和跨 replica 下的四路径线上基线。

相关设计：[原生会话、Fork 与共享工作区](2026-08-31-native-session-fork-shared-workspace-design.md)（保留容器、native resume、共享 workspace 边界）。
