# Agent 自己开 PR：GitHub 中继与用户署名

状态：已实现。

相关：[公网出口](2026-09-23-agent-public-egress-design.zh-CN.md)、[sandbox 安全模型](2026-09-01-agent-sandbox-security-model.zh.md)。

## 1. 决定

云端 agent 像本地 Claude Code 一样工作：用户在对话里说「开个 PR」，agent 自己 commit、按仓库规则跑检查、`git push`、开 PR。标题、正文和 commit message 都由 agent 写。

- **PR 和 commit 属于用户本人。** commit 作者是用户（GitHub noreply 邮箱），agent 在 commit message 末尾写 `Co-Authored-By: <GitHub App 的 bot>`。push 和 API 调用以用户本人的 GitHub 身份发出。
- **真正的 GitHub 凭据不进 sandbox。** sandbox 只拿到对话级的占位凭据。git 和 `gh` 的 GitHub 流量经过平台的 GitHub 中继。中继校验占位凭据，检查仓库和操作，再换成用户的 GitHub App user token 转发给 GitHub。这和 MCP relay 是同一种结构。
- **平台不替 agent 开 PR。** 没有快照捕获、补丁闸门、发布分支、Create PR / Update PR 按钮，sandbox 里也没有 `GH_TOKEN` 形式的真实 token。

对 agent 的信任和本地一样：都是用户发起、替用户干活，都会读外部内容，都可能无人看管地跑。不同的是谁保管凭据。本地的 token 在用户自己的 keyring 里，云端的平台替所有用户保管，sandbox 跑在共享的宿主机上。GitHub 的 user token 不能限定到单个仓库，所以绝不能进 sandbox。中继让平台既用用户本人的身份，又把仓库范围的检查放在 sandbox 够不着的地方。

| 被攻破的是 | 后果 |
|---|---|
| 单个 sandbox | 一个占位凭据，离开这个对话的租约就没用 |
| 宿主机 | 占位凭据。真正的 token 不在宿主机上 |
| 控制面 | 所有用户的 user token 和 App 私钥（它们本来就在这里） |

## 2. GitHub 中继

### 2.1 位置和路径

```
sandbox 里的 git / gh / curl
  │  https://github.com/...、https://api.github.com/...
  │  （/etc/hosts 把这两个名字指向组桥上的 github-tunnel）
  ▼
github-tunnel（宿主机，组桥上的 relay 别名，TCP 443）
  │  ssh -L，独立密钥，permitopen 只到控制面的 GitHub 中继端口
  ▼
github-relay（控制面，只含 GitHub 中继路由的 listener）
  │  TLS 在这里终止，证书由部署自己的 CA 签发，名字限定为 github.com 和 api.github.com
  │  校验占位凭据 → 检查仓库和操作 → 换成用户 token
  ▼
github.com / api.github.com
```

- **独立 listener。** `create_github_relay_app` 和 `create_mcp_app` 用同一个镜像和数据库，但只含 GitHub 中继路由。它不进公网边缘，也不和 MCP relay 共用端口。MCP relay 的 token 在这里不能用，反过来也一样。
- **独立隧道。** 宿主机上的 `github-tunnel` 容器有自己的 SSH 密钥和 `permitopen`，挂在 `agent-relays` 网络上，由现有的 relay 别名机制接到每个组桥，端口 443 进入组桥的 `relays` 集合。一把能开两条隧道的钥匙会让隔离形同虚设，所以每条隧道一把。
- **名字解析。** `github-tunnel` 带着 `github.com` 和 `api.github.com` 两个别名。runc 下 Docker 自带的 DNS 直接回答别名。Kata guest 里由可信 helper 维护的 `/etc/hosts` 块写入同样的地址。其他 GitHub 域名（`codeload`、`raw.githubusercontent.com`、`objects.githubusercontent.com` 等）不变，仍直连公网。
- **CA 信任。** 部署生成一个只允许签 `github.com` 和 `api.github.com` 的 CA（X.509 name constraints），私钥只在控制面。网桥把这两个名字解析到中继，对 sandbox 里的每个进程都是如此：有占位凭据的成员、临时工作区里没有凭据的成员、环境的 setup、services、verify 脚本、action 和终端。所以信任这个 CA 不能和「持有 GitHub 凭据」绑在一起，否则 setup 脚本 `git clone` 一个公开仓库就会 TLS 失败。容器里第一个进程启动前，宿主机以 root 在 `/etc/freeinference/` 写入这个 CA 和一份合并后的信任库（镜像的系统根证书加这个 CA），root 所有、`0444`，成员、脚本和终端都改不了。每个进程带上 `SSL_CERT_FILE`、`GIT_SSL_CAINFO`、`CURL_CA_BUNDLE`、`REQUESTS_CA_BUNDLE`、`NODE_EXTRA_CA_CERTS`，使 git、gh、curl、Node 和 Python 都信任它。进程自己设置了同名变量时以它自己的为准。镜像的信任库不变。

