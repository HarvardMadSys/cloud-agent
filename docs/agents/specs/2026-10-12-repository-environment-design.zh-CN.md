# 可复用云端环境配置

状态：已实现。后续扩展见 [环境安装快照](2026-10-15-environment-install-snapshots-design.zh-CN.md) 和 [Team 共享工作区](2026-10-15-team-shared-workspace-design.zh-CN.md)（团队环境）。

相关：[AGENTS.md](../../../AGENTS.md) §6–§7、[Agent 统一公网出口](2026-09-23-agent-public-egress-design.zh-CN.md)、[沙盒安全模型](2026-09-01-agent-sandbox-security-model.zh.md)、[原生会话与共享工作区](2026-08-31-native-session-fork-shared-workspace-design.md)。

## 1. 目标

用户为一个仓库配置依赖、变量、服务和常用命令，保存为一套云端环境。创建任务时选择它，平台准备好工作区后 agent 才开始工作。配置可重复使用，每个新工作区仍有独立的容器和文件。

一套环境包含：

- 普通变量和 Secret，可限定安装期、运行期或两者。
- 安装脚本（如 `npm ci`），在新工作区安装依赖。
- 启动脚本，启动 Redis 等常驻服务并等待就绪。
- 可选的验证命令，和若干具名的快捷操作（运行测试、检查类型、启动开发服务）。

用户还可以：为同一仓库保存多套环境并设默认；管理跨仓库复用的个人全局变量；用「测试环境」在独立临时容器里验证配置；查看历史并恢复旧版本；对已有会话显式更新运行期变量。

不包含：无仓库任务、用户自带 agent 账号、自定义镜像和资源档位、网络规则配置、仓库内自动发现的环境文件（只执行 owner 在平台保存的脚本，避免仓库更新悄悄改变执行内容）、文件型 Secret 挂载、清理脚本。

## 2. 概念与生效规则

**环境配置**属于一个 owner 和一个仓库，以不会复用的 `environment_id` 标识。**环境版本**是一次保存后的完整配置（变量、安装脚本、启动脚本、验证命令、快捷操作），保存产生新版本，旧版本不可变。每个 owner 另有一份带版本的**全局变量配置**，只含变量。团队环境的归属和权限见 Team 设计。

**工作区环境绑定**在新任务创建时确定，固定环境版本和全局变量版本，属于整个 sandbox group。后续消息、重试、goal 续跑、容器重启和共享工作区的 Fork 都使用同一个绑定。

修改已保存环境只影响之后创建的工作区。已有工作区可以点击"更新运行期变量"，从下一轮起使用最新的运行期快照，安装期输入和脚本保持原样。

| 用户动作 | 行为 |
| --- | --- |
| 新建任务并选择环境 | 固定用户所见的环境版本和全局版本；版本已变则返回 409 并提示刷新 |
| 新建任务未指定环境 | 用仓库默认环境；没有默认时用平台基础环境加个人全局变量 |
| 选择"平台基础环境" | 不用仓库环境的变量、脚本和操作，仍用个人全局变量 |
| 编辑、导入、改脚本、恢复历史 | 生成新版本，现有工作区不变 |
| "使用最新配置新建任务" | 创建独立工作区，不搬迁原生会话状态 |
| Fork 到相同工作区 | 继承绑定，不能换环境 |
| 删除环境 | 归档；从新任务选择中移除，已有工作区、历史和必要密文引用保留 |

同名变量按"个人全局 → 所选环境"解析，后者覆盖前者的整条定义（值、类型、阶段），再按阶段筛选。环境里把同名变量设为"仅安装期"，运行期不会重新露出全局值。

任务创建接口不再接受 `environment_overrides`：非空列表返回 422，空列表视为未提供。早期绑定保存的覆盖仍会读取、注入，并参与脱敏和发布扫描。

### 生命周期

| 配置项 | 运行时机 | 变量 | 成功条件 |
| --- | --- | --- | --- |
| 安装脚本 | 每个新工作区首次准备；同一容器重启不重复 | 安装期 | 退出码 0；默认超时 900 秒 |
| 启动脚本 | 安装后首次启动，之后每次容器从停止到运行 | 运行期 | 服务启动并就绪，退出码 0；默认超时 120 秒 |
| 验证命令 | 用户点击测试环境，安装和启动成功后 | 运行期 | 退出码 0；默认超时 300 秒；可不填 |
| 快捷操作 | 用户手动运行 | 运行期 | 命令退出、被停止或终端结束 |
| Agent、子进程、终端 | 环境准备成功后 | 运行期 | 沿用现有机制 |

