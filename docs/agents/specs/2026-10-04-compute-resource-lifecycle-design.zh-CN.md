# Cloud Agent 计算资源生命周期与服务器管理

状态：已实现。运维入口见 [资源管理运行手册](../../developer/operations-compute.md)。

## 目标

Admin → Servers 页面用同一套流程添加服务器，来源有两种：

- 接入一台已能 SSH 登录的机器，不要求预装 Cloud Agent。
- 向 CloudLab 或其他云平台申请新资源。

两种来源都实现抽象父类 `ComputeProvider`，取得资源后共用 Host 部署、注册和就绪检查。Host 模块管理执行资格，计算资源模块管理底层资源，跨模块的操作由应用层协调。

页面只有一个全局运行位置设置：管理员选定一台服务器，所有新 sandbox 都用它。单个 sandbox 的位置选择和已有 sandbox 的跨机器迁移不在范围内。

## 模型与职责

```text
ComputeAllocation  一次接入或外部分配，记录来源与管理范围
  └─ ComputeNode   分配中的一台服务器
       └─ Host     服务器上已注册的执行环境，可有多个
            └─ SandboxGroup 容器、工作区和会话
```

CloudLab 的 Allocation 对应一个 experiment，可含多个 Node。SSH 接入的机器也有一个 Allocation 和一个 Node，表示平台接入了它，不表示平台创建或拥有它。`Server` 只是页面的聚合视图，没有自己的表和写接口。

| 模块 | 负责 | 不涉及 |
| --- | --- | --- |
| Host 管理 | 注册、凭证、心跳、并发、Drain、移出池 | Provider SDK、租期 |
| SandboxPlacement | 全局运行位置的读取、校验、更新 | 机器申请与销毁 |
| 资源模型与策略 | 期望状态、租期、管理范围、续期策略 | HTTP 路由、SDK、Docker |
| 资源流程协调器 | 接入、续期、退出的步骤与恢复 | 不通过 HTTP 调用本服务自己的路由 |
| `ComputeProvider` 子类 | 资源接入或供应商 API、观测、错误转换 | Host 表、sandbox 表、任务调度 |
| HostBootstrap | 通过受信连接统一部署和就绪检查 | 云资源的申请、续期、销毁 |
| ServerQueries | 合并数据库里的观测，生成可用动作和阻塞原因 | 页面 GET 不调用云平台 |

Provider 只返回资源事实。它不能把 Host 改成 online、清空会话、修改任务或调用 Docker。系统使用现有 Postgres 加后台 worker，不引入消息总线或工作流引擎。

### 存储

| 表或记录 | 内容 |
| --- | --- |
| `agent_compute_allocations` | provider key、可选账号引用和外部资源 ID、接入方式、校验过的 provider 参数、管理模式、期望与实际状态、实际到期时间及观测时间、策略与 revision |
| `agent_compute_nodes` | allocation ID、节点标识、资源实例身份、显示名、连接信息 |
| Host 的可选绑定 | 服务端维护的 `compute_node_id`；Host 的状态、slots、心跳、凭证仍归 Host 模块 |
| `agent_compute_operations` | request ID、类型、目标、固定的执行参数、步骤、状态、错误、重试时间、执行租约与 fencing generation |
| Host 准入限制 | 目标 Host、来源、作用范围、版本 |

约束：

