# Team 与团队共享环境

状态：已实现。Team、邀请和成员管理，以及团队共享环境配置都已上线。Owner 失去平台准入时的管理面恢复操作尚未实现。

Team 管理一组协作成员，团队拥有可复用的环境配置。成员的 Chat、工作区和容器始终属于成员本人。登录身份和个人推理额度继续由 Gateway 管理。

不做共享工作区：多人共用一个容器会让各自的开发互相干扰，而团队真正需要共享的是环境配置。也不做全平台团队账单、团队 API Key、SSO、SCIM、团队记忆、访客角色、按环境的私有 ACL。

## 1 产品模型

一个用户可以加入多个 Team，每个 Team 可以有多个环境配置。个人空间继续存在，个人用户不必创建 Team。

| 对象 | 含义 | 归属 |
| --- | --- | --- |
| Team | 成员和管理权的范围 | 团队自身，有一名 Owner |
| 环境配置 | 可复用的变量、脚本、快捷操作和版本，属于一个仓库 | 个人或 Team |
| 安装快照 | 某环境当前安装配置的安装结果 | 跟随环境，团队环境的快照全队共用 |
| Chat、工作区、容器 | 一段 Agent 会话及其运行环境 | 始终是执行成员本人的 |

规则：

- 团队环境对团队的活跃成员开放，没有子团队，也没有按环境的 ACL。
- 成员用团队环境创建任务时，仍按本人检查 `agent.use` 和仓库 entitlement。加入 Team 不授予 GitHub 或 GitLab 权限。
- 推理使用执行成员的模型权限和账户额度。
- 团队成员互相信任：团队环境的 Secret 和安装快照对全队生效（§6.4）。
- 团队资源的管理主体是 Team，执行主体始终是用户。`agent_threads.user_id` 和 `agent_jobs.user_id` 仍是执行用户，会话、容器、终端、MCP、Git 凭证和发布的归属不变。

界面里没有"空间"的概念。团队环境与个人环境一起出现在环境列表和新建任务的选择里，带团队标记。

用户流程：A 创建 Team 成为 Owner；A 创建邀请链接并自行分享；B 登录后申请加入，A 批准；A（或管理员）在"设置 → 环境"新建环境并归属团队，也可把自己的环境复制到团队；B 选择它后，任务在 B 自己的容器里以团队的变量和脚本运行。B 可以查看、测试、复制这个环境或设为自己的默认，只有 Owner 和管理员能修改。

## 2 角色与权限

每个 Team 恰有一名 Owner，另有 Admin 和 Member。Owner 任免管理员，管理员负责日常成员管理和团队环境。平台管理员沿用独立的管理面和审计，团队角色不带来平台管理权限。

| 操作 | Owner | Admin | Member |
| --- | --- | --- | --- |
| 查看团队资料和成员名单；查看、测试、复制、选择团队环境，设为自己的默认 | 可以 | 可以 | 可以 |
| 修改团队名称；创建和撤销邀请、批准或拒绝加入；移除普通成员 | 可以 | 可以 | 不可以 |
| 创建、修改、恢复历史版本、删除团队环境；重建或删除其安装快照 | 可以 | 可以 | 不可以 |
| 任免或移除管理员、转让所有权、删除 Team、查看管理审计 | 可以 | 不可以 | 不可以 |
| 退出 Team | 先转让或删除 Team | 可以 | 可以 |

成员记录只存 `admin/member`，Owner 始终从 `owner_user_id` 推导。角色调整带团队 revision 并写审计，所有管理写入在团队行锁内重查当前角色。管理员降级、退出或被移除时，其创建的未完成邀请和申请一并关闭。重新加入默认为 Member，所有权转让后的原 Owner 也成为 Member。

## 3 邀请与加入

不依赖全站用户目录或邮件服务：Gateway 只提供用于显示的 email，没有已验证邮箱的邀请契约，可编辑的名称或未验证邮箱不能当作加入凭据。所以用一次性邀请链接加 Owner 或管理员批准。

### 邀请链接

- 服务端用 `secrets.token_urlsafe(32)` 生成 256 位 token，数据库只存 SHA-256。邀请 24 小时有效，只允许一个账户领取。原始 token 只在创建响应里返回一次，需要再分享就生成新邀请。
- 创建响应丢失时，同一幂等键只能恢复邀请元数据（`token` 为 `null`），不能从哈希恢复链接。客户端提示撤销后重新生成，避免悄悄产生多个有效链接。
- 链接形如 `/teams/join#token=…`，token 在 URL fragment 里，浏览器不会把它发给任何服务器。加入页在登录后的应用外壳之外（与 `/login` 相同），先把 token 移入当前标签页的 sessionStorage（30 分钟有效，用后即删），再清除地址栏的 fragment。token 不写入持久缓存、分析事件、请求日志或登录回调 query，领取时通过 POST 请求体提交。
- 邀请页面只显示团队名、邀请人和申请状态，不返回团队资源。

