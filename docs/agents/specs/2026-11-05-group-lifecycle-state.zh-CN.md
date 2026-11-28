# Sandbox group 的生命周期状态

状态：已实现。host 上每个 sandbox group 的生命周期由显式状态 `GroupLifecycleState` 表示，只能通过转移方法修改。

代码在 `apps/backend/cloud_agent_host/supervisor/`：`group_lifecycle_state.py`（状态与转移）、`group_lifecycle.py`（准入表）。相关：[原生会话、Fork 与共享工作区](2026-08-31-native-session-fork-shared-workspace-design.md) §7、§9。

## 1. 问题与决定

group 的生命周期原来由十几个互相关联的布尔和可选字段表示（`starting`、`stopping`、`snapshotting`、`stop_archive_pending`、`orphan_finalization_pending` 等），分散在六个文件里被赋值。谁设置谁清除全靠调用方自觉，哪些组合可能同时出现也没人写下来。

现在：

- 状态集中在 `GroupLifecycleState`，所有字段只在 `group_lifecycle_state.py` 里赋值，由棘轮测试 `tests/unit/test_group_lifecycle_writes.py` 守住。
- 所有修改都在 `SharedSessionState.lock` 下进行。这把锁是 `RLock`，它的 condition 用来等待转移结束。Docker、marker 和 archive 的 I/O 在锁外做，做完回到锁里重新校验。
- 准入表 `ADMISSION`（`group_lifecycle.py`）决定当下哪些操作能运行，只读状态，不改状态。
- 成员租约、`operation_tokens`、`terminal_tokens`、`idle_deadline`、`network_proof` 不在这个状态里，准入只把它们当作"有没有占用者"来读。

## 2. 状态模型

### 2.1 四个槽

进行中的操作是四个槽，每个槽至多一个持有者，持有者记为 `begin_*` 返回的 `Hold`：

| 槽 | 持有者 |
|---|---|
| `start` | 一次启动 |
| `stop` | `STOP`（`stop_if_idle`，在自己的 lifecycle check 下）、`FINALIZE`（reconcile 的重试、收尾、blocked 停止）、`SHUTDOWN_RETRY`（关机时的 archive 重试）、`ARCHIVE_HOLD`（管理员 archive 请求，一直持有到 commit 或 abort） |
| `exclusive` | `ENVIRONMENT`（环境准备）或 `INITIAL_ARCHIVE`（初始 archive 捕获）。准入把它当作 `SNAPSHOTTING` |
| `check` | 一次 lifecycle check |

用槽而不是集合：每个槽能证明至多一个持有者，集合表达不了"同一种操作不能有两个"。用单个 Optional 又表达不了按设计会同时成立的组合。结束操作必须交回 `begin` 时拿到的那个 `Hold`，这样"替别人清除"会立刻暴露。

`archive_request_id` 不是独立字段，它由 `stop` 推出：`stop.kind is ARCHIVE_HOLD` 时就是 `stop.request_id`。同一请求重入 `hold_archive_locked` 时返回 `False`，什么都不改。

### 2.2 记录、屏障与运行标签

- 记录：`last_start_failed`（只由启动结束时重写，被拒绝的启动除外；停止不清除）、`container_lost`（只会置真，lost 的 group 只会被 `commit_archive` 移除或被新的注册替换）。
- 屏障：
  - `final_archive`：停止后欠下的 final archive，含成员快照和 `blocked` 标志。
  - `orphan`：孤儿收尾，含 `marker_unreadable` 标志。
  - `start_completion_pending`：启动时欠下的 archive 完成步骤。
  - `initial_preservation`：初始 archive 捕获失败后需要保留冻结字节。
- 运行标签 `RunLabel`：`start_seq`、`unfenced`、`sealed_seq`。`sealed_seq` 只增不减。
- `generation` 是 fence 时钟，在检查开始和结束、archive 请求获取和释放、`container_lost` 首次置真时推进。

线上字段和日志里的名字不变。`SharedSessionDescriptor.snapshotting`、`lifecycle_pending` 和删除诊断里的屏障名都由新字段推出。

## 3. 可达组合

记 S = `start`，T = `stop`，N = `exclusive`（快照屏障），C = `check`。