- 资源 ID 和 Host 名称分开管理。新 experiment、重新分配的节点、同名的另一台服务器都获得新的资源身份，不因 hostname 相同继承旧会话。保留会话仍依赖某个绑定时，不能把它改指向另一个资源实例。
- 管理模式分 `observe` 和 `manage`。导入默认 `observe`，没有云平台变更权限；`manage` 要明确授予哪些操作和完整范围。持有账号 Token 不等于获得该账号全部资源的管理权。
- 外部身份 `(provider_id, external_id)` 唯一。SSH 接入使用平台生成的登记 ID，地址和主机名只是连接信息。
- 未知到期时间与无到期时间分开表示。读取失败时保留上次观测并标 stale，不把 `null` 显示成永久有效。
- 数据库只存 secret reference。表单、资源规格、操作结果和日志都不含 Token。Host 和 sandbox 拿不到 Provider 账号凭证。
- 状态分开表达：Host 沿用 `enrolling / online / draining / offline / disabled`；资源有 `desired_state=present|absent` 和 Provider 观测状态；操作为 `pending / running / waiting / blocked / succeeded / failed / unknown`。资源 ready、Host online、可以安全释放是三个独立事实。

## 添加服务器

| 方式 | 输入 | Provider 行为 |
| --- | --- | --- |
| 接入已有服务器 | SSH 地址、端口、用户、凭证引用、已核实的主机身份 | `ExistingSSHProvider.acquire(mode=attach)` 核实连接与身份，不申请新机器 |
| 申请云服务器 | Provider、账号引用、该 Provider 的资源配置 | 子类 `acquire(mode=provision)` 申请资源，返回进度和最终节点 |

之后两条路径相同：环境检查，安装或采用已有 Host，注册，readiness。拿到某台 AWS 或腾讯云机器 SSH 信息的用户也走 SSH 接入。平台只获得所声明的能力，不从服务器品牌推断云账号或租期管理权。

底层保留 `attach` 和 `provision` 两种显式意图，不让一个方法根据"机器是否存在"自己决定创建。云 Provider 也可声明支持 attach，凭外部 ID 接管已有实例或 experiment。

### 接口

`AddServer(provider, mode, provider_config, deployment_profile, request_id)` 是应用用例，不是新的可写实体。应用层从注册表按 provider key 选子类，记录接入意图，驱动 `acquire`，再调用同一个 HostBootstrap。`provider_config` 由各子类的配置模型校验，不摊成一个满是可空字段的父类对象。

后端路由在 `/admin/agent/` 下，实现见 `cloud_agent/api/admin_compute.py`。核心如下，另有连接、默认值、自动扩缩、部署模板等辅助路由：

| 路由 | 用途 |
| --- | --- |
| `GET /servers`、`POST /servers` | 聚合列表；接入或申请并部署 |
| `DELETE /servers/{id}` | 只清理本地记录，不销毁资源 |
| `POST /server-preflights` | 无资源分配副作用的连接与配置验证 |
| `POST /compute-allocations/import` | 只导入观测，不部署 Host |
| `PATCH .../{id}/policy`、`POST .../{id}/extend`、`POST .../{id}/rotate`、`POST .../{id}/retire` | 策略、手动续期、轮换、退出 |
| `GET .../{id}/retirement-impact` | 退出范围与阻塞原因 |
| `GET /compute-operations`、`GET .../{id}`，`POST .../{id}/retry`、`cancel`、`resolve`、`confirm-absent` | 找回、查看、恢复操作 |
| `GET /compute-providers`、`/compute-connections...` | 已启用的 Provider 和字段描述；连接元数据与受保护的凭证写入 |

规则：

- 原有 `/admin/agent/runner-hosts/...` 保留。Host 命令从 `api/admin_hosts.py` 提取到 `application/host_commands.py`，鉴权、审计、错误响应不变；资源流程调用同一组用例，不复制 SQL。
- Remove 只撤销凭证并让 Host 变 disabled，机器继续存在。DELETE 和 Forget 只清理记录，不因 Provider 类型增加销毁副作用。对仍有资源绑定或保留会话的 Host，Forget 返回 `409`。已转成 `cold` 的 sandbox 不算保留状态。
- 长操作返回 `202 + operation_id`，配置更新返回保存的 revision。页面完成任一操作后重新读聚合查询。
- 后端返回 `allowed_actions` 和有类型的 `blocked_reasons`，执行时重新验证。前端按能力显示按钮，不写 `if cloudlab ... else ...`。
- 所有端点要求 `agent.admin`，并写审计。隐藏按钮只是体验。

