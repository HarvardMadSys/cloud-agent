# Agent 沙盒安全模型与能力边界

状态：当前安全契约。已知缺口见 §17。

相关：[仓库开发与安全边界](../../../AGENTS.md)、[部署环境与隔离档位](../../../ENVIRONMENT.md)、[原生会话、Fork 与共享工作区](2026-08-31-native-session-fork-shared-workspace-design.md)、[统一公网出口](2026-09-23-agent-public-egress-design.zh-CN.md)。

## 1. 目标与非目标

平台允许 Agent 对用户授权的仓库执行动态代码。设计要满足：

1. 沙盒内代码拿不到 control plane、runner host、Docker daemon 或 source-control publisher 的控制权。
2. 沙盒只得到完成当前任务所需的短期能力，能力有 scope、subject、generation、TTL 和撤销边界。
3. 准备、执行和 Fork 使用同一套固定网络边界，没有可选择或放宽的网络模式（[出口设计](2026-09-23-agent-public-egress-design.zh-CN.md)）。
4. 平台持有的长期源代码、模型、MCP upstream 和发布 credential 不进入 workspace 或 Docker writable layer。Owner 主动提供的环境凭据遵循 §6.1a 的例外。
5. 同一 shared sandbox 内的 sibling chat 可以共享文件，但不能读取彼此的 credential、控制彼此的进程或接收彼此的事件。
6. 安全能力无法验证时 fail closed，不静默降级到更弱的 runtime、开放网络或共享 credential 路径。
7. 观测、审计和资源采样不依赖沙盒内代码配合，也不能被它伪造为成功。

本文不声称：普通 Linux container 等同于独立内核；Docker 足以承载所有 public multi-tenant workload；模型能可靠识别 prompt injection；sibling chat 之间有文件机密性；安全策略能阻止用户授权域内的 Agent 故意破坏共享代码；容器隔离能修复宿主内核、Docker daemon 或镜像供应链漏洞；日志或 eBPF 本身构成访问控制。

## 2. 威胁模型

不可信输入：用户仓库里的源码、脚本、Git hooks 和构建配置；包管理器安装脚本及传递依赖；README、AGENTS.md、issue、网页和检索结果中的 prompt injection；MCP server 返回的内容；Agent 生成的 shell command、patch 和后台进程；编译器、测试工具、插件和 language server 的子进程；sibling chat 写入共享 workspace 的文件。一个边界如果要求 Agent "自觉不使用"某项已进入沙盒的能力，就不是安全边界。

受保护资产：用户源码与未发布修改、source-control access、推理能力（冒用身份、绕过 scope 或 quota）、MCP upstream credential、control-plane 数据库、runner host 与 Docker socket、publisher credential、sibling 的 activation credential、telemetry 与审计记录。

可信计算基（TCB）：control plane 与数据库；runner、Session Supervisor 与 sandbox manager；Docker daemon 与目标隔离 runtime；source preparer、gateway/MCP relay 与 publisher；sandbox base image、runtime pin、seccomp 与 capability 配置；宿主内核，或 Kata 提供的额外边界。用户仓库、Agent CLI 执行的工具、workspace 内容和上游 MCP 响应不在 TCB 中。

## 3. 信任边界与数据流

```text
                      trusted control domain

 browser ──identity──> control plane ──dispatch──> runner/supervisor
                           │                         │
                           │                         ├── Docker Engine
                           │                         ├── source preparer
                           │                         └── sandbox manager
                           │
                           ├── gateway: identity/model catalog/grants
                           ├── MCP registry + per-user encrypted connections
                           └── publisher + source-control credential

                                      capability boundary
                                                │
                                                ▼
                                      untrusted sandbox
                                      ├── /workspace
                                      ├── Agent CLI
                                      ├── build/test tools
                                      └── child processes
```

沙盒能到达的目的地由[出口设计](2026-09-23-agent-public-egress-design.zh-CN.md)和 AGENTS.md §7 规定：公网 IPv4 TCP 80/443、一个指定解析器、本组的 MCP 中继（以及配置了的模型中继和 GitHub 中继）。中继是固定目的地的转发器，不是通用代理，沙盒不能指定其上游。

## 4. 权限所有权