| 组合 | 可达吗 |
|---|---|
| S + N | 可达。工作区引用准入 `MUTATION` 后，初始 archive 拿到 N，接着引用的启动拿到 S，因为 `START` 不看 N。环境准备期间的成员激活启动同理 |
| N + T | 可达。初始 archive 冻结后捕获失败，自己调 `stop_if_idle`；网络失守的 fail-stop 也一样 |
| N + C、T + C、N + T + C | 可达。普通停止和 reconcile 在 C 下设置 T，`ARCHIVE_HOLD` 期间 `commit_archive` 会再开 C |
| S + C、S + N + C | 只在 `container_lost` 时可达。`commit_archive` 在 `archive_request_id` 为空且容器 lost 时不经准入就开 C，随后 `DELETION` 抛 `RetainedChatLost` |
| S + T | 不可达 |

除 `ARCHIVE_HOLD` 外，T 的持有者同时持有 C，并在结束 C 之前清掉 T。`ARCHIVE_HOLD` 非空蕴含 T。

S + T 不可达靠准入表保证：关机重试用 `SHUTDOWN_TAKE_STOP`，在 `TAKE_STOP` 之外还拒绝 S 和 C。以前这靠 `sandbox_manager.py` 的关机顺序（先排空请求，再 join 后台扫描）间接排除，现在不依赖调用顺序。代价：关机时一个启动还在进行，它的 group 记进 `failures`，不再被关机并发重试。被拒的那次启动自己的 fail-stop 上传若失败，group 保持停止，网桥和待完成标记留着，由下一次启动后的 reconcile 完成 final archive。

`last_start_failed` 保留到下一次启动。它只影响不带 `retry_failed` 的 `wait_for_start_locked` 调用方（它们总在一次带 `retry_failed=True` 的启动之后，读到的是那次启动的结果），以及"已就绪"捷径和网络证明复用：它们因此改走完整的准备流程或重新证明网络，不会拒绝请求。

## 4. 不变量

转移的前置条件和这些不变量由 `check_invariants()` 检查：

- 启用 final archive 时，`blocked` 蕴含欠下 final archive 且带成员（`members` 为空只在不启用时出现）。
- `STOP` 或 `FINALIZE` 持有 T 时必然持有 C，且 C 在 T 之后结束。
- 不会同时有 S 和 T（U1）。
- `initial_preservation` 蕴含 N，且 N 属于初始 archive 自己的 hold（I5）。
- `start_completion_pending` 只在 S 下才能从假变真。
- `final_archive` 的成员与 `pending` 同时存在，`orphan.marker_unreadable` 不能脱离 `orphan`：这两条由数据结构的形状保证。
- `sealed_seq` 只增不减，`unfenced` 在一次运行内只会从假变真：由 `begin_run_locked`、`join_run_locked` 的写法保证。

前置条件不满足时，生产里记一条 `group_lifecycle_violation`，按旧写法继续，避免新检查本身拖垮 host。测试里 `set_strict_checks(True)`（由 `tests/unit/conftest.py` 打开）：违反前置条件抛 `AssertionError`，每次转移先断言调用方持有锁（用 `Condition.notify(0)`，不碰私有的 `RLock._is_owned()`），转移后断言不变量，违反时抛 `LifecycleInvariantError`。生产里违反的不变量以 `violation:<名字>` 加入 `CombinationCounts`（每个进程首次见到某个组合时记 `group_lifecycle_combination`，按小时汇总），用于核对本文的可达性表。

## 5. 转移规则

每个转移方法以 `_locked` 结尾，只做三件事：检查前置条件、写字段、需要时 `notify_all`。准入仍由 `GroupLifecycle.admit` 负责，转移不做准入。