### 状态与并发

1. B 登录，服务端验证身份和 `agent.use`。B 在加入页明确点击"申请加入"才领取邀请，持有链接本身不产生团队访问权。
2. 事务按"团队行 → 邀请行"加锁，检查有效期、未撤销和团队 active，写入绑定 B 的 join request。
3. Owner 或管理员查看申请人的显示资料和稳定账户标识后批准或拒绝。权限永远绑定 `user_id`，显示名只用于展示。
4. 批准前向 Gateway 确认申请人当前的 `agent.use`。批准事务再检查操作者身份、团队状态、申请有效性和成员容量，然后激活 membership、消费邀请并写审计。

重复领取（连点、刷新）返回同一申请，前提是申请仍待处理，或已批准且本人仍是活跃成员。已批准的申请人后来退出或被移除时，原链接不能让其重新加入：领取返回 409 `no_longer_member`。另一账户不能抢占已领取的邀请。同一用户在一个 Team 只能有一个待处理申请（部分唯一索引），随链接过期的旧申请在下次领取时标为 `expired`。重复批准返回已完成结果，不产生第二条 membership。撤销、拒绝或过期的 token 不能再用。转让所有权时撤销尚未完成的邀请和申请。

### 容量与滥用

所有团队接口都要求已登录且有 `agent.use`，没有匿名入口，token 的猜测不可行，所以不设请求速率限制，用容量上限约束滥用（`domain/teams.py`）：

| 上限 | 值 |
| --- | --- |
| 一个账户拥有的 Team | 20 |
| 一个 Team 的活跃成员（含 Owner） | 50，批准时检查 |
| 一个 Team 同时未使用且未过期的邀请 | 20 |
| 单次列表返回的历史邀请或申请 | 50，仍可操作的邀请全部保留 |

## 4 成员与团队生命周期

### 退出和移除

主动退出、Owner 移除成员、管理员移除普通成员走同一套撤权流程：事务内把 membership 标为 `left` 或 `removed`，递增 `access_generation`，写审计。提交后，新的读取和管理请求立即被拒绝。团队环境没有需要后台清理的运行资源，离队效果由 generation 栅栏实现（§6.3）：

- 离队成员在列表里看不到团队环境，按 ID 读取、复制或绑定返回 404。
- 其使用团队环境的会话在下一轮 claim 时失败（`team_environment_revoked`），正在执行的一轮照常结束。
- 指向团队环境的仓库默认值在其下次新建任务时清除。
- 已进入容器的 Secret 收不回，需要时由 Owner 或管理员轮换。

重新加入时复用历史 membership 的身份记录，再次递增 generation，不会恢复旧会话对团队环境的使用，需要继续工作就由本人新建任务。

### 所有权

`agent_teams.owner_user_id` 是 Owner 的唯一依据。Owner 可把所有权原子地转给有准入资格的活跃 Member，原 Owner 变成 Member。转让前先确认目标是活跃成员，再问 Gateway 其 `agent.use`，避免借此探查任意账户的准入状态。Owner 不能直接退出，也不能被普通成员移除。转让、退出、移除和 Team 状态变化锁同一 Team 行。

Owner 失去平台准入时不自动选继任者，由平台管理面通过经审计的恢复操作处理，该操作尚未实现。平台管理员目前可在 **Admin → Teams** 只读查看所有团队、成员（含已退出和被移除的）、待处理申请和团队审计。团队环境不依赖它：管理员仍可维护团队环境，没有管理员的团队在 Owner 失去准入后环境无人能改，成员可复制成个人环境继续用。

### 删除

Team 只有 `active → deleted`：Owner 带读取时的 revision 删除，未用的邀请被撤销，待处理申请被取消，所有成员立即失去访问权，行和审计保留。团队环境不再出现在任何人的列表里，也不能被新任务选择，已绑定的会话下一轮失败。环境行、版本和审计保留，安装快照按保留规则在 7 天未使用后删除。

## 5 执行与发布身份

团队环境不改变会话、容器、终端、MCP、Git 凭证或发布的归属，它们完全属于执行成员，沿用个人任务的全部检查。

## 6 团队环境

### 6.1 归属与编辑