| 能力 | Authority | 沙盒是否持有 | 约束 |
|---|---|---|---|
| 用户登录与角色 | Gateway identity | 否 | 短期 identity token 供 control plane 验证 |
| Job、Attempt、lease fence | Cloud Agent control plane | 否 | Runner 只持有当前 claim |
| Repository clone | Trusted source preparer | 否 | 短期 token 使用后删除 |
| Model inference | Gateway | 是，短期 `agr` capability | User、model scope、TTL、quota |
| MCP call | Cloud Agent MCP relay | 是，独立 `mcp` capability | User、job、attempt、server/tool allowlist |
| MCP upstream credential | Cloud Agent encrypted store / relay | 否 | Relay 代为调用 upstream |
| Publish/push/PR | Trusted publisher | 否 | Patch gate、secret scan、目标校验后使用短期 token |
| Docker lifecycle | Host supervisor | 否 | Generation-fenced Docker API |
| Terminal/file/Git operation | Sandbox manager | 否 | Membership auth + operation fence |

模型 grant 和 MCP token 是两个 audience：泄露 inference grant 不能调用工具，泄露 MCP token 不能调用模型或 control-plane API。

## 5. Credential 生命周期

### 5.1 不得持久化的 credential

clone token、inference grant、MCP token、publisher token、local-control password 和长期 provider secret 不得出现在：Docker image layer 与 `docker history`；容器创建环境（`docker inspect Config.Env`）；CLI argv；`/workspace`；durable `$HOME`、provider state root 和 native session transcript；Fork receipt journal；patch、artifact、日志 body 和前端事件；host 的 per-chat 文件目录。

### 5.1a Owner 主动提供的环境变量

[可复用云端环境](2026-10-12-repository-environment-design.zh-CN.md)允许 owner 把自己的变量和 Secret 注入固定版本的工作区。它们可以是长期凭据，仓库代码、依赖和 agent 可以读取、外发，或写入文件和模型上下文。§5.1 的规则不构成对用户代码行为的保密承诺。

控制面用独立的加密 domain 保存所有值，owner 配置读取接口不回显 Secret。已知原文由这些措施保护：阶段过滤；写入平台历史前的有状态脱敏；对普通文件和二进制新增内容的受信快照与发布扫描。Owner 自己的终端和文件查看不打码。保留名不能覆盖平台 grant、MCP、GitHub、Docker 和网络配置。Owner 设置的 `OPENAI_*`/`ANTHROPIC_*` 推理凭据不进入 agent CLI 自身的环境，CLI 继续用平台 grant；成员私有 tmpfs 中的 `BASH_ENV` 脚本只把它们交给 CLI 运行的命令。配置变化只影响未来工作区，同 owner 的 Fork 继承原绑定，没有跨 owner 分享。

安装快照（[设计](2026-10-15-environment-install-snapshots-design.zh-CN.md)）只在一次性的预构建容器里生成，不截取对话工作区。预构建只运行安装脚本，看不到运行期变量，环境测试的容器不保存。快照只给同一 owner、同一环境、同一安装指纹的新工作区用。它是不可信的沙盒输出，所以和检查点恢复一样，核对 SHA-256、归档结构和镜像名之后才 `docker load`。

安装、服务、验证和快捷操作使用受管的 non-root 身份，各有私有 HOME。安装与验证结束后清理对应 UID 的进程，包括 detached 后代；服务属于容器 boot。快照冻结纳入这些身份，操作停止只清理本次操作的身份。

### 5.2 注入与撤销

Credential 只通过这些临时路径进入执行：process-scoped environment；成员私有的 tmpfs activation directory；内存中的 bridge local-control credential；Supervisor 与 relay 之间受控的请求 metadata。

Activation credential 和 residency grant 都要：在 generation 确认后签发；只绑定一个 subject、scope 和 execution identity；使用短 TTL；由心跳在 fence 仍有效时续期；撤销请求丢失时也会在 TTL 到期后失效。

两者的撤销边界不同：

- Activation credential 绑定当前 job/attempt。turn settle、cancel 或 supersede 后撤销。
- Residency grant 绑定一个 thread 的常驻 membership，Gateway external job id 为 `thread:<thread_id>`。一次 turn settle 不撤销它。只要同一个 resident process 还活着，它就跨该 thread 的后续 turn 保留。process replacement、idle stop、takeover 或 release 时撤销。

只靠远程 revoke 加长 TTL 不可接受，因为 control plane 故障时，被遗弃的 attempt 会一直持有能力。