运行中的容器收到新消息、替换 CLI 进程或增加 Fork 成员时，不重复安装或启动。安装和启动都为空时沿用基础环境。

可用阶段和 Secret 类型是两个独立属性。变量可选 `setup`、`runtime` 或 `both`，默认 `both`。仅安装期的 Secret 不注入运行期进程，但安装代码仍可能把它写进文件，这不是隔离仓库代码的保密保证。

模型、runtime、reasoning effort、skills、MCP 和 goal 仍由任务配置管理，网络、镜像和资源由平台管理。

## 3. 用户体验

入口：

- **Settings → 环境**：个人全局变量，以及按仓库分组的环境列表（名称、是否默认、版本、变量数、最近测试结果）。
- **新建任务**：选定仓库后显示环境选择器，自动选中仓库默认环境，可查看有效变量的名称、来源、类型和阶段。第一次使用时也能在提交前完成配置。选择器加载失败时任务仍可提交，省略环境字段，由服务端应用默认环境和全局变量，并显示回退说明。
- **右侧 Environment tab**（也可从"…"菜单进入）：当前工作区实际绑定的配置、安装和启动状态、快捷操作。绑定版本与最新版本不同时明确标注，不把最新配置冒充当前配置。

新建的第一套环境自动成为仓库默认，之后新建或复制的不替换默认。

编辑器包含：名称与仓库（仓库创建后不能改，复制时可选本人有权限的另一个仓库）；变量表（Secret 只显示"已设置"，不显示片段；Secret 改成普通变量必须重新输入值；全局变量只读，可"在此环境覆盖"）；安装脚本（可基于锁文件给建议，如 `package-lock.json` → `npm ci`、`uv.lock` → `uv sync --frozen`，多包管理器或 monorepo 不擅自选，不覆盖已有脚本）；启动脚本（说明须启动常驻服务、等待就绪后退出）；验证命令；快捷操作（名称、命令、相对工作目录）；保存和"保存并测试"；平台环境说明（镜像、资源、网络、超时，只读）。

### `.env` 导入

粘贴后先预览新增、覆盖、重复和被拒绝的变量名与原因。默认按 Secret、`both` 导入，逐项可改成普通变量。非空 Secret 去除首尾空白后至少 8 个字符，所以 `DEBUG=1` 这样的值要用户明确选成普通变量，平台不自动降低敏感级别。确认后整批保存，有错误行时整批不写。解析器支持 `KEY=VALUE`、可选 `export`、引号、注释和空行，不做命令替换或变量插值。原始文本不进浏览器持久存储，预览不暂存明文，提交时重新解析并校验版本。

### 执行结果

会话时间线展示"安装依赖"和"启动服务"两步，含状态、耗时、退出码和可展开的输出（最后 64 KiB 的脱敏文本，标明是否截断）。安装或启动失败、超时都阻止 agent 开始，不降级成警告。可以"重试当前配置"，它仍用绑定的版本；改了脚本或变量要新建工作区。失败的安装可能留下部分文件，脚本应能重复运行。

测试失败不禁止保存或创建任务，但任务仍要通过实际安装和启动。"未测试""旧版本测试过""失败""通过"是不同状态。测试结果标注环境版本、全局版本、仓库 commit 和镜像 digest，任一不同就只显示历史记录，不把当前组合标成已验证。

### 限制

- 环境不保存运行中的进程。容器停止后由启动脚本重新拉起服务。容器丢失后的会话恢复按原生会话设计处理。
- 复制环境只复制配置，不复制工作区文件或容器层。
- 网络只允许既定出口，填入远端数据库地址不会放行 5432 等端口。

## 4. 数据模型

| 表 | 内容 |
| --- | --- |
| `agent_environments` | `id`、`user_id`、`repo`、`name`、`revision`、`archived_at`；`repo=NULL` 表示个人全局配置 |
| `agent_environment_versions` | 单调递增 `revision`、三类脚本、快捷操作、修改摘要；发布后不可变 |
| `agent_environment_version_variables` | `name`、`value_ciphertext`、`secret`、`phase`；`(version_id, name)` 唯一 |
| `agent_repository_environment_defaults` | `(user_id, repo)` 唯一，指向 `environment_id`，独立 CAS revision |
| `agent_environment_bindings` | 固定的环境版本、全局版本、加密的旧覆盖、API 安装脚本覆盖；不可变 |
| `agent_threads.environment_binding_id` | origin thread 与同组 Fork 指向同一绑定；`setup_script` 保留为解析后的兼容字段 |
| `agent_environment_runs` | 环境测试的绑定、commit、镜像、状态、host、lease/fence、取消标志、步骤结果 |
| 步骤和操作运行记录 | 绑定、sandbox group、容器 boot generation、幂等键、退出码、日志 |