`agent_environments.team_id` 非空的环境属于该 Team，`user_id` 记录创建人。团队环境必须属于一个仓库，名称在同一 Team 和仓库内唯一，与个人环境的名称互不冲突。

- 创建：Owner 或管理员在 `POST /environments` 带 `team_id`，或用 `POST /environments/{id}/copy` 带 `team_id` 把自己能用的环境复制到团队。归属创建时确定，不能再改。
- 编辑：沿用版本号的乐观并发，保存时锁环境行，两名管理员同时保存会依次检查同一个版本号。普通成员的修改、删除、恢复历史、重建或删除快照返回 403。
- 审计：创建、修改（含恢复历史）和删除写团队审计 `environment_created`、`environment_updated`、`environment_archived`，`target_id` 为环境 ID。
- 默认环境仍按"成员 × 仓库"保存，可以指向团队环境，设置时检查成员能使用它。删除团队环境时清除所有成员指向它的默认值。

### 6.2 运行

绑定与个人环境相同：创建任务时冻结所选版本，之后的修改只影响新任务。两点不同：

- 个人全局变量只在运行期生效。成员的个人全局变量合并进团队环境时一律改为运行期可用，仅安装期的个人变量不合并，同名以团队环境为准。这样同一团队环境对每个成员的安装相同，安装快照才能全队共用，而成员自己的密钥仍能交给 Agent 和它运行的命令。
- 绑定记录 `team_id` 和成员绑定时的 `team_access_generation`。

### 6.3 离队栅栏

每次把绑定的变量值交给容器之前，控制面检查绑定的成员仍是该 Team 的活跃成员、Team 仍为 active、`access_generation` 与绑定时相同。这些路径都经同一个读取（`get_environment_binding`）：每轮 claim、终端、快捷操作、Fork、重新准备环境、环境测试和更新运行期变量。

- claim 时检查不通过：该轮直接置为失败，返回 403 `team_environment_revoked`，不会因换 host 而重新排队。其他接口返回 409。
- 已运行的一轮不被中断，对它而言 Secret 已交出。
- 脱敏和发布扫描仍读取这些值（`for_masking`），以便在该会话已有的输出里继续找到它们，但不把值交给容器。会话环境页的只读视图不含 Secret，离队后仍能打开。

### 6.4 信任前提与已接受风险

团队环境以"团队成员互相信任"为前提，已记入 [AGENTS.md](../../../AGENTS.md) §7：

- Secret 对成员可读。能用团队环境运行任务的成员可以在自己的任务里读到它的 Secret。编辑器只写不读，只防界面回显，不是成员间的保密；对话框对团队环境明确提示。把团队环境复制成个人环境同样复制 Secret。
- 安装快照全队共用。成员的任务或测试可为团队环境排一次预构建，基于该成员检出的提交运行团队的安装脚本，结果供之后所有成员的新任务使用。成员因此能影响其他成员的容器从什么状态启动，这与成员能向共享仓库推送代码属于同一信任范围。删除和重建快照只开放给 Owner 和管理员。
- 离队不收回已交出的值。栅栏只阻止新的一轮，离队成员读到过的 Secret 要轮换。

### 6.5 安装快照

快照查找对团队环境不要求属于同一用户：同一团队环境、同一安装指纹的快照对所有成员可用。使用记录（次数、最近使用时间、校验失败）写在这个共用快照上，保留和淘汰据此计算。快照计入排队那名成员的 20 GiB 配额。删除团队环境的快照时，删除所有成员排出的快照并取消其预构建。Owner 或管理员改了安装脚本或安装期变量后，所有成员的旧快照作废。成员改个人全局变量不影响团队快照，因为它们不参与团队环境的安装。涉及多个成员快照的写操作按账号顺序取各归属者的快照锁。详见 [环境安装快照](2026-10-15-environment-install-snapshots-design.zh-CN.md)。

## 7 数据模型

### Team 核心表（迁移 `0079_teams`）

| 表 | 关键字段与约束 |
| --- | --- |
| `agent_teams` | `id, name, owner_user_id, state (active/deleted), revision, created_by_user_id, create_request_key, created_at, updated_at, deleted_at`；名称 1–80 字符且无首尾空白；`(created_by_user_id, create_request_key)` 唯一 |
| `agent_team_members` | 主键 `(team_id, user_id)`；`role=admin/member, state=active/left/removed, access_generation, joined_at, departed_at`；离队保留记录；`departed_at` 为空当且仅当 active |
| `agent_team_invitations` | `id, team_id, created_by_user_id, request_key, token_hash, state (open/claimed/revoked), expires_at, revoked_at`；`token_hash` 唯一且为 32 字节；`(team_id, created_by_user_id, request_key)` 唯一 |
| `agent_team_join_requests` | `id, team_id, invitation_id, user_id, state (pending/approved/declined/cancelled/expired), decided_by_user_id, decided_at`；`invitation_id` 唯一；`(team_id, invitation_id)` 复合外键指向同一 Team 的邀请；`(team_id, user_id) WHERE state='pending'` 部分唯一 |
| `agent_team_audit_events` | `id, team_id, actor_user_id, action, target_user_id, target_id, details, created_at`；`action` 是固定词表；不存邀请 token、哈希、Secret 或工具原始输出 |

