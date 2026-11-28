# Agent CLI 一键更新

状态：已实现。运维入口见 `ENVIRONMENT.md` 中的 `AGENT_RUNTIME_*` 设置。

## 目标

管理员在 Admin → Settings → Agent versions 点一下，就能把 Claude Code、Codex、OpenCode 或 pi 更新到 npm 上的最新版本，或换成 npm 上任意一个正式版本（包括更老的），不用改代码、重建镜像或重新部署。

此前 CLI 版本打包在沙箱镜像里，默认版本同时写在 `deploy/sandbox/Dockerfile.agent-sandbox` 和 `cloud_agent_shared/shared_protocol.py`，换版本都要重建镜像。现在每台主机在运行时自己下载所需版本。代价是生产环境运行的 CLI 版本不再完全由发布的镜像决定。

## 产品决定

- 支持 Claude Code、Codex、OpenCode、pi。Kilo 不支持：我们运行的是带隔离补丁的源码构建，npm 上的官方包没有这个补丁。Kilo 只能选镜像自带的版本。
- 所有在线主机都下载好并自测通过后，新对话才切到新版本。已有对话一直用它开始时的版本。
- 一键回退到更新前新对话所用的版本。
- 版本下拉框列出 npm 上的所有正式版本，可升可降，降级也要先通过同样的自测。不列出预发布版（版本号带 `-`，如 `0.162.0-alpha.1`）和 npm 标了废弃的版本。例外：npm 的 latest 本身被标废弃时，说明废弃的是整个包，此时照常列出所有版本并显示 npm 的说明。
- 状态只有"下载中"（小恐龙进度条）和"已完成更新"。失败时显示哪台主机、什么原因和"重试"按钮；切换还没发生时页面注明新对话仍用原版本。

## 界面行为

每个可更新的 agent 显示：有新版本时 "Update to X"；下载中的进度条（所有在线主机进度的平均值，新对话真正切换后才到 100%）；完成后 "Updated to X" 和 "Roll back to …"；已最新时 "Up to date"；查不到 npm 时显示原因且不提供更新。

版本下拉框分 **On servers**（所有服务器上已有）和 **Download from npm**（其余正式版本，新的在前）两组：

- 选每台服务器都有的版本：按钮是 "Save"，立即生效。
- 选有服务器还没有的版本：按钮是 "Download and switch"，写明有几台要先下载并自测，之后走与 Update 相同的流程。
- npm 版本列表读不到时，仍列出服务器上已有的版本并显示原因。

手动选择的版本会取代进行中或已完成的更新，同样下发给每台主机：后加入或丢了下载的主机也会去下载。有主机下载失败时显示 "Some servers could not download …" 和重试按钮。

版本列表来自 npm 的精简包文档（`application/vnd.npm.install-v1+json`），opencode 的约 15 MB，所以用单独的 `GET /admin/agent/runtime-upgrades/{runtime}/versions` 读取，不放进页面的轮询。控制面每个包每小时最多读一次，失败只缓存 1 分钟。

## 流程

```text
管理员点 Update ─► POST /admin/agent/runtime-upgrades
                   校验版本在 registry 上存在，记录目标：
                   {version, before, nonce, previous_version, previous_download}
                   每次心跳回复都带上要保留的版本（每个 runtime 最多两个）
                          ▼
每台主机的 runner（RuntimeDownloads 一次处理一个版本）
   1 下载   npm install --ignore-scripts --before <before>（联网，只运行 npm 本身）
   2 装脚本 npm rebuild（断网），再检查 --version 完全一致；
            把装好的目录复制到一个新的沙箱镜像容器上，只提交这一层
   3 自测   断网容器里用生产驱动对接回环地址上的假模型跑一轮对话
   4 保留   通过后才打正式标签；主机上报 {state, progress, error}
                          ▼
控制面每次心跳后检查：每台在线、已注册的主机都提供该版本时，
把新对话切到它，并记录 activated_at
                          ▼
新对话首次启动成员时，把该版本复制进对话容器自己的写层
/opt/runtimes/<runtime>/<version>，并放到 PATH 最前
```

要点：