### 页面

页面是 **Admin → Servers**。

- 顶部是 **New sandbox location**，下面是统一服务器列表。每行显示名称、来源、状态、sandbox 数、剩余租期。**Harness version** 选择器在列表下方。
- 详情窗口有 Overview、Lease、Activity、Access settings 四个页签。并发、Drain、移出池、Forget 在 Overview；续期策略在 Lease，只对支持租期的资源出现；账号和 SSH 密钥在 Access settings，密钥不回显。
- 新增窗口用卡片区分 **Existing SSH server** 和各云 Provider。账号与密钥在同一表单里选择或填写，先保存凭证再提交服务器请求。只有一个提交按钮，不要求先单独 Validate。CloudLab 实验名、模板参数、项目组和"仅观察导入"放在高级选项。
- 新增请求省略部署模板，由控制面从运维配置里选一个未被占用的。没有可用模板时拒绝，不复用其他服务器的身份，不放宽隔离。
- Provider 列表只含后端已启用的实现。字段位置由 Provider 的元数据声明，控制面不依赖表单分组。
- 资源可用而 Host 未注册时显示 Awaiting agent。HTTP 202 不表示服务器已可用。
- 编辑草稿绑定打开时的 revision，冲突时保留输入并提示刷新，不覆盖另一个管理员的修改。每次提交生成稳定的 request ID，重复点击和响应丢失后用同一个 ID 找回操作。
- 没有 SecretStore 时，连接管理显示不可用，不退回明文数据库或浏览器存储。
- 下线入口 **下线并释放资源** 与 **暂停接单**、**恢复接单**、**移出任务池** 分开，不用含糊的 Delete。影响摘要显示整个 Allocation 的节点、Host、任务和保留会话；CloudLab 明确说明会影响整个 experiment。

## 全局运行位置

沿用 `agent_runner_policy.active_host` 和 `PUT /admin/agent/runner-hosts/active`，鉴权与审计不变。`SandboxPlacement` 只封装读取、校验和更新，不另建策略表，没有逐任务的位置字段。

- 候选项是已完成 readiness 的 Host，显示来源、可用性和容量。选择位置不会隐式申请机器，新接入的服务器也不会自动成为目标。
- **Automatic** 清除固定，恢复在合格 Host 池里分配。目标不可用时不会自动退回 Automatic。
- 切换提交后，尚未分配的新 sandbox 只在目标 Host 首次领取。已领取、创建中或已保留的 sandbox 继续用原 Host，Fork 也留在原 group 的 Host。页面同时显示统一目标和各服务器上的实际 sandbox 数量，不声称"已迁移"。
- 目标离线、Drain、容量或租期不足时显示等待原因，不在别处偷偷创建。
- Claim 在事务里读取并锁定全局 policy。切换前已完成的领取保留原 Host，之后的用新目标。切换目标不会顺带 Drain、Remove 或释放旧服务器。

## Provider 父类与子类

```python
class ComputeProvider(ABC):
    def capabilities(self) -> ProviderCapabilities: ...
    async def acquire(self, request: AcquireRequest, intent: OperationIntent) -> ProviderReceipt: ...
    async def recover_acquire(self, intent: OperationIntent) -> AcquisitionResolution: ...
    async def inspect(self, ref: ResourceRef) -> ResourceObservation: ...
    async def extend(self, ref, request) -> ProviderReceipt:   # 默认抛 UnsupportedCapability
    async def release(self, ref, intent) -> ProviderReceipt:   # 默认抛 UnsupportedCapability
```

实际定义在 `ports/external/compute_provider.py`（还有 `validate_config`）。资源来源靠继承扩展，连接和部署靠组合复用。