规则：

- 全局配置每个 owner 一份，不允许脚本和快捷操作。活跃仓库环境的名称在 `(user_id, repo, 去空格后的名称)` 内唯一，长度 1–80。删除是归档，ID 不复用。
- 所有编辑带 `(environment_id, expected_revision)` 做 CAS。删除再创建同名环境，旧页面的写入不会命中新环境。冲突返回 409 和当前 revision。
- 设默认、删默认、复制、恢复在事务内完成。删除默认环境同时清除默认引用，不自动选另一套。恢复是把旧配置复制成新版本。
- 创建任务时，同一个事务读取默认、所选版本和全局版本，校验权限和上限，写入绑定。
- 创建任务接口保留显式 `setup_script`：未提供用环境脚本，显式 `null` 或空串表示本次禁用安装。解析看字段是否出现，不看 truthiness。它与变量一样冻结到绑定上。
- 早于本功能的工作区迁成 `legacy` 绑定，保留原安装脚本和空变量，不继承后来配置的全局变量。无配置时仍创建 `required=False` 的空绑定，这样之后新增的全局变量不影响旧任务，也不要求旧 Sandbox Manager 升级。
- 合并上限（见 §5）在 owner 的配置锁行上 `SELECT FOR UPDATE` 串行检查。改全局变量时，检查它与该 owner 每套活跃环境当前版本合并后的结果，任一超限则整次拒绝并返回环境名。

### 历史、密文与清理

- 版本历史显示作者、时间、变量名/类型/阶段的变化以及脚本和操作的 diff。Secret 只显示"新增、替换或删除"。任何读取接口都不返回 Secret 的当前值或旧值。
- 普通变量也加密保存，使用 `AGENT_SOURCE_CONTROL_ENCRYPTION_KEY`，域为 `hybridinference/environment-variable/v1`，与 MCP 和源代码凭据隔离。没有 key 时，含变量的写入和执行返回 503，不存明文也不跳过变量；不含变量的脚本配置仍可用。
- 删除或轮换变量只产生新版本，不删除仍被绑定、原生会话、测试、待发布快照或历史引用的旧密文。实际注入过工作区的 Secret 版本引用随工作区保留，供脱敏和发布扫描使用。
- 归档环境中无引用的版本在归档 30 天后可清理。有执行或发布引用的版本不级联删除。备份按既有保留策略过期，界面不承诺立即物理擦除。

### 已有会话的运行期更新与启动恢复

"更新运行期变量"用 owner 权限和 `runtime_revision` 做 CAS，把当前仓库环境和全局配置合并，只取 runtime/both 变量，存为独立加密快照。安装期输入、脚本、操作和 `binding_id` 不变，不会自动安装新依赖。活动轮次继续用已领取的快照，下一轮读更新后的快照，原生进程发现环境变化后替换进程并继续会话。同组 Fork 共享更新，运行中的服务不热更新。旧运行期 Secret 累计最多 512 条、256 KiB，超过时拒绝并建议新建工作区。

安装成功而启动失败时，默认仍阻止 agent。用户可点击"本轮跳过启动脚本"，只授权该 chat 下一个 human 轮次（含重试）绕过启动，兄弟 Fork 和 goal 自动轮次不能消费。服务可能不可用，界面明确提示。跳过不改启动脚本，之后的正常轮次仍执行启动。只有已结束的最新轮次可以授权跳过，安装失败不能跳过。

## 5. 校验与上限

变量名匹配 `^[A-Za-z_][A-Za-z0-9_]{0,127}$`，区分大小写。保留名在写入时拒绝并说明原因，host 再次校验，发现非法绑定就拒绝运行，不丢掉冲突项后继续。完整集合在 `cloud_agent_shared/environment.py`（`RESERVED_NAMES`、`RESERVED_PREFIXES`）：