### 5.3 Retained sandbox 与 Fork

Credential 不是 session state。`docker stop` 后 tmpfs 清空，`docker start` 后重新签发。Fork 不复制 credential，child membership 使用新的 slot、generation 和 credential。native session 文件可以复制或分叉，但其中不得有 provider key。

## 6. Repository ingress

沙盒不持有 repository clone credential。流程：

1. Control plane 校验用户、repository 和授权。
2. 受信的 source preparer 在临时目录里用短期 token fetch。
3. 删除 credential 和远端中可能携带的敏感配置。
4. 校验 tar path、file type、size、uid/gid 和目标边界。
5. 通过 Docker archive API 流入 `/workspace`。
6. prompt admission 前删除临时 checkout。

临时目录不是 checkpoint，也不能作为恢复或 Fork 的第二份真相。之后的 published base 更新用不含 credential 的 Git bundle 导入 object，不得 reset shared worktree、index、local commit 或未提交的改动。

## 7. 沙盒网络

所有任务共用一套网络，规则和验收见[出口设计](2026-09-23-agent-public-egress-design.zh-CN.md)。安全相关的要点：

- 规则由宿主 nftables 表执行，由短时 helper 安装（runner 和 sandbox manager 自身不持有 `NET_ADMIN`），先安装并回读校验，再创建组网桥。校验不过则 host 不接活。
- 每次冷启动后在 guest 内自检路由、解析器、中继和两个边界探针，通过后才运行仓库代码。
- 规则无法证明时，停止对应的组并保留工作区。

接受的限制（细节见出口设计 §2、§3）：

- 80/443 只是端口约束，不检查内容，不防 DoH 或隧道，不防外发。沙盒可以把仓库内容和自己的短期令牌发往公网。
- 平台公网地址可能可达。MCP 工具调用只由 MCP 专用应用提供，主 API 没有工具路径，这项入口隔离保留。
- 推理 grant 的外传与重放风险已被接受：持有者可在网关可达的推理入口重放，消耗所属用户的额度和平台资源，但受该用户的模型权限、配额、有效期和撤销约束，不授予其他身份、MCP 或平台管理权限。驻留 grant 随有效进程续期，短 TTL 和默认 30 分钟空闲回收都不是从泄露时起算的绝对上限。这个决定不放宽长期凭证、成员隔离、MCP、配额、Kata 或网络规则。

## 8. 容器与 runtime 隔离

选 runtime 要先回答：工具进程发出的 syscall，最后由谁执行。

[![runc、gVisor 与 Kata 的 syscall 执行边界：普通容器直接进入宿主机内核，gVisor 由 Sentry 承接，Kata 由客户机内核执行](assets/sandbox-syscall-execution-boundaries.svg)](assets/sandbox-syscall-execution-boundaries.svg)

*图 1：隔离边界越强，宿主机越难直接观察并归因工具进程的原始 syscall。*

| 维度 | 普通容器 / runc | gVisor | Kata |
|---|---|---|---|
| syscall 的执行者 | 宿主机内核 | Sentry 用户态内核 | 客户机内核 |
| 与宿主内核的边界 | namespace/cgroup/seccomp，共享内核 | 用户态 syscall 实现，缩小 host syscall 面 | 硬件虚拟化，独立 guest kernel |
| 启动与固定开销 | 最低，毫秒级 | 较低 | 最高，百毫秒至秒级，有 VM 固定内存 |
| Linux 兼容性 | 最高 | 有 syscall 与内核特性缺口 | 接近原生 guest Linux |
| Host eBPF 直接看到 | 工具进程的真实 syscall | Sentry/Gofer 的 host syscall | VMM、virtio、tap 等 host-side 行为 |
| Operation 归属 | 按 PID/cgroup 直接关联 | 需要 runtime event 与代理侧 identity 关联 | 需要 guest 内的 telemetry 回传 |

测量位置必须随隔离边界移动：runc 可以主要依赖 host sensor；Kata 的 syscall 和进程级观测必须进入 guest，host 侧只保留 VM 与设备总量。

### 8.1 当前实现与准入