- 父类只定义统一语义和明确的"不支持"结果。它不持有业务数据库，不决定重试，不部署 Host，不参与调度。
- `ProviderCapabilities` 声明 `acquisition_modes`、`can_extend`、`can_release`。能否真的释放还取决于具体资源的管理模式、权限和保留状态。不支持的操作在提交前拒绝。
- `ProviderReceipt` 表示已确认、受理待查、被拒绝或结果未知，不等于资源已达到期望状态。`recover_acquire` 返回已确认、仍在进行、确认未创建或结果未知。
- 连接描述只引用访问方式、目标和凭证引用，不含明文秘密。
- 子类返回标准错误类别（权限、配额、暂时不可用、已终止、能力不支持、结果未知），诊断不含秘密。只有 Provider 确实给出证据时才标记"需要人工审核"，不把所有 `403` 当成待审批。

| 子类 | acquire | 续期与释放 |
| --- | --- | --- |
| `ExistingSSHProvider` | attach 已有且可核实 SSH 的机器 | 无云资源续期或销毁；仍可移出 Host 池。租期报告为未托管，不把 SSH 可达当作永不到期 |
| `CloudLabProvider` | provision experiment，或 attach 指定的 experiment | 延期与终止，作用范围是整个 experiment |

其他云（Google Cloud、Azure、AWS、腾讯云）是扩展位置，不预先添加空实现。新增供应商只需加子类、配置校验、注册和适配器测试，Host 管理和续期流程不增加供应商分支。若需要父类没有的能力，显式扩展契约，不塞进任意参数。

## 续期

手动续期和定时策略都提交 `RequestExtension(allocation_id, target_expires_at, reason)`，只有触发来源和目标时间的算法不同。

策略字段包括 `enabled`、`renew_before`、`extend_to_horizon`、理由、停止新会话和停止领取的提前量。只有明确启用的 managed allocation 才自动续期，导入和保存 Token 都不会开启。

1. Worker 按服务器时钟判断是否需要续期。它在控制面一侧运行，不依赖被续租的机器存活。
2. 事务里保存操作和固定的目标到期时间。同一 allocation 的请求串行，等价请求合并。
3. 事务外调用 Provider。之后重新观测，实际到期时间达到目标才算成功；部分批准会保存实际时间并显示未满足的目标。
4. 临时错误退避重试，明确拒绝或凭证失效则阻塞并通知管理员。超时先查询，不用"当前时间加若干小时"生成更远的目标。
5. 按实际租期更新 Host 准入限制。

CloudLab 的 API 接受 `expires_at` 或 `extend_by`。适配器优先使用已持久化的绝对时间，以读回值为准。延期受 CloudLab 规则约束，不是永久在线保证。关闭网页不会停止自动续期；关闭开关只阻止后续请求，已发出的请求仍按操作跟踪。

### Host 准入限制

独立于 Host liveness 的机制。每条限制记录来源（allocation ID、用途、operation ID）、范围和版本，用途分 `lease_deadline`、`retirement`、`bootstrap`，有效限制取并集。

- `new_groups`：阻止创建新 sandbox group，现有会话的后续轮次照常运行。
- `all_claims`：阻止所有新的任务领取，已运行的任务按原规则结束。
- 管理员的 Drain 仍由原机制管理。Undrain 不绕过资源限制。续期成功只解除 `lease_deadline`，不会清除同一资源的 `retirement` 或 `bootstrap`，也不撤销管理员的 Drain。

限制在最终写入点检查：Claim 在扣 slots 的同一决策点，Fork、初始 reservation、Host 绑定和 enrollment 各在自己的写入点。只在 HTTP 路由检查有竞态。加锁顺序固定：先全局放置 policy 锁，再按 allocation ID 排序取资源准入锁，最后才是 job、Host、group 写入；创建依赖的事务取共享锁，退出栅栏和最终释放取排他锁。调用外部 API 时不持有数据库锁。

有硬到期时间的受管资源，claim 还按已持久化的期限和服务器时钟计算准入，worker 停止也不会超期接单。观测超过 freshness 阈值时暂停新的放置。