- 下载的版本存成主机本地的 Docker 镜像：`<namespace>-agent-runtime-<namespace 的 SHA-256 前 12 位>:<runtime>_<version>_<基础镜像 ID 前 12 位>`。runner、对话进程和 Sandbox Manager（在另一个容器里）唯一共享的是 Docker daemon，所以不需要新的主机挂载。摘要使不同 namespace 共用一个 daemon 时各用各的仓库，避免互相挪走标签。旧名字 `<namespace>-agent-runtime` 下的版本仍可用，新的下载用新名字（`runtime_images.py`）。
- 复制进对话而不是挂载进去，因为对话状态必须全在容器写层里（`_verify_mounts` 拒绝任何挂载）。这样对话归档、恢复或迁移时 CLI 跟着走，删除主机上的镜像也不影响已有对话（`runtime_install.py`）。只有整棵目录都归 root 且只有 root 能写时才启用，成员改不了别人要运行的 CLI。目录写完后才写标记文件，复制到一半的不当作完整。
- 新沙箱镜像发布后，主机把每个目标重新下载、重新自测，因为之前的自测基于旧镜像的 wrapper 和 Node。期间旧镜像上的那份仍可用，通过后才删旧的。重新自测失败时页面显示"更新失败"，管理员可回退。
- `--before` 把 npm 的依赖解析固定在管理员点击的那一刻，所以一周后才下载的主机也装出同一棵依赖树。
- 每台主机每个 runtime 最多保留两个版本：更新的目标版本，和它替换掉的版本。后者在切换前是新对话还在用的版本，切换后是回退目标，所以回退在每台主机上即时生效。切换只等目标版本，进度也不算后者。下发时新对话所用的版本排前面。回退之后只下发回退到的版本，回退前用的那个在还有对话或检查点用它的主机上保留，其余主机在不再需要十分钟后删除。
- 重试（对同一版本再点一次）只换新的 nonce，`before`、回退目标和是否已切换都保持不变，让重试的主机解析出同一棵依赖树。回退是手动选择更新前的版本，沿用它第一次下载时的 `before`。

## 安全边界

- 只有下载这一步联网，且只运行 npm 本身（属于已审核的镜像），关闭所有包脚本。npm 用 registry 提供的完整性哈希校验每个包。下载网络默认是 Docker 的 `bridge`，可用 `AGENT_RUNTIME_DOWNLOAD_NETWORK` 更换。
- 包的安装脚本在断网环境里运行。所有安装容器去掉全部 Linux capability，设置 `no-new-privileges`，限制进程数。这比沙箱镜像的 `docker build`（root、联网）更严格。
- 自测在断网容器里以成员身份运行，假模型只监听容器内的 127.0.0.1。安装和自测用 runc 而不用 Kata，信任模型与镜像构建相同：运行的是厂商发布的包，不是用户代码。
- 不能安装任意包。包名来自代码里固定的目录（`runtime_catalog.py` 的 `npm_package`），控制面只接受精确版本号，并先确认 registry 上确有该版本。
- 沙箱网络边界、凭据和挂载规则不变，对话容器没有新增挂载、网络或权限。

## 版本记录与协议

- 每次 attempt 照旧记录 `runtime_version`。主机上报的可用版本包含已下载的版本，claim 时的回退规则（`resolve_runtime_version`）不变。
- 共享 sandbox 协议里固定的默认版本（`SHARED_SANDBOX_RUNTIME_PINS`）不变。新版本通过管理员的选择生效，所以 `shared-sandbox-v1` 的认证不受影响。没有选择时，主机运行镜像默认版本。

## 存储与接口

- 迁移 `0075_runtime_upgrades` 新增两列：`agent_runner_policy.runtime_upgrades`（JSON，每个 runtime 一条；`kind` 为 `update` 或 `selection`）和 `agent_runner_hosts.runtime_downloads`（JSON 数组，每台主机对每个保留版本的上报）。旧版本的控制面和主机忽略它们。
- 心跳请求带 `runtime_downloads`，读不懂的条目丢弃，不拒绝整个心跳。心跳回复带每台主机要保留的全部版本，含已完成更新的回退版本。
- `GET /admin/agent/runtime-upgrades`：每个 agent 当前给新对话用的版本、npm 最新版本、更新状态和进度。`POST` 发起更新，写审计 `agent.runtime_version.upgrade`，版本可以是 registry 上任意精确的正式版本。`GET .../{runtime}/versions` 返回正式版本列表、整个包被废弃时的说明和读取失败原因。
- 回退复用 `PUT /admin/agent/runtime-versions`：手动选择的版本取代该 runtime 的更新；选回"默认"则清除记录。没有任何主机有所选版本时拒绝（404），对可下载的 agent 提示到 Settings 里更新，只有 Kilo 提示把版本加进 `*_EXTRA_VERSIONS` 并重新部署。
- registry 地址是 Admin 中的 **Agent package registry** 设置（`AGENT_RUNTIME_REGISTRY_URL` 只用于初次导入，空值为公共 npm）。控制面必须能访问 registry，否则页面提示无法检查更新。每台主机上报它下一次下载所用的 registry，在所有在线主机都用上新值之前，更新会被拒绝。详见 `ENVIRONMENT.md`。

## 代价与限制