`cloud_agent_host/container/sandbox.py` 只有三个 backend：`process`（仅作确定性测试接缝，不能 attest）、`container` 和 `kata`。服务端的执行 profile 只有 `trusted-v1` 和 `untrusted-kata-v1`，由 `AGENT_DEFAULT_EXECUTION_PROFILE` 设定，调用方不能选择，Follow-up 和 Fork 继承已存的策略。

- `container` 且 `runtime=None` 是普通 runc 共享内核。Preflight 记录 `agent_sandbox_shared_kernel`，该档位只适合可信仓库，或已有外层 VM 隔离的场景。
- `kata` 使用 `io.containerd.kata.v2`。Preflight 用 exact-image probe 执行 `uname -r`，guest kernel 与 host kernel 相同则拒绝宣告 VM-isolated。同一 probe 还要跑 guest observer 的 `--self-test`：必须经 guest ring buffer 看到一个短命非 root child 的 fork、exec、exit 和非零 CPU 的 `done`。BTF 对不上的 guest kernel 不能 advertise 接活。
- 进程级观测走 guest-root CO-RE observer（`deploy/sandbox/guest-observer.*`），不用 host eBPF。Agent 进程没有 `CAP_BPF`，helper 只由 Kata 专用的特权 exec 以固定 argv 拉起。
- gVisor 没有独立 backend、profile 或 probe。管理员能填 runtime 字符串，不等于平台验证了 gVisor 隔离。不可信仓库的强制档位只认通过 probe 的 Kata。

`is_vm_isolated` 只表示配置了非空 runtime，不是安全证明。准入必须依赖明确的 profile、runtime allowlist 和 exact-image probe。配置要求强化隔离但 host 只提供普通 Docker 时，host 不得 advertise capability，也不得 claim 对应 job。

| 场景 | 最低边界 |
|---|---|
| 一次性、隔离 VM 中的内部任务 | Hardened container（外层 VM 承担租户 kernel boundary） |
| 可信内部仓库、自托管 host | Hardened runc container（仍需 non-root、cap、seccomp 和资源限制） |
| Public multi-tenant、不可信仓库 | 通过 probe 的 Kata VM |

### 8.2 基线 hardening

所有执行 profile 至少具备：非 root user；`no-new-privileges`；drop all capabilities，只按审核结果加最小集合；seccomp；CPU、memory、PID、disk 和 timeout 限制；`--init`；没有 Docker socket、host `/proc`、host cgroupfs 和 host path mount；不含 credential 的不可变 base image；精确的 runtime/helper pin 和 image digest attestation。

retained 容器的创建路径已设置 non-root、capability drop、`no-new-privileges`、默认 seccomp、CPU/memory/PID 限制、`--init` 和 stop timeout。Writable layer 的 disk 配额由 `AGENT_SANDBOX_DISK_MIB` 设置，默认 0，即不限。设置后用 `--storage-opt size=` 让 Docker 对 writable layer 限额，驱动或底层文件系统不支持时创建失败，不会去掉限额重试。

## 9. Docker-owned storage

chat state 只通过 Docker Engine API 操作：create、start、stop、inspect、exec、stats、archive。不读取 `/var/lib/docker`，不解析 storage-driver 私有格式，没有 per-chat host workspace，`/workspace` 和 native state 不配置 bind、named 或 anonymous volume。这减少了 host-path 攻击面，但不提供脱离 host 的持久性：删除容器、误运行 prune、丢失 data root 或丢失 host 仍会使 session `lost`，除非有可恢复的 checkpoint。

Session host 禁止 generic container/system prune。磁盘压力通过 admission 和告警处理，不能通过静默删除 retained sandbox 处理。

## 10. Shared sandbox 的成员隔离

Fork sibling 属于同一个用户和 security domain，有意共享 `/workspace`。Credential 和 process control 仍是硬边界。

每个 membership 使用预制的 execution slot：独立 UID、共享 gid、私有 tmpfs、各自的 process generation；workspace 是共享的 setgid group 且不放 credential 文件；平台 journal 属于 sandbox 用户且不含 credential。具体 UID 是实现参数，契约是：

- sibling UID 不能读取 `/proc/.../environ`，不能 ptrace 或 signal sibling，不能读取 sibling 的 tmpfs 和 config；
- slot 复用前必须完成 secret 和 process 清理；
- 没有安全的 UID/slot pool 时，不 advertise shared capability。