建议 `renew_before > stop_new_groups_before > stop_claims_before >= 0`。不轮换的分配另有 `move_out_before`（默认 2 小时，`DEFAULT_MOVE_OUT_BEFORE_SECONDS`）：进入窗口后 Host 停止领取，会话逐个经检查点搬到别的 Host。提前停止接单只降低中断风险，不保证现有任务在到期前完成。

机器租期、attempt lease 和 API Token 有效期是独立的字段和策略。付费续费需要单独定义费用和授权，不因子类声明了 extend 就自动购买。

## 退出与删除

`retire` 是应用用例，不是对 `delete_server()` 的包装：

```text
记录退出意图并设置准入栅栏
  → 冻结资源绑定和新会话成员创建
  → 等待在途任务与操作结束
  → 检查全部保留状态和管理范围
  → 撤销 Host 接入资格
  → 发送 Provider release
  → 确认外部资源已终止
  → 保留退出的资源与操作记录
```

释放前检查：

- 全部节点和 Host 的绑定范围，并确认新绑定已被栅栏阻止。
- 运行中的 attempt、正在 materialize 的 Fork、未决的 prompt delivery、相关 workspace 操作。
- 保留的 sandbox group，包括 stopped 容器。归档聊天和停止容器都不等于放弃状态。
- 最新的容器观测是否与数据库依赖一致。离线、观测过期、未知容器或未覆盖的节点一律阻塞，不把未知当作零。
- 全局放置是否仍指向该资源。

阻塞原因包括 `unmanaged_scope`（管理范围不完整，或 experiment 含未纳入管理的用途）、`placement_pinned`（管理员改选或清除全局固定后才能继续）、`retained_groups_present`。目前没有跨机器恢复，也没有"归档即可丢弃"的契约，所以仍有保留 group 就阻塞。不提供 `force=true`。

其他规则：

- CloudLab 的终止单位是整个 experiment。只接管部分节点时不能获得整体释放能力。
- `ExistingSSHProvider` 没有 release。页面只提供"移出任务池"和受保护的记录清理，后端收到释放请求也拒绝，不改成 SSH 关机或返回假成功。
- 退出被阻塞时，已启用的续期继续执行，且不解除退出限制。退出意图本身不开关续期。
- 最终检查通过后先持久化 `release_committed`，再撤销 Host 凭证并发送释放。从这一点起不能取消。提交前取消只清除该退出操作的限制。释放结果未知时保持限制并继续观测，不自动 Undrain 或重新注册 Host。
- 进入释放阶段后不再启动新的续期。已有续期结果未知时先核对。
- 外部平台自行回收是另一种事实：保存 Provider 证据，交给会话领域处理丢失的状态。单次心跳超时不等于数据已丢。

## 部署与操作恢复

### 申请到可接单

流程：记录意图，`acquire`，观测节点，HostBootstrap 部署，一次性 enrollment，heartbeat、sandbox manager 与安全环境 readiness，允许新会话。

- HostBootstrap 是独立接口，通过 `SshTransport` 等连接组件工作，首个实现复用现有部署脚本。云适配器不负责 SSH、Docker 或 Kata。安全检查不因 Provider 更换而放宽隔离、权限或网络。
- 节点只需满足访问条件，Host 软件由该流程安装。检测到已有安装时，核对部署身份和版本后采用或报冲突，不覆盖未经接管的 Host。支持的平台和权限要求在 preflight 里返回。
- enrollment token 在即将注册时签发，绑定 Host 名称和服务端记录的目标 Node。节点绑定由控制面取得，不信任 runner 自报的 provider ID。
- 签发 token 前就建立 `bootstrap` 准入限制。即使首次心跳让 Host 变 online，也要等受信的 readiness 检查通过才接单。
- 重复部署复用操作身份。已接入的 Host 用其持久凭证。丢失的 token 在核对注册结果后重新签发，不会反复注册而挤掉现有 Host。
- 创建成功而部署失败时，操作停在可诊断状态，可重试。资源仍显示在页面，清理走退出流程，不在异常处理里直接删除。SSH attach 部署失败只保留失败状态，不对已有机器做补偿删除。

