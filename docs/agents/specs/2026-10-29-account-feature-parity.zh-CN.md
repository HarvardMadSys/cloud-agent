# 本地账号的功能范围（与 HybridInference 账号对齐）

状态：已实现。取代更早的本地登录身份设计里的分期和排除决定；那些设计中的访问视图和限流分桶仍然有效。Gateway 模式已移除（[决定](2026-10-30-independent-accounts-only.md)）。账号流程见[账号与 waiting list 设计](2026-10-27-independent-accounts-and-waitlist-design.zh-CN.md)。

## 范围

HybridInference 账号身份与管理的全部能力，都复制或改编到本地实现：

- 注册与验证：注册、验证、找回和修改密码、ToS、captcha、注册策略。
- 登录：邮箱、登录名、手机号。
- 身份绑定：邮箱和手机号。
- 账号生命周期：首次部署建管理员，删号（自助和管理员），软删、恢复、彻底擦除。
- 会话与设备：同浏览器多账号，登出和会话撤销，已识别设备，登录事件。
- 管理员：账号管理（搜索、排序、计数、备注、全部状态动作），审批通知邮件，面向账号的群发和定时邮件。
- 设置：账号相关的运行时设置、可插拔的邮件和短信供应商、账号界面语言。

推理 API key 和套餐额度留在上游模型服务，见末尾「不实现的部分」。

边界不变：不共享 Gateway 数据库、token、密钥或供应商凭据，不 import `serving.*`，不引入共享包；沙箱网络和凭据边界不变。

## 关键决定

1. **身份与准入分开。** 上游的 `pending_approval`、`rejected` 是账号状态，Agent 用独立的准入（waiting list）状态表达。上游的「自动批准」在 Agent 里是身份验证完成时由策略执行 `approve`，审计 actor 记为 `signup-policy`。待审账号可以登录、查看申请状态。
2. **访问视图，缺一行即无权。** 身份拆成 `agent_local_login_identities`（email、login_name、phone 各一行）和可选的 `agent_local_password_credentials`。视图 `agent_local_account_access` 用 `LEFT JOIN` 加 `coalesce` 构造，所有 SQL 闸门都读它。
3. **已证明的邮箱。** 每个身份记录 `verified_by`：`link`、`code`、`operator` 或 `policy`。只有 `link` 和 `operator` 算已证明。管理员邮箱名单提权和注册域名自动批准都要求已证明的邮箱。
4. **账号配置是管理员设置。** 在 `Admin → Settings → Accounts`，存数据库，首次从环境变量导入，密钥只写。设置清单在 `accounts/settings_registry.py`，有测试保证每项都在设置页的分组里。
5. **删号是软删加终止栅栏。** `deleted` 状态在同一事务里撤销会话、移出所有浏览器、作废令牌和手机验证码、撤销准入、写审计和终止记录。彻底擦除另需三个前置：工作已停、主机已清理、共享团队已移交。擦除后保留墓碑，墓碑同时是写入栅栏。
6. **多账号就是浏览器会话**，每个浏览器最多 5 个账号。前端每个请求带 `X-Agent-Account`，和浏览器当前账号不符就返回 409 `account_switched`。已打开的流每 30 秒复查一次。
7. **已识别设备**沿用 HMAC cookie。密钥由 `AGENT_LOCAL_SESSION_SECRET` 经 HKDF 派生，cookie 带 `__Host-` 前缀，`Path=/`。
8. **手机号**支持注册、登录、绑定（仅限尚未绑定时）和无密码账号的本人确认。短信供应商是接口加腾讯云实现，默认关闭。不能解绑或更换手机号，与上游一致。
9. **邮件供应商**是 `smtp`、`tencent_ses` 或注册进来的传输方式，每封邮件发送时重新读设置。SES 发不出的邮件在入队前就拒绝。
10. **Cookie 固定用 `__Host-` 名**（要求 Secure、`Path=/`、不设 Domain），比上游可配置的 `samesite`、`secure`、`domain` 更严。会话有效期是设置 `accounts.session_ttl_days`。Agent 用服务器端会话，没有 access token 和 refresh 轮换。

## 功能