| 类别 | 名字 |
| --- | --- |
| Agent CLI | `ANTHROPIC_`（§11 的三个名字除外）、`CLAUDE_`、`CODEX_`、`OPENCODE_`、`KILO_`、`PI_` 前缀；`MCP_TOOL_TIMEOUT`、`DISABLE_TELEMETRY`、`DISABLE_AUTOUPDATER`、`DISABLE_ERROR_REPORTING` |
| GitHub 与 Git | `GH_TOKEN`、`GITHUB_TOKEN`、`GH_HOST`、`GH_REPO`、`GH_PROMPT_DISABLED`、`GIT_TERMINAL_PROMPT`、`GIT_CONFIG_NOSYSTEM`；`GIT_CONFIG_` 前缀 |
| 平台自用 | `AGENT_` 等平台前缀；`WORKSPACE_COMMAND`、`WORKSPACE_CWD` |
| 进程基础 | `HOME`、`PATH`、`USER`、`LOGNAME`、`SHELL`、`PWD`、`TERM`、`BASH_ENV`、`ENV` |
| Docker 客户端 | `DOCKER_`、`BUILDKIT_`、`BUILDX_`、`COMPOSE_`、`LD_`、`OTEL_`、`XDG_` 前缀、代理变量（不分大小写）及相关固定名字 |

Docker 客户端名单放在 `cloud_agent_shared`，控制面和 host 共用，避免控制面接受而 host 的 Docker 客户端行为被改变。

| 对象 | 上限 |
| --- | --- |
| 单个值 | UTF-8、无 NUL、最多 32 KiB；允许多行和空值 |
| 单份全局或环境配置 | 100 个变量 |
| 合并有效集合 | 150 个变量，`name=value` 加终止字节合计最多 128 KiB，所有阶段的并集也要满足 |
| 脚本或操作命令 | 8000 字符，无 NUL |
| 快捷操作 | 每个环境最多 20 个；名称 1–80；工作目录默认仓库根，只能是工作区内的相对路径 |
| `.env` 请求 | 1 MiB；重复名、语法错误、保留名、超限都阻止整批导入 |
| 环境测试 | 每个 owner 最多 1 个非终态测试（含排队）；开始后总时长最多 30 分钟 |

这些是产品和协议上限，不等于 Linux `ARG_MAX`。非空 Secret 去除首尾空白后至少 8 个字符，在持久化前校验，错误信息只含变量名和限制。空 Secret 不作为匹配串。脚本编辑器提示用变量引用，不要把凭据写进脚本。`/v1/agent/config` 公布保留规则版本、上限、超时和能力，前后端用同一份约束。

## 6. Owner API

路由在主 API 的 `/v1/agent/` 下，使用 `require_agent_owner`，响应 `Cache-Control: no-store`。每次按 ID 读取、编辑、复制、设默认、恢复和执行都校验 owner，仓库权限沿用任务 admission 的 entitlement 检查。实现在 `cloud_agent/api/` 的环境路由。

| 路由 | 作用 |
| --- | --- |
| `GET/POST /environments`、`GET/PATCH/DELETE /environments/{id}` | 列出（可按 `repo` 过滤）、创建、读取、CAS 保存整份编辑、归档。PATCH 里 Secret 可保持、替换或删除，不能把遮罩串当值 |
| `GET/PATCH /environment-globals` | 个人全局变量 |
| `PUT/DELETE /environment-default?repo=` | CAS 设置或清除仓库默认 |
| `POST /environments/{id}/copy` | 复制到本人指定的仓库和名称，不复制测试状态 |
| `GET /environments/{id}/versions`、`POST .../restore` | 历史（不含 Secret 值的 diff）与恢复；全局变量在 `environment-globals/versions` 和 `.../restore`，恢复须通过所有活跃环境的合并校验 |
| `POST /environment-import/preview`、`POST /environment-import` | `.env` 预览（只返回变量名与结果）和原子导入（服务端重新解析，不信任前端的 `secret_allowed` 提示；`ordinary_names` 只能引用同次导入的变量） |
| `POST /environment-script-suggestions` | 读取锁文件给建议，不执行仓库脚本 |
| `POST /environment-runs`、`GET .../{id}`、`POST .../{id}/cancel` | 环境测试 |
| `POST/DELETE /environments/{id}/snapshot` | 安装快照，见快照设计 |
| `GET /jobs/{job_id}/thread/environment` | 实际绑定、来源、准备结果、是否有新版本、各操作最近一次运行 |
| `POST /jobs/{job_id}/thread/environment/retry`、`/runtime`、`/skip-start` | 重试失败的准备步骤；更新运行期变量；跳过一次启动 |
| `POST /jobs/{job_id}/thread/environment/actions/{action_id}/runs` | 在当前工作区运行绑定版本中的操作 |
| `GET /environment-action-runs/{id}`、`POST .../{id}/stop` | 操作状态与输出摘要；幂等停止进程树 |