hosts 覆盖只是方便，不是安全边界。agent 可以绕开 hosts 直接用 GitHub 的真实 IP（出口本来就允许公网 443），但 sandbox 里没有任何真正的 GitHub 凭据，绕过去只能匿名访问 GitHub。安全边界是「真 token 只在中继里」。

### 2.2 占位凭据

- 每个成员建立 residency 时得到一个 GitHub 中继 token。它和 MCP 的 `amr.` token 同一套 HMAC 格式（`tokens.py`），scope 是 `github-residency`，两者互不通用。
- 它通过 credential helper 交给 git，通过 `GH_TOKEN` 交给 `gh`。它只对中继有意义，直接发给 GitHub 会被当作无效 token 拒绝。
- 每次请求都按 MCP relay 的方式验证（`resolve_residency_attempt` 加 fence 复查）：residency 代数一致、状态 hot、租约未过期，对话没删，当前 attempt 在运行，宿主机还在册。用户身份从 job 读，不从请求里读。
- 撤销是隐式的：Stop、取消、attempt 被替换、residency 轮换、宿主机移除，token 立即失效。

### 2.3 用户 token

- 中继使用 `agent_github_accounts` 里该用户的 GitHub App user token（8 小时有效，6 个月的 refresh token）。刷新复用 `github_user_token.user_access_token` 的租约和代数 fence。控制面刷新，所以长对话不会遇到 token 一小时过期。
- 用户没连接 GitHub，或 refresh token 失效时，中继返回 403，提示用户去连接或重新授权。对仓库有没有 push 权限由 GitHub 自己判断。GitHub 拒绝 token 本身（401）时，中继删掉存着的 token，让用户重新连接。

### 2.4 允许什么

只有指向这个对话的仓库、且在允许列表里的请求才带上用户 token。其他请求去掉 `Authorization` 后匿名转发，所以 agent 仍能读公开仓库和页面，只是不带任何人的身份。

**git smart HTTP**（`github.com/<owner>/<repo>(.git)/...`）：

- `info/refs`、`git-upload-pack`：允许（fetch、pull）。
- `git-receive-pack`：中继先解析 pkt-line 的引用更新命令，再转发数据包。只允许 `refs/heads/*`，拒绝删除分支、推 tag 和其他引用。允许 force push，因为 agent 改写自己的分支（rebase）是正常操作。推到哪个分支由 GitHub 的分支保护决定，和本地一样。

**REST**（`api.github.com`）：

- 允许 `/repos/<owner>/<repo>/...` 下的读请求，以及 PR、issue 评论、review、review 评论、check、status、Actions 运行记录和合并 PR 的写请求。
- 拒绝仓库管理类接口：设置、webhook、协作者、部署密钥、分支保护、Actions secrets 和变量、删除仓库、转移仓库。
- 允许 `GET /user` 和 `GET /rate_limit`（`gh` 用它们确认身份）。

**GraphQL**（`api.github.com/graphql`）：拒绝，返回 403 并提示改用 REST（`gh api repos/{owner}/{repo}/...`）。GraphQL 可以用 node ID 访问任意仓库，按仓库限制它需要解析查询并逐个核对 node ID。代价是 `gh pr create`、`gh pr view`、`gh pr checks`、`gh pr merge` 这类基于 GraphQL 的子命令不能用，平台说明（第 4 节）告诉 agent 用对应的 `gh api` REST 调用。

### 2.5 PR 记录