- 每个新对话把 CLI 复制进自己的写层，计入对话的磁盘配额，解压后约 180–390 MB（Codex 最大，OpenCode 最小）。镜像自带的版本没有这项开销。每个已下载的版本在主机上占一层镜像，大小相近。
- 主机清理不再需要的版本（`runtime_downloads.py`）：
  - "需要"指最近一次心跳回复把它列为目标，或本部署在这台主机上的对话容器（任何状态）或检查点镜像用着它。
  - 只在收到过带目标列表的回复后才清理，旧控制面的回复不带列表，收到后停止清理。
  - 连续十分钟不被需要才删。新对话在 claim 时就定下版本，但容器创建出来才算在用，可能要一段时间。被点名一次，计时就重新开始。
  - 用不强制的 `docker rmi`，被容器占用的镜像 Docker 会拒绝，下次再试。删除前在锁内再看一次最新的目标列表。
  - 复制用的源容器带按部署区分的标签，每次清理先删掉超过一小时的（`docker container prune`，`until=1h`）。
  - 每次下载完成后清理。还有对话在用的版本每分钟检查一次，因为对话被删除时没有通知。
- 每个 runtime 上报的可用版本最多 64 个（`HOST_VERSION_MENU_MAX_ITEMS`），超过时控制面拒绝心跳和 claim，整台主机离开主机池，所以主机自己保证不超。超出时先去掉不被需要的版本（已下载的从最老的起，再是镜像自带的从最后一个起），被需要的最后才去，镜像自带的第一个（默认版本）始终保留。被去掉的版本这台主机不再提供：新对话选了它则在这台主机用默认版本；已有对话在沙箱镜像已换过时不能在指定位置分叉，也不能中途插话（`HOST_SHIPS_GROUP_CLI`）。
- 自测只证明一件事：一轮对话的输出生产驱动都读得懂，模型的回答出现在对话里。它覆盖不了 `drivers.py` 里照固定版本行为写的处理，如中途插话、后台子 agent、权限提示。所以先在 staging 更新，没问题再更新生产，有问题就回退。
- 所有在线主机都升级到含此功能的 runner，切换才能完成。

## pi 的包和驱动

pi 在 2026 年 5 月从 `@mariozechner/pi-coding-agent` 换到 `@earendil-works/pi-coding-agent`，旧包最后一个版本是 0.73.1。一键更新从新包下载（`runtime_catalog.PI`）。镜像默认版本仍是旧包的 0.73.1（`ARG PI_VERSION`），更新后可一键回退到它；改镜像默认版本时，安装命令一起换成新包名。

驱动按输出形状而不是版本号兼容新旧两种 pi：

- 自动重试：0.75.4 起，模型服务偶发出错时 pi 先结束这次运行（`agent_end` 带 `willRetry: true`）再自己重试。驱动不把这次结束当作轮次结束，以最后一次不再重试的运行为准。不用 `agent_settled` 判断结束，因为 0.75.4 到 0.80.3 有 `willRetry` 而没有它，等它会让这些版本的对话永不结束。
- 请求 id：0.84.0 起，`responseId` 只出现在 `message_end` 上。对新版，驱动先留着已完成的内容块，到 `message_end` 带上 id 再发出，轨迹检查器靠 id 归并同一次请求。
- 1.x 把自己的系统提示词当作 `system` 消息报出，还会多发 `agent_settled`，两者都不进原始日志。
- 0.79.0 起 pi 只给"受信任的项目"读取仓库里的 `.pi/settings.json`、`.pi/skills`、`.agents/skills`、`.pi/SYSTEM.md`，RPC 模式默认不读。为与 0.73.1 和其他 agent 一致，启动脚本对 0.79.0 及以上版本加 `--approve`（版本从 PATH 上 pi 的 `package.json` 读出，0.73.1 不认识该参数，不加）。这不影响隔离：`--no-extensions` 仍使仓库、设置文件和配置目录里的扩展都不加载，`.pi/mcp.json` 也不启动 MCP 服务器，模型只能用内置的 bash、read、edit、write 和我们的桥接扩展。镜像里的 `PI_OFFLINE=1` 使两个版本都不安装仓库设置里列出的 npm 包。
- 插件：0.75.0 起 `pi install` 装进 pi 配置目录下的 npm 项目。安装脚本在新位置找到包后，在原位置补一个链接，pi 的加载路径不变。
- 0.84.0 之前的 pi 把 `--version` 打印到 stderr，所以下载第二步的版本检查和自测一样，同时看 stdout 和 stderr。

测试：`tests/external/test_runtime_updates_live.py` 用真实 npm 和 CLI 跑完整流程，`tests/external/test_cli_inference_variables.py` 和 MCP 相关的 external 测试覆盖 pi 的新旧版本。

## 后续

- 给依赖具体版本行为的驱动逻辑补自测场景，如中途插话、权限提示。
- Kilo 的隔离补丁合入上游后，把 Kilo 加入一键更新。
- 把镜像里 pi 的默认版本改到新包的某个版本。