任务创建接口带 `environment_id`、所见环境/全局 revision。`environment_id` 未提供表示用默认，显式 `null` 表示平台基础环境。响应只返回绑定引用和脱敏摘要。复制和执行使用幂等键。测试的取消、超时和完成竞争由 lease/fence 控制，迟到的结果不能覆盖终态。

接受环境值的输入面（变量、导入、创建任务、Sandbox Manager 相关请求）使用 `CredentialSafeRoute`：422 只保留字段位置和安全原因，去掉原始 `input`/`ctx`，异常日志、追踪和审计也不记录请求体。环境加密使用独立的 `EnvironmentEncryptionError`，不能把 GitHub/GitLab/MCP 的故障误标成环境加密错误。

## 7. 控制面下发与 host 生命周期

### 7.1 能力门控

`cloud_agent_shared` 定义 `ENVIRONMENT_CAPABILITY = "environment-v1"`，表示完整支持绑定版本、阶段变量、准备流程、测试和操作。

- claim 返回固定的 `binding_id`、解析后的脚本和操作、分阶段变量。带值字段 `repr=False`，不参与普通事件序列化。每次 claim 解密同一份安装绑定和显式选择的运行期快照，未点击更新就不读最新配置。
- 能力必须验证到实际执行的 runner 和 Sandbox Manager：claim attestation 和 Sandbox Manager 握手都要声明，并与当前部署/进程代次关联。只有 ContainerBackend 和 shared-live/Sandbox Manager 都支持完整时才宣告，失去支持即撤销。
- 需要该能力而领取方不支持时，归还 claim 并短暂退避，不消耗重试预算，不缺变量继续运行。调度优先过滤不兼容的 host，界面显示"等待支持环境配置的 host"。终端和操作直接返回可重试的能力错误，不发送会被旧 Sandbox Manager 静默忽略的字段。
- 解密失败按环境不可用处理，有界退避，不持续占用执行槽位。

### 7.2 准备步骤与恢复

host 的 group supervisor 统一协调准备。agent claim、终端、测试和操作都经过同一个 ready gate。顺序：容器建立，网络校验，首次安装，启动服务，可用。

- 安装成功记录以 `binding_id` 加实际容器/工作区身份标识，只有确认成功才能跳过。失败或结果不明时不记成功，重试可能再执行脚本，所以脚本必须能重复运行。平台没有通用的 exactly-once 保证。
- 启动以 `binding_id`、容器 ID 和 boot generation 标识。容器每次启动递增代次；host 进程重启而容器没重启时不重复启动。
- group 内并发进入通过一个有 fencing 的准备操作合并。
- 启动失败时清理本次创建的受管进程，保留失败输出，重试只重跑启动；安装失败则重跑安装。
- 没有新的恢复机制：容器丢失仍按现有的会话不可恢复规则处理。

安装默认用仓库根目录和 sandbox 准备身份。安装期创建的后台进程在交接运行期前终止，避免仅安装期 Secret 留在常驻进程里。启动的服务由受管的 sandbox 身份运行，生命周期属于 group 而不是某个成员 CLI：成员退出或原生 resume 不杀服务，容器停止或删除时清理。这些服务只有运行期用户变量，没有推理 grant、MCP 凭据或 `GH_TOKEN`，并纳入共享写入者快照冻结。启动脚本成功只证明当时就绪，不保证服务自动重启。

安装、启动、验证和操作使用持久且组可读写的 `/workspace/.cloud-agent-tools/home` 作为工具 HOME，`UV_PYTHON_INSTALL_DIR`、`CARGO_HOME`、`GOPATH`、`POETRY_VIRTUALENVS_IN_PROJECT` 等指向工作区，成员进程得到同样的路径。该目录写入 Git 本地 exclude，不随发布带出依赖缓存。成员 HOME 仍是私有的。状态文件和 PID 记录放在独立的 noexec 私有 tmpfs。

### 7.3 注入、终端和成员隔离