| 内容 | 共享 | 私有 |
|---|---|---|
| `/workspace` 与 `.git` | 是 | |
| 依赖、构建产物、项目级 instruction | 是 | |
| Native conversation id | | 是，每个 membership |
| Provider home | 并发安全的 runtime 共享 | 并发不安全的 runtime 每成员一份 |
| Activation credential 与 config | | 是，tmpfs |
| Event stream 与 cancellation | | 是，generation-fenced |
| Terminal 对文件的影响 | 是 | Terminal 的所有权和 cancel 是每成员的 |

sibling 故意 chmod、删除或覆盖文件属于 shared-workspace 干扰，可能让某个成员 `lost`，但不应让它拿到 sibling 的 credential。

在同一容器网络命名空间里，`127.0.0.1` 不是 membership 边界。使用 loopback HTTP server 的 runtime 必须：绑定随机端口且不发布到 host；使用 process-generation scoped 的认证；credential 只存在于 bridge 进程的环境或内存；未认证请求被拒绝；sibling 不能从 `/proc` 或持久文件读取 credential。

## 11. 进程生命周期与取消

清理不能依赖 Agent CLI 正常退出。

Turn 开始前记录 member UID 的 PID + start-time baseline。结束或取消时：

1. 优先调用 runtime 自带的 interrupt；
2. 对本 activation 新增的同 UID 后代发 TERM；
3. 超时后 KILL；
4. 重复 census，处理 `setsid`、double-fork 和 PID reuse；
5. 零后代得到证明后，才把 resident root 标为 idle/HOT。

Process group 只是线索。PID 必须与 start time 组合，避免 PID reuse 误杀。

Prompt admission 前取消可以安全释放 attempt。admission 后，如果 runtime 无法证明 prompt 未被消费或已到 settled boundary，就停止该 membership 的 transport，标记成员 `lost` 或明确的 dirty failure，不 replay 同一个 prompt，也不停止 sibling 或整个 group，除非容器本身失效。

## 12. File、Terminal 与 Git API

这些 API 必须经过 sandbox manager，不走 host filesystem：

- 路由先授权 `user -> thread -> membership -> sandbox`；
- 每个 mutation 创建 generation-fenced 的 operation token，并计入 group activity 和引用计数，防止 idle stop；
- file path 规范化并限制在 workspace root，archive upload 校验 path traversal、symlink 和 file type；
- command 和 PTY 有 idle timeout 与 hard lifetime；
- terminal 没有 inference/MCP credential，所有权和 cancellation 按 membership；
- Git view 可共享，publisher 的权限不在 sandbox manager 内。

只读 operation 可在 sibling turn 期间继续。写 operation 必须被 snapshot quiescence census 计入。

## 13. Publication

沙盒不能 push。发布路径：

1. 沙盒内生成相对可信 base 的 patch；
2. Control plane 存储不可变 artifact 和 workspace epoch；
3. Trusted publisher 在外部 checkout 上应用 patch；
4. 校验 path、mode、symlink、binary policy 和 patch base；
5. 执行 secret scan 和 workflow approval policy；
6. 用短期 source-control credential 做 commit、push 和 PR；
7. 外部副作用结果不明确时停止自动重试。

Publisher 不信任沙盒声明的 repository、branch 或 base，要从 control-plane 的记录重新解析。

## 14. Fail-closed 规则

| 检查失败 | 禁止行为 | 必须结果 |
|---|---|---|
| Identity / authorization 不可判定 | 创建 job | 4xx/5xx，不匿名降级 |
| Runtime/image attestation 失败 | Claim shared/untrusted job | Host 不 advertise capability |
| Gateway grant 无法验证 | Model call | 401/403/503，不得不计量放行 |
| MCP token/fence 无法验证 | Tool call | Relay 拒绝 |
| 宿主网络规则缺失或回读校验失败 | 创建组网桥、claim job | Host 不接活，组网络操作失败 |
| Guest 网络自检失败 | 执行仓库代码或发送 prompt | 停止沙盒、保留工作区，turn 失败 |
| Native resume 不可证明 | 发送 follow-up | `lost`，不 blank-session fallback |
| Slot cleanup 不明确 | 复用 slot | 成员 fail closed |
| Snapshot writer census 不一致 | 宣称 quiescent/publish | 保持 pending/dirty |
| Publisher 外部结果不确定 | 自动重推 | 等待人工 reconcile |
| 要求的强隔离不可用 | 普通 Docker fallback | 拒绝 job |
| Kata guest observer `--self-test` 失败 | 宣称 host 可接 Kata 活 | Host 不 advertise capability |