| 领域 | 行为 |
|---|---|
| 注册 | `POST /v1/auth/signup`。邮箱加密码，`use_case` 随申请提交。必须同意 ToS（记录 `tos_accepted_at`，`terms_url` 或 `terms_text` 为空时仍要求勾选）。Turnstile 用于注册和每次发验证码，每个 token 只用一次，未配置密钥时不校验。拒绝一次性和保留域名。设置 `signup_enabled` 同时控制邮箱和手机注册 |
| 注册策略 | 域名允许名单（精确或 `*.` 通配）和自动批准，只认已证明的邮箱（`/v1/admin/accounts/signup-domains`）。`require_email_verification` 关闭时邮箱被接受但未证明（`policy`）。注册 IP 限流（每小时 5 次、每天 10 次）；来源无法解析时共享额度或返回 503。新注册可通知管理员，每个申请只通知一次 |
| 防枚举 | 用已注册的邮箱再注册，统一返回 `{ok:true}` 并向该地址发重置链接，不改账号状态 |
| 邮箱验证 | `POST /v1/auth/verify-email`，链接 24 小时有效、单次使用，已用链接直接拒绝。`POST /v1/auth/resend-verification` 不泄露账号是否存在。验证完成后按策略自动批准 |
| 登录 | `POST /v1/auth/login`：`identifier` 不含 `@` 时按登录名处理。手机号用 `POST /v1/auth/phone/login`。停用账号返回 403 `account_suspended`，消息是管理员填的说明。限流有三个桶，先通过地址桶才计入账号桶，已识别设备另有独立的桶。登录事件写入 `agent_local_login_events`，管理员可按天数或账号清理（需确认） |
| 会话 | `POST /v1/session/logout` 登出当前账号。`POST /v1/auth/logout-all` 登出本浏览器全部账号。`/v1/auth/accounts` 和 `/switch` 列出和切换。改密、重置、停用、删号都会撤销会话 |
| 密码 | 忘记和重置密码。已登录时 `POST /v1/auth/account/password` 改密码。无密码账号用验证码设置首个密码。改密发通知邮件 |
| 绑定邮箱 | `POST /v1/auth/account/email`：先确认本人再查重，点击链接后才生效，通知旧地址，作废旧的重置链接。没有 SMTP 时返回 503 |
| 绑定手机 | `POST /v1/auth/account/phone`，验证码只存 keyed hash，有尝试上限，先预留后发送，不泄露号码是否已注册 |
| 删号 | `POST /v1/auth/account/delete`：输入标识并证明本人，管理员不能删自己。管理员动作 `delete`、`resume`（清理确认后才能执行，已擦除的账号不能恢复）、`POST /{id}/erase`（按外键遍历删除该 owner 的全部数据，不碰其他 owner 的行；要求主机已清理、共享团队已移交）。已删除 owner 的团队用 `GET /{id}/teams` 和 `POST /{id}/teams/{team}/transfer` 移交 |
| 管理员 | `GET /v1/admin/accounts`：搜索（`?q=`）、过滤、分页、按创建或最近登录排序、状态计数。九个带版本号的动作：批准、拒绝、停用、恢复、授予或撤销管理员等。备注和停用说明。审计覆盖全部动作。批准和拒绝发结果邮件 |
| 管理员名单 | 设置 `admin_emails`：登录时自动提权，只认已证明的邮箱，名单优先；撤销会被名单重新授予时，拒绝该撤销 |
| 群发邮件 | `/v1/admin/account-mail`：预览、测试、创建或定时、取消、收件人明细。收件人在创建时固定，认领带令牌，每个收件人逐条记录。只发已验证地址，Markdown 不渲染原始 HTML，已删除的账号不能作为受众 |
| 首次部署 | 一次性 setup code 创建登录名管理员（`POST /v1/auth/setup/admin`，启动日志写明 `/setup/administrator`）。运维 CLI `bootstrap_admin --create-email/--create-login-name/--member/--approve` 无需 SMTP，记为 `operator`。`GET /v1/auth/options` 给登录页和注册页提供公开配置 |
| 语言 | `PUT /v1/auth/account/locale` 保存账号界面语言，以最新选择为准。未登录时的选择带 `chosen_at`；浏览器只把未登录或同一账号的选择带入账号 |

## 有意偏离上游

| 偏离 | 理由 |
|---|---|
| 待审账号可以登录，查看申请状态 | 准入独立于身份 |
| 已用过的验证链接直接拒绝，不报告状态 | 不向持有旧链接的人透露账号信息 |
| 限流计数持久化在 Postgres，令牌放在 URL fragment | 多副本共享计数，令牌不进访问日志 |
| `require_email_verification=false` 时，旧的未验证账号仍需验证 | fail-closed |
| 关闭验证期间接受的邮箱不被管理员名单和域名名单信任，直到证明为止。重置密码即收回账号 | 上游在这里能被抢注提权。收回时无法区分抢注者和本人，所以清除注册资料、撤回待审申请，并在重置页和邮件里告知；已作出的审批决定和账号里已有的 Agent 数据保留 |
| 擦除保留墓碑，共享团队先移交 | 不误删其他成员的数据 |
| 沙箱删除归档留在存储桶，按保留策略处理 | 控制平面无权删除这些对象 |
| 管理页的中文只覆盖新增的账号管理页面 | 控制台其余部分仍是英文 |

## 不实现的部分

推理 API 和计费留在上游模型服务。Agent 的推理走用户自己的模型连接，计费在供应商侧。

| 上游能力 | Agent 的边界 |
|---|---|
| 角色 `free/pro/internal/admin`，模型可见性、额度、并发 | 只有 `agent.use` 和 `agent.admin` 两级准入。管理员不能自降、自停、自删 |
| 每用户日额度、月额度，`max_concurrent_requests`，`disabled_models` | 额度归供应商，并发由 Agent 调度器配置，模型来自用户连接 |
| API key 的生成、列表、删除、重生成 | Agent 没有公共推理 API。模型连接密钥在停用和删号时随终止栅栏失效，擦除时被删除 |
| 用量、成本、最近请求、自动化评分，API-key 认证失败封禁，`llm-prober-layout` 偏好 | 不属于账号功能，Agent 没有对应物 |
| 发行版禁止注册（`distribution.features.public_signup`） | Agent 没有发行版概念 |