### 持久化与并发

- 外部副作用先记录意图再执行。`request_id` 与参数指纹唯一：同一请求重试返回同一操作，相同 ID 不同参数返回冲突。
- 每个 allocation 同一时刻只执行一个外部变更。waiting 或 blocked 的退出不占用执行租约，所以仍可续期；`release_committed` 之后不再派发续期。
- Worker 以短事务领取操作，释放锁后调外部 API，按执行租约和 fencing generation 写回结果。失去租约的 worker 不能覆盖新状态。数据库栅栏撤不回已发出的云请求，所以还需要固定的请求参数、外部幂等或查询恢复，以及不可逆阶段的取消限制。
- CloudLab 创建接口没有标准幂等键。适配器持久化专用项目内的预定名称和创建上下文，只有能唯一核实时才接续。无法确定上次创建是否成功时标记 `unknown`，要求核对或显式导入。遇到同名资源不直接收编或删除。
- 释放超时不宣告成功。只有在正确账号和作用域下有足够证据，才确认资源已终止。资源记录用 tombstone，避免把之后同名的机器当作原资源。
- CloudLab 对指定 UUID 的 `DELETE 204` 视为已删除，查询返回 UUID 和 project 都匹配的终止状态也可用于恢复。单独的 `404` 返回 `resource_visibility_unknown`：操作保持 release committed，保留资源与 profile 的占用，等提供方确认后重试。

## 代码位置

路径相对 `apps/backend`，前端在 `apps/frontend`。

| 位置 | 内容 |
| --- | --- |
| `cloud_agent/application/host_commands.py` | Host 命令与 `SandboxPlacement` |
| `cloud_agent/application/server_queries.py` | 聚合读模型 |
| `cloud_agent/application/compute_*.py` | AddServer 与资源命令、续期、退出、释放、轮换的流程 |
| `cloud_agent/domain/compute.py`、`schemas/compute.py` | 投影、幂等指纹、命令与策略校验 |
| `cloud_agent/ports/external/compute_provider.py`、`host_bootstrap.py` | Provider 与部署接口 |
| `cloud_agent/providers/compute/` | `existing_ssh.py`、`cloudlab.py`、注册表、连接密钥 |
| `cloud_agent/workers/compute_resources.py` | 定时扫描、领取、退避、恢复 |
| `cloud_agent/storage/repositories/compute/` | Postgres 实现与事务约束 |
| `cloud_agent/api/admin_compute.py`、`admin_hosts.py` | 资源路由与保留的 Host 路由 |
| `AddComputeServer.tsx`、`ComputeConnectionForm.tsx`、`ComputeResourcePanel.tsx`、`ComputeResources.tsx` | 前端新增表单、连接、租期和操作进度 |

## 范围外

单个 sandbox 的位置选择、已有 sandbox 的跨机器迁移与恢复、按负载自动扩缩容（CloudLab 的自动扩缩另有实现，见运行手册）、硬件规格调整和云费用管理。它们可以复用这里的资源身份和操作机制，但不是本设计的前提。

## 外部依据

- [CloudLab 资源与延期说明](https://docs.cloudlab.us/basic-concepts.html)：experiment、节点、延期审核、本地磁盘的临时性。
- [CloudLab Portal API](https://docs.cloudlab.us/advanced-topics.html)。
- [CloudLab OpenAPI](https://gitlab.flux.utah.edu/emulab/portal-api/-/blob/master/openapi.json)：创建、查询、延长、终止；延长支持 `expires_at`、`extend_by` 和 `reason`。生产 API 地址、账号权限和实际错误行为需在接入验收时验证。