邀请的"过期"由 `expires_at` 在读取时推导，不单独存储。

- 唯一 Owner 由数据库保证。`agent_teams` 有生成列 `owner_membership_state`：未删除时为 `'active'`，已删除时为 NULL。延迟复合外键 `(id, owner_user_id, owner_membership_state) → agent_team_members (team_id, user_id, state)` 在提交时检查，所以无论创建、转让、退出以什么顺序写入，提交时 Owner 不是活跃成员则整个事务失败。团队删除后 NULL 使该行免检，Owner 才可以离开。
- 锁顺序：每个写操作先锁 Team 行，再锁邀请、申请和 membership。领取只知道 token，先无锁读出邀请所属 Team，锁 Team，再锁邀请并复查。只读列表复查成员资格但不加锁。
- `access_generation` 只向前：退出、移除、重新加入都递增，重新加入复用原行。

### 团队环境（迁移 `0082_team_environments`）

| 表 | 变化 |
| --- | --- |
| `agent_environments` | 可空 `team_id → agent_teams`；CHECK 团队环境必须有 `repo`；名称唯一索引拆为个人（`team_id IS NULL`）和团队 `(team_id, repo, normalized_name)` 两个；新增 `(team_id, repo)` 索引 |
| `agent_repository_environment_defaults` | 外键从 `(environment_id, user_id)` 改为只引用 `environment_id`，让成员的默认值能指向团队环境，访问在设置和绑定时检查 |
| `agent_environment_bindings` | 新增 `team_id` 与 `team_access_generation`，CHECK 两者同时为空或同时非空 |
| `agent_team_audit_events` | `action` 词表加入三种环境操作 |

迁移不改已有行。存在团队环境（含已删除的）时拒绝降级，因为删除这一列会把团队环境悄悄交给创建它的成员。

## 8 API

使用现有主监听器和登录身份，sandbox 不能调用团队管理能力。创建类操作用 `request_key` 做幂等，会冲突的修改用 `expected_revision`，重放先验证当前访问权再返回允许返回的历史结果。前缀 `/v1/agent`：

| API | 行为 |
| --- | --- |
| `POST /teams`、`GET /teams` | 创建（同一 `request_key` 返回同一个 Team）；只列本人活跃加入的 Team |
| `GET/PATCH /teams/{team_id}` | 读取；Owner 或管理员带 revision 改名 |
| `DELETE /teams/{team_id}?expected_revision=` | Owner 删除，撤销未完成的邀请和申请 |
| `GET /teams/{team_id}/members`、`POST .../leave` | 活跃成员名册（Owner 在前）；Admin 或 Member 退出 |
| `DELETE .../members/{user_id}`、`PATCH .../members/{user_id}/role` | 移除（管理员只能移除普通成员）；Owner 带 revision 任免管理员 |
| `POST .../transfer-ownership` | 转给有准入资格的活跃 Member，带 revision |
| `POST/GET .../invitations`、`DELETE .../invitations/{id}` | 创建（仅这一次响应带 token）；查看可操作邀请及最近历史；撤销邀请及其未批准的申请 |
| `POST /team-invitations/claim` | 登录用户以 token 领取，生成申请 |
| `GET /team-join-requests`、`GET .../join-requests` | 本人近 30 天的申请；Owner 或管理员查看待处理且未过期的申请 |
| `POST .../join-requests/{id}/decision` | 批准或拒绝，批准前确认申请人当前准入 |
| `GET .../audit-events` | Owner 分页查看审计 |

环境 API 的改动：

| API | 变化 |
| --- | --- |
| `GET /environments` | 同时返回个人环境和本人所在活跃团队的环境，每项带 `team`（`{id, name}` 或 `null`）和 `can_edit` |
| `POST /environments`、`POST /environments/{id}/copy` | 可带 `team_id`，仅 Owner 或管理员；复制不带则复制给本人 |
| `PATCH`、`DELETE /environments/{id}`，恢复版本，重建或删除快照 | 团队环境仅 Owner 或管理员，其他成员 403 |
| 会话的环境视图，终端、快捷操作、Fork、重新准备、更新运行期变量 | 视图带 `team`；离队后交出变量值的接口返回 409 |
| worker claim | 离队或 Team 删除后返回 403 `team_environment_revoked` |