- 启动：`begin_start_locked` / `end_start_locked(hold, outcome)`（`COMPLETED`、`FAILED`、`REFUSED`）；`begin_run_locked(start_seq)` 对 ≤ `sealed_seq` 的 fence 抛 `StaleStartFence`；`owe_start_completion_locked` / `settle_start_completion_locked`。
- 停止：`begin_stop_locked(kind)` / `end_stop_locked(hold)`；`hold_archive_locked(request_id)` / `release_archive_locked(request_id)`。
- 独占：`begin_exclusive_locked(kind)` / `end_exclusive_locked(hold)`；`preserve_initial_locked` / `release_initial_locked` 都要求持有自己的 hold。`GroupLifecycle.admit_as(hold, operation)` 让持有者自己的准入不把自己算作 `SNAPSHOTTING`。
- 检查：`begin_check_locked` / `end_check_locked(check)`，自己的 T（`STOP`、`FINALIZE`）必须先结束。
- 屏障：`owe_final_locked(members, blocked=...)`（`start`、`stop`、`check` 至少一个不为空）、`refresh_final_members_locked`、`owe_orphan_locked`、`marker_read_locked`、`settle_orphan_locked`、`settle_archive_locked`。
- `mark_lost_locked`、`accepted_stopped_locked`、`advance_generation_locked`。

### 4 条已决定的行为

- `accepted_stopped_locked`（接受一次 STOPPED 的 inspection）只在 N 属于 `INITIAL_ARCHIVE` 且 `initial_preservation` 时才清这两项，从不替别人释放 N。preservation 随停止清掉，因为停止已经保住了冻结的字节。如果初始 archive 捕获在记账时发现自己的 hold 已被释放，就不再重新置真 preservation，不碰别人的槽，`notify_all`，并记 `sandbox_initial_archive_preservation_released_by_stop`。
- `join_run_locked` 对 ≤ `sealed_seq` 的 fence 不计入运行标签，标签保持原样，记 `sandbox_run_join_below_sealed_checkpoint`，请求照常准入。拒绝会把一个无害的请求变成 409，原来的行为也是 fail-closed 的，只是标签可能低于 seal。
- 过期的环境准备不能重新启动已停止的 group。`prepare_environment` 在 `ensure_running` 之前、在锁内检查成员租约仍是 `activation_owner`，否则抛 `SessionLeaseLost("Environment preparation lease was lost")`。`start_group` 也接收 `activation_owner`，在 `_SAME_RUN` 时于 Docker 启动之前在锁内再查一次，被拒的启动记为 `REFUSED`，不算启动失败。原因是：一次已被 `stop_if_idle(reason="orphaned_attempt")` 清掉租约的 attempt，迟到地请求准备环境时，如果启动了已停止的容器，运行中的 group 仍带着旧 fence，控制面会把旧 checkpoint 当作覆盖了这个 group。
- `ensure_running` 里的 `admit(Operation.RUN)` 和对 `group.inspection` 的写入在锁内进行，Docker 启动在锁外，结果回到锁内写回。

`MUTATION` 目前不直接拒绝 `CONTAINER_LOST`，由调用方随后调 `accept_inspection_locked` 抛错。把它放进准入表会让错误类型从随后的 `RetainedChatLost` 变成准入时直接抛，调用方的处理需要先核对，所以没有改。

## 6. 测试

- `test_group_lifecycle.py`：对 `ADMISSION` 逐行测试。
- `test_group_lifecycle_state.py`：每个转移的前置条件满足、不满足和在记录或屏障下调用三类情形。
- `test_group_lifecycle_exhaustive.py`：状态空间有限，直接枚举（`start`、`stop` 持有者、`exclusive` 持有者、`check`、各记录与屏障取值、成员占用者、`inspection.running`、`archives_finals`，共约九万个状态）：
  1. 准入结果与冻结的旧判断（测试里保留的一份参照）逐状态逐操作一致；
  2. 从满足已证明不变量的每个状态出发调用每个转移，要么前置条件拒绝，要么结果仍满足不变量；
  3. 从初始状态出发按转移 API 做广度优先搜索（只有 `admit` 放行才调用 `begin_*`），得到的操作组合集合正好等于 §3 的表。以后有人加转移、产生新组合，这个测试会失败，直到同时更新本文。
- `test_lifecycle_race_evidence.py`：在真实 supervisor 上复现过的几个竞态（初始 archive preservation 被别人的停止释放、`join_run` 低于 sealed checkpoint、过期环境准备重启已停止的 group、关机与进行中的启动）各有一条回归用例。
- `test_group_lifecycle_writes.py`：AST 扫描，断言 `cloud_agent_host/` 里除 `group_lifecycle_state.py` 和 `group_lifecycle.py` 外，没有任何语句给生命周期字段赋值，也没有 `setattr`。
- `test_shared_session_supervisor_lifecycle.py` 等并发测试的断言不因这个状态改变。
