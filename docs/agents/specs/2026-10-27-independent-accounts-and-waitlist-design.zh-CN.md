# Cloud Agent 独立账号与 waiting list

状态：已实现。Gateway 模式已移除，账号系统是唯一的登录方式（[决定](2026-10-30-independent-accounts-only.md)）。功能清单见[功能对齐表](2026-10-29-account-feature-parity.zh-CN.md)。

## 1. 产品方向

- Cloud Agent 有自己的账号、申请和审批，用户不需要先有 HybridInference 账号。
- 登录系统的代码和交互改编自 HybridInference，但不共享用户数据库、会话、签名密钥，也不在运行时 import `serving.*`。
- 推理走用户自己的模型连接。Cloud Agent 不提供共享模型、平台 key 或推理额度，费用和限额归 key 所属的上游账号。账号独立不等于上游额度独立：用户填的 key 若属于别处的同一账号，仍共用那个账号的额度。
- 不整体搬入 `PostgresOperationalStore`、推理配额、Gateway JWT 与密钥、模型角色、服务间 SSO。Cloud Agent 的 session cookie 跨站保护不能因复用旧页面而丢失。

## 2. 用户与管理员流程

1. 用户注册并验证邮箱（或手机号、登录名，见对齐表），建立本地身份。
2. 提交 Agent 使用申请，可填用途。登录后能看到申请状态。
3. 管理员在后台查看、批准或拒绝。批准只授予 `agent.use`。
4. 获批用户填写自己的 Base URL、API key 与模型，验证成功才保存，再用这个连接创建任务、续聊和 Fork。
5. 管理员可以撤销准入。停用账号还会撤销它的登录会话。
6. 只有被显式授予 `agent.admin` 的账号能管理平台，不按邮箱匹配隐式提权。`agent.admin` 必须包含 `agent.use`。

| 账号/准入状态 | 登录、看申请 | 连接、任务、终端、工具 | 管理后台 |
|---|---|---|---|
| 邮箱待验证 | 只有验证和恢复流程 | 否 | 否 |
| 已验证，未申请或待审批 | 是 | 否 | 否 |
| 已验证，已批准 | 是 | 是（仍检查资源所有权） | 否 |
| 已验证，被拒绝或撤销 | 是，可见原因和允许的后续操作 | 否 | 否 |
| 已批准，且有管理权限 | 是 | 是 | 是 |
| 账号停用 | 拒绝新登录，撤销现有会话 | 否 | 否 |

申请记录和账号状态分开，所以待审批账号可以登录。规则：

- 待审批时重复提交，保持原申请。
- 拒绝或撤销后，用户可以明确重新申请。
- 停用会清除管理员身份和已获准状态，恢复账号不恢复它们。
- 撤销保留记录，阻止新工作，停止活动执行。重新批准不自动重放积压任务。
- 审批变更带版本检查，防止过时页面覆盖新决定。

## 3. 身份与准入契约

- `user_id` 是稳定、不透明的资源所有者标识。模型连接不依赖邮箱、角色或登录方式。
- 登录给出当前身份，`AgentAdmission.decision(user_id)` 给出当前的 `allowed` 与 `permissions`。每次检查都读数据库，不缓存。
- 申请页面只需要有效身份。个人连接、任务和其他受保护接口需要当前 `agent.use` 加资源 owner 检查。
- 领取任务、自动继续、排队消息和恢复常驻进程时也检查准入，不只在 HTTP 创建入口检查。
- 准入服务不可用时拒绝，不回退到 cookie 里的权限或「允许所有」。
- `/v1/session/me` 返回 `id`、`agent_access` 和申请状态。前端的模型连接页只消费这份结果。
- Profile 和团队成员邮箱都读本地账号资料。

### 终止访问

`terminate_owner_access(user_id, reason, generation)` 依次做：

1. 原子提交准入撤销和版本变化。
2. 拒绝新的 claim。
3. 按 lease fence 取消活动 attempt，释放 residency。
4. 通过 `application/residency.py` 的关闭路径停止成员进程。主机失败时有持久重试和心跳收敛。

只删 cookie、只取消一个 HTTP 请求都不算完成。普通暂停保留加密的连接配置。账号删除另外撤销连接并销毁密文（见[模型连接设计](2026-10-27-user-model-connections.zh-CN.md)）。

## 4. 数据与会话

迁移 `0108_local_accounts` 建立独立 namespace、账号、密码登录、准入、会话、一次性令牌、请求频率计数和撤权清理记录。`0109_local_account_parity` 在此基础上扩展（见对齐表）。

- 账号：稳定 ID、状态、创建和更新时间。
- 登录身份：账号 ID、类型、唯一的规范化标识、验证状态。密码只存哈希（Argon2id，规则与参数适配自 HybridInference）。
- 申请：状态、用途、申请和审核时间、审核人、公开原因。内部备注不回传用户。
- 准入：显式权限和版本。批准和权限写入在同一事务里生效。
- 会话：可撤销的随机不透明令牌，服务端只存用途隔离的 keyed hash。有效期默认 30 天，由设置 `accounts.session_ttl_days` 调整。密码重置和账号停用会撤销旧会话。不使用共享 JWT 或刷新令牌。
- 验证与恢复：邮件验证链接 24 小时有效，密码重置链接 1 小时有效，单次使用。令牌放在 URL fragment 里，页面显式 POST 才消费，所以不进访问日志。响应不泄露账号是否存在。
- 第一位管理员由部署持有的基础设施授权创建，公共注册不能抢占。
- Cookie 保持 `__Host-`、Secure、HttpOnly 和现有的跨站写入检查。认证材料不进入 host claim、sandbox、模型连接或公开 API 响应。
- 邮件配置由 Cloud Agent 自己拥有。`workers/notifications.py` 是 Postgres 通知，不是邮件发送器。

## 5. 历史 Gateway 身份

数据库通过 `agent_identity_namespace` 绑定身份来源。绑定了 Gateway issuer 的数据库，或有 owner 数据但没有绑定的数据库，启动时被拒绝，没有忽略冲突的开关。不按邮箱相等合并账号，不导入密码哈希、刷新 token 或 Gateway 用户表。切换方式见[升级说明](../../developer/upgrade-notes.md#independent-accounts-cutover)。