## 15. 审计

安全相关记录包括：Job/Attempt/lease/generation 转换；grant 的 mint、renew、revoke 和过期（不记 token）；宿主网络规则的安装与回读、guest 网络自检和 relay probe；slot 分配、process cleanup 及不明确情形；native resume 与 Fork receipt 校验；容器 OOM、意外退出和 missing object；snapshot gate、secret scan 和 publisher 结果；sampler coverage 与 sensor gap；Kata guest observer 的 cursor、loss 和 incomplete 标记（不是完整的零进程普查）。

不做逐连接出站审计，宿主规则上的拒绝计数是本部署的总量，不归属到组或成员。日志属性只用有界的标识符和枚举，prompt、tool argument 与 result、credential、repository token 和完整环境变量不得进入共享 telemetry sink。

## 16. 验收要点

- Credential：`docker inspect`、image history、workspace、native state 和 Fork journal 中没有 credential；撤销请求丢失时短 TTL 仍能终止能力；suspend 用户后 model call 立即被拒；inference token 不能访问 MCP，MCP token 不能访问 inference 或 control plane；sibling 不能读取 credential 或 local-control password；slot 复用前不留旧 secret 或进程。
- 网络：按出口设计 §9 的验收表执行。
- Runtime 隔离：non-root、cap drop、seccomp 和资源限制在真实 runtime 中生效；public multi-tenant profile 通过 Kata exact-image probe；sibling 的 `/proc`、ptrace、signal、tmpfs 测试通过；`setsid`、double-fork 和忽略 TERM 的进程都能清理；容器 OOM 按 group failure 处理；不会静默回退到共享 UID 或普通 Docker。
- 数据与发布：clone token 不进沙盒；tar 路径穿越和异常 file type 被拒；shared workspace 的竞争在 UI/API 中明确，不虚构 per-chat patch；patch gate、secret scan 和目标校验在 trusted publisher 中执行；不明确的 push 不自动重复；retained container 不被 generic prune 删除。

## 17. 已知剩余风险

- Docker writable layer 是单一持久副本，host 或 data root 丢失会导致 `lost`（有可恢复 checkpoint 的除外）。
- disk 配额默认关闭，需要设置 `AGENT_SANDBOX_DISK_MIB`。
- 公网出口按端口放行，不检查内容，不防外发。推理 grant 被带出后可在有效期内按所属用户的权限重放，该风险已接受。
- Shared workspace 允许同一授权域内的 sibling 相互影响文件和项目级 state，包括在 `.git` 里埋下被下一个执行者运行的 hook。
- 普通 Docker 共享宿主内核，只适合相应的信任等级。
- Runtime-native 的 state 格式和并发行为受精确版本影响，升级必须重新 attestation。
- Relay 与 publisher 扩大 TCB，需要独立的 patch、依赖和部署审计。
- Resource 和 operation sensor 故障不会停止 turn。要通过 coverage 与 ops event 暴露，不能误报为零风险。

## 18. 代码入口

| 边界 | 位置（`apps/backend/` 下） |
|---|---|
| Sandbox 与 Docker lifecycle | `cloud_agent_host/container/` |
| 沙盒网络：配置、宿主规则、组网桥 | `cloud_agent_shared/egress.py`、`cloud_agent_host/network/` |
| Source setup/import | `cloud_agent_host/setup.py` |
| Supervisor、slot 与 process cleanup | `cloud_agent_host/supervisor/` |
| Runtime credential/config | `cloud_agent_host/runtimes/`、`cloud_agent_host/resident_transport.py` |
| Sandbox manager | `cloud_agent_host/sandbox_manager.py`、`cloud_agent/sandbox_manager/` |
| MCP registry、relay、proxy | `cloud_agent/mcp/`、`cloud_agent/api/mcp_relay.py` |
| Resource sampling 与 Kata guest observer | `cloud_agent_host/observe/`、`deploy/sandbox/guest-observer.*` |
| Source control 与 publisher | `cloud_agent/source_control/` |

任何放宽 network、credential、UID、capability、seccomp、publisher 或 Docker API 边界的变更都需要安全评审和对应的 acceptance test，不能作为普通兼容修复悄悄落地。