- 合并顺序：backend `base_env()`，该阶段用户变量，平台 HOME/PATH/Git 等，runtime 凭据与配置。平台与用户名字冲突在两侧都被拒绝，最终顺序只是防御。
- 用 `docker exec --env KEY` 按名称传值，值不进 argv、容器 create 配置或平台生成的工作区文件。明文只存在于授权控制面和 host 的内存、进程环境和受保护的内部请求中。请求、异常 repr、profiling 和持久化的 host 状态不得含值。
- `ResidentLaunch`、`SharedResidentLaunchConfig` 和终端请求携带绑定身份与对应阶段的环境，参与兼容性检查，比较的是绑定和版本标识，日志里没有 Secret 明文或无盐摘要。平台凭据按原机制续期，与用户配置冻结分开。
- Agent 成员沿用独立 UID 和私有目录，不把平台推理、MCP、GitHub 凭据移入 group 基础环境。
- 终端和一次性命令用工作区绑定的运行期变量，冷启动先过 ready gate。已打开的终端不会因保存配置而改值。新增字段必须在 Sandbox Manager 确实消费后才算成功，不能靠 Pydantic 忽略额外字段。

## 8. 测试环境与快捷操作

### 8.1 环境测试

测试是独立的 `environment_run`，有自己的队列、lease/fence、取消和终态，复用 host 容量、沙箱和网络实现，不伪造带空 prompt 的 coding job。

1. owner 选择环境和 ref（默认仓库默认分支）。首次请求时控制面把 ref 解析成确定的 commit，固定环境和全局版本；幂等重试先按 owner、选择和 ref 找回原 run。
2. 获取独立执行槽位，创建临时容器，使用与正式任务相同的镜像、资源限制、网络和准备路径。
3. 源代码经可信路径获取，不把长期 Git 凭据交给脚本。测试不领推理 grant、MCP relay 凭据和有写权限的 `GH_TOKEN`，不启动 coding agent，不发布 PR。
4. 依次执行安装、启动和可选的验证命令。没有验证命令时只证明安装和启动成功，不宣称项目测试通过。
5. 完成、失败、超时或取消都终止进程并删除临时容器、网络和工作区，保留脱敏结果和版本引用。host 失联由 reaper 标记异常，过期租约不再占槽，但保留 cleanup 未完成的孤儿记录。重连的 host 在确认清理前不能领新任务，autoscaler 也不把它算作可用容量。

测试不能覆盖已有聊天工作区，也不生成可复用镜像。界面明确提示它仍会运行用户脚本，可能对外部服务有副作用。失败重试创建新的 run ID 和干净容器。

测试计入 host 容量，不是绕过资源上限的入口。每台 host 的一个后台执行器负责测试的领取、运行和清理，一次在途占一个本地槽位；主领取循环继续处理 coding task。关闭浏览器不会留下无限运行的测试：取消、步骤超时和总 TTL 都由服务端执行。

### 8.2 快捷操作

操作是当前绑定里的具名命令，通过现有 terminal/Sandbox Manager 执行，不创建模型 turn，不用推理额度。配置更新后，旧工作区仍显示旧的操作列表。

- 操作复用终端的 admission、暂停/恢复和 activity 机制，agent 或快照需要协调工作区时同样受控。新增执行与 snapshot capture 串行，已运行的进程及脱离进程组的子进程都在写入者冻结范围内。
- 运行请求只带 `action_id`、binding 和幂等键。host 从可信配置解析命令，不信任客户端覆盖 owner、工作区或脚本。工作目录规范化后检查 realpath 在工作区内。停止只终止本次操作的进程树。
- 数据库里的 run 身份和幂等键持久化，终态不被迟到的 running 响应覆盖。Sandbox Manager 在产生副作用前把运行身份写入 host 私有目录 `environment-actions`（不挂入容器），完成时保存有界脱敏输出和退出码。相同 run ID 在 terminal 回收或 Sandbox Manager 重启后也不再 spawn。停止先于延迟的启动请求到达时，保留停止记录阻止它启动。
- 结果缺失或无法确认启动与否时标 `unknown`，提示核对外部副作用后手动新建 run，不凭 404 判成功，也不永远 running 或自动重跑。
- 允许前台长时间运行，沿用终端的空闲/断连生命周期和显式停止。实时输出走 owner 授权的终端通道，写入平台历史的摘要必须脱敏。

## 9. 脱敏、历史 Secret 与发布检查

### 9.1 输出脱敏

Secret 集合来自实际绑定和该工作区曾注入的所有版本引用，包括安装期 Secret。轮换、删除或归档环境不会让旧值失去保护。