非成员按 ID 访问团队环境一律 404，不区分"不存在"和"无权访问"。

错误统一为 `{"error": {"type": …, "message": …}}`：

- 404 `not_found`：非成员或跨 scope 的请求。403 `forbidden`：已知 Team 内角色不足。
- 409 带稳定类型：`revision_conflict`、`owner_cannot_leave`、`team_full`、`request_pending`、`applicant_not_admitted`、`no_longer_member` 等。
- 410 `invitation_unavailable`：邀请链接失效，不区分不存在、过期、撤销或被他人领取。
- Gateway 无法回答时返回 503，不拿过期的肯定结果兜底。
- 所有团队响应 `Cache-Control: no-store`。格式错误的请求返回通用 422，不回显请求内容，避免把输错的链接写进错误信息。

## 9 前端

- 设置里有"团队"页（建队、邀请、批准、移除、转让、退出、删除）和邀请链接打开的 `/teams/join` 加入页。
- 设置 → 环境：团队环境带"团队 · 名称"标记。不可修改的显示"查看"而不是"编辑"，菜单只有复制和设为默认。Owner 或管理员新建时可选归属，复制时可选目标团队。
- 环境对话框对团队环境说明由哪个团队共享，以及 Secret 对全队可读、个人全局变量只在运行期生效。不能修改时只读打开，可看、可测试、可读历史，不能保存或恢复。
- 新建任务的环境选择里，团队环境带标记，"有效配置"不列出不会生效的仅安装期个人变量。会话的环境页显示所用环境属于哪个团队。

按钮隐藏只是体验，服务端仍校验每个动作。团队环境的修改不推送给其他成员，他们重新打开列表时读到最新版本，已有会话固定在创建时的版本上。

## 10 将来接入平台统一 Team

目前 Team 成员由 Cloud Agent 数据库管理，Gateway 管用户身份、`agent.use`、模型权限和推理额度。不在 Gateway 加团队计费字段，也不让两个服务各自决定同一份 membership。

将来如果 Gateway 提供统一 Team 服务，通过明确的 HTTP 契约引入 `(issuer, external_team_id)` 映射，保留现有资源的稳定 Team ID。迁移时冻结本地成员写入，迁移并核对成员及 generation，切换授权权威，再把本地表降为受控镜像。不用长期双写或两个独立的 Owner 字段维持一致。团队计费可在此后独立设计，拥有团队资源不自动意味着 Owner 为所有人付费。

## 11 代码位置

| 位置 | 内容 |
| --- | --- |
| `cloud_agent/schemas/teams.py`、`api/teams.py` | Team、邀请和成员 API，请求校验与安全响应 |
| `cloud_agent/domain/teams.py`、`application/teams.py` | 词表、上限、拒绝类型；token、Gateway 准入检查、显示资料 |
| `cloud_agent/ports/teams.py`、`storage/repositories/teams.py` | 成员关系、邀请消费、Owner 不变量、审计 |
| `storage/repositories/environments/`、`api/environments.py` | 团队环境的访问、编辑权、审计、默认值、绑定、离队栅栏 |
| `storage/repositories/environment_snapshots.py` | 团队快照共用和删除 |
| `application/turn_claim.py` | claim 时的离队栅栏 |

迁移见 [0079_teams](../../../apps/backend/migrations/versions/0079_teams.py) 和 [0082_team_environments](../../../apps/backend/migrations/versions/0082_team_environments.py)。身份与资源权限的分工见 [Owner 鉴权](../../../apps/backend/cloud_agent/api/_owner_auth.py)、[仓库 entitlement](../../../apps/backend/cloud_agent/source_control/entitlement.py) 和[身份 namespace](../../../apps/backend/cloud_agent/deployment/identity_namespace.py)。相关设计：[环境配置](2026-10-12-repository-environment-design.zh-CN.md)、[环境安装快照](2026-10-15-environment-install-snapshots-design.zh-CN.md)。

测试：`tests/integration/storage/test_team_store.py`、`test_team_environment_store.py`，`tests/servers/test_teams_api.py`、`test_environments_api.py`、`test_environment_snapshots_api.py`、`test_grant_at_claim.py`，以及前端 `components/features/teams/*.test.tsx` 和 `components/features/agents/environment/*.test.tsx`。