中继看到 `POST /repos/<owner>/<repo>/pulls` 成功返回时，把这个 PR 记到 `agent_thread_pull_requests`（对话、轮次、仓库、编号、URL、head 和 base 分支、创建时间）。界面上的 PR 链接、done 状态和 Watch PR 都从这里取。job 的 `published_pr_url` 是这一轮开的 PR，`workspace_pr_url` 是对话最新的 PR。Fork 不继承原对话的 PR 记录。用户自己在 GitHub 上开的 PR 不在记录里，需要时把链接贴进对话。

## 3. sandbox 里的配置

- **git remote。** 工作区加上不带凭据的 `origin = https://github.com/<owner>/<repo>.git`，agent 用平常的 `git push origin <分支>`、`git fetch origin`。`origin` 在新建工作区时记录，更早创建、一直保留的工作区没有它，平台说明告诉 agent 缺了就自己加。
- **凭据和变量。** credential helper 用只在调用时生效的 `GIT_CONFIG_*` 变量配置，不写进共享的 `.git`。`GH_TOKEN` 是中继占位凭据，`GH_REPO` 是对话的仓库。
- **commit 署名。**
  - `GIT_AUTHOR_*` 和 `GIT_COMMITTER_*` 设成用户的 GitHub 名字和 noreply 邮箱 `<id>+<login>@users.noreply.github.com`，来自 `agent_github_accounts`。
  - commit message 末尾写 `Co-Authored-By: <bot 名字> <bot 邮箱>`。bot 邮箱是 `<bot 用户 id>+<app-slug>[bot]@users.noreply.github.com`，GitHub 能把它对应到 App 的头像。Claude 运行时用成员私有配置里的 `attribution` 设置自动加。其他四个运行时由平台说明要求 agent 自己加。
- **签名 commit。** sandbox 里没有用户的签名密钥。要求签名 commit 的分支保护会拒绝这些 push，这是已知限制。
- **CI 文件。** push 修改 `.github/workflows/` 需要 App 的 `workflows` 权限，目前不申请，这类 push 会被 GitHub 拒绝。

## 4. 给 agent 的平台说明

`cloud_agent_host/platform_instructions.py` 给 agent 一段简短说明，只在成员有仓库和中继占位凭据时才给。内容是 agent 自己发现不了的事实：

- 这个工作区的 `origin` 是哪个仓库，`git push` 和 `gh api` 以用户本人的身份生效，只能写这个仓库。
- commit 作者已经设好，不要改。commit message 末尾加 `Co-Authored-By` 那一行。
- `gh` 的 GraphQL 子命令不可用，附上开 PR、看 PR、看检查、合并分别用的 `gh api` REST 调用。

说明不规定工作方式（要不要开 PR、分支怎么建、PR 怎么写），这些由用户和仓库自己决定，和同一个 CLI 在用户本机上一样。

仓库自己的设置不会自动生效。Claude 运行时用 `--setting-sources user` 启动，共享工作区里仓库的 project 和 local 设置被当作不可信的启动输入。所以仓库的 `.claude/settings.json` hook 和仓库的 CLAUDE.md 都不会自动加载，agent 不一定会去读。这是已接受的代价。

五个运行时都拿到说明，注入方式由 `with_platform_instructions` 统一处理：

- Claude Code 和 pi：`--append-system-prompt`。
- Codex：在 `app-server` 前用 `-c` 覆盖 `developer_instructions`。
- OpenCode 和 Kilo：`deploy/sandbox/` 里的 opencode 和 kilo 包装脚本从启动器设置的 `…_INSTRUCTIONS` 环境变量读出说明（确切名字见 `http_bridge/member.py`），写成文件，加进配置里的 `instructions` 文件列表。

## 5. 接受的限制

- **推送不再经过平台的检查。** 没有 `.github/` 检查，也没有密钥扫描。改 workflow 被 App 缺少 `workflows` 权限挡住，密钥由 GitHub 的 push protection 和仓库 CI 兜底。中继以后可以在 receive-pack 里扫描用户已知的密钥（模型 key、环境 Secrets），目前不做。
- **发布权在对话本身。** 用户说开就开，没有「只有主人点了才发布」的步骤。
- **没连接 GitHub 的用户不能 push。** 部署白名单里的公开仓库仍可匿名克隆并跑任务。