脱敏在原始文本上传、持久化或进入异常详情之前进行，覆盖 runner、Sandbox Manager、环境测试、安装/启动后台输出、操作摘要、资源测量里的文本详情，以及快照和发布失败路径。脱敏器按输出流维护状态，保留最长匹配串所需的尾部缓冲，处理跨 chunk、UTF-8 字节边界和 stdout/stderr 各自的顺序。截断、拼接、重试、flush、异常和取消都要遵守，不能先截断落盘再打码，也不能在 cancel 时直接上传未检查的尾部。不能在单个 payload 上做 `.replace()`，因为 Secret 可能被拆在多个 delta 里。

- 完整值精确匹配。多行值额外匹配去掉首尾空白后不少于 8 个字符的实质行，排除 PEM 的 `-----...-----` 头尾。单个短行和通用分隔行不作全局匹配串。优先最长匹配，替换成 `[secret:NAME]`。
- 只处理日志事件里指定的文本字段，不改写 ID、digest、序列号、状态等协议字段。
- 输出短到仍可能是 Secret 前缀时可以暂缓展示，流结束后检查剩余内容。
- 保护目标是已知 Secret 的原文和上述多行形式，不保证识别编码、变形或模型改写后的泄漏。owner 的实时终端和文件查看仍是原始内容。

### 9.2 发布和快照

可信快照扫描用与捕获一致的临时 Git index：从固定 base `read-tree`，再 `git add -A -N`，扫描新增或修改的实际文件字节，包括未跟踪且未忽略的文件、二进制和符号链接目标文本。不跟随符号链接，不改用户 index。扫描与捕获在同一个共享写入冻结边界内。文件名按 Git 记录的原始字节读取，不要求 UTF-8。工作区里的另一个 Git 仓库在补丁里只是一条提交 id，扫描跳过它，发布时 publisher 拒绝这种子模块条目。

受信 publisher 另外扫描暂存 blob，独立再检查一次。命中就阻止发布，只返回变量名和文件路径。后台 snapshot capture 在补丁写入存储前做同类检查，命中则阻止该次快照并给出脱敏原因，不把原始补丁放进错误日志。只删除含 Secret 的旧行不算新泄漏。已排队的发布保留所需的历史密文引用。

这不是任意文件加密或防外发：agent 写入工作区的文件和原始归档仍可能含值；沙箱用 `GH_TOKEN` 直接 push 不经过 publisher；模型上下文不等同于平台 UI 日志。

## 10. 安全边界与风险接受

用户自带凭据进入沙箱，是本功能引入的设计变化，已写入 AGENTS.md §7 和沙盒安全模型。

owner 主动提供的变量可被其仓库代码、依赖和 agent 读取。提示注入或不可信代码可能使值经现有公网出口外发、进入模型上下文或写入文件。平台不承诺阻止这些。

平台提供静态加密、owner 授权、Secret 不回显、分阶段注入、已知值的日志脱敏和受信发布扫描，不把它们当作对恶意仓库或模型的保密边界。运维持有解密 key 时仍可解密，与既有凭据存储相同。

平台长期账号和服务凭据留在沙箱外。用户不能通过环境覆盖平台 grant、MCP、GitHub、宿主 Docker 或网络配置。MCP-only listener、每成员凭据隔离、Kata/non-root/capability/seccomp 和资源限制不变。只支持同 owner 的共享工作区，不能借此把个人 Secret 分享给另一用户。团队环境的 Secret 规则见 Team 设计。

## 11. 平台推理变量与用户推理凭据

runtime 适配器用 `OPENAI_API_KEY`、`ANTHROPIC_API_KEY` 等标准名字把平台推理 grant 交给 agent CLI，并用 base URL 指向网关，CLI 运行的命令继承 CLI 的环境。把同名用户变量直接放进 CLI 环境，会改变 agent 认证，或被平台值盖掉而让用户代码读到平台 grant。所以只开放两组名字，并和 CLI 分开：

| 组 | 名字 |
| --- | --- |
| OpenAI | `OPENAI_API_KEY`、`OPENAI_BASE_URL` |
| Anthropic | `ANTHROPIC_API_KEY`、`ANTHROPIC_AUTH_TOKEN`、`ANTHROPIC_BASE_URL` |

- 启动 CLI 时这些值不进入 CLI 的环境，CLI 仍用平台 grant 认证。
- host 把它们写成脚本放在该成员私有的 tmpfs（`/run/agent-members/<slot>/inference-env.sh`，0600，只属于该成员 uid，经 stdin 写入，不进 argv），并让 CLI 环境的 `BASH_ENV` 指向它。CLI 运行命令时启动的每个 bash 先读这个脚本，所以命令、它启动的服务和子进程拿到用户的值。
- 用户设置了某组中任一名字，这一组完全以用户为准：脚本先 `unset` 整组，再导出用户设置的名字。只设 `OPENAI_API_KEY` 时 `OPENAI_BASE_URL` 被清空，用户的 key 不会发往网关。没设置的组保持原状。
- 安装、启动、验证脚本、终端和快捷操作不经过 CLI，直接按阶段注入用户的值。

这依赖各 CLI 用 bash 执行命令。`tests/external/test_cli_inference_variables.py` 用真实 CLI 和假网关验证：CLI 发往网关的请求只带平台 grant，命令看到的是用户的值。升级 CLI 时重跑它。

执行这样的绑定需要 `environment-inference-v1` 能力，旧 host 会拒绝这些名字。领取任务、控制面直连 Sandbox Manager 和 runner 准备环境前都检查该能力，缺少时按 §7.1 退回等待，不把带值配置发给旧版本。

用户的值照常参与脱敏和发布扫描，风险同 §10。本设计不含用用户自己的 key 为 coding agent 付费，也不开放 `CLAUDE_*`、`CODEX_*`、`ANTHROPIC_MODEL` 等配置 CLI 本身的名字。

## 12. 部署

环境配置没有管理员功能开关。`/config` 始终提供 `environment_limits`，Settings、任务环境选择和 Environment tab 始终显示。没有配置变量或脚本时任务用基础环境，不执行环境准备。权限、加密、环境能力检查始终生效。

运行细节：

- Environment tab 的 GET 不写库。Secret 脱敏材料按 owner、绑定、运行期版本和加密 key 缓存 60 秒（最多 256 条），状态的 Docker inspect 缓存 15 秒。准备摘要由持有 attempt 租约锁的生命周期事件写入，失去租约的 host 不能覆盖。
- 发布遇到环境解密故障按暂时不可用重试，不永久失败。

滚动部署和回滚：

- 迁移先可用，host 和 Sandbox Manager 宣告能力后才承接新增配置。部署期间不能丢变量执行，测试队列也不能把新协议派给旧进程。
- 首次创建新绑定之前，所有处理任务创建、后续消息、Fork 和终端请求的控制面副本都必须已升级，否则旧副本会把新绑定当成无配置。
- 存在新绑定后不支持直接回退到不认识绑定的旧二进制。回滚要先关闭相关执行入口，排空测试和准备操作，并让已绑定的工作区由兼容版本服务或保持禁止执行。

## 13. 实现位置

路径相对 `apps/backend/`（前端在 `apps/frontend/`）。

| 部分 | 位置 |
| --- | --- |
| 共享规则和能力 | `cloud_agent_shared/environment.py`、`shared_protocol.py` |
| 存储与迁移 | `migrations/versions/`、`cloud_agent/ports/`、`cloud_agent/storage/repositories/` |
| 加密与绑定解析 | `cloud_agent/source_control.py` 的 cipher 模式；`cloud_agent/application/` 的环境解析服务 |
| Owner API | `cloud_agent/api/` 的环境路由、`api/config.py` |
| 新任务与 Fork | `cloud_agent/application/job_admission.py`、`storage/repositories/session_fork.py` |
| claim 与兼容 | `cloud_agent/api/worker.py`、`cloud_agent_host/runner.py`、Sandbox Manager 握手 |
| 准备与服务 | `cloud_agent_host/setup.py`、`chat_sandbox.py`、group supervisor |
| 成员变量与终端 | `cloud_agent_host/resident_transport.py`、`sandbox_manager.py`；`cloud_agent/api/workspace.py`、`sandbox_manager_client.py` |
| 快照与发布 | `cloud_agent_host/manager_api/snapshots.py` 和控制面的发布路径（受信 publisher） |
| 前端 | `apps/frontend/src/components/features/agents/` 下的 `TaskComposer.tsx`、`WorkspaceTabBar.tsx`、`JobDetail.tsx` 等和 environment 组件 |

## 14. 参考

- [Cursor 云端环境设置](https://cursor.com/docs/cloud-agent/setup)：安装与启动分离、可复用配置和测试体验。
- 网络说明的表达可借鉴，但执行边界由统一公网出口设计决定。
