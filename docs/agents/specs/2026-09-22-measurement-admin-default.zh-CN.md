# Measurement 默认可控制，采集默认 Disabled

状态：已实现。

## 用户行为

部署并完成数据库迁移后，管理员打开 **Admin → Measurements**：

- 没有活动实验时，页面显示 **Disabled** 和 **Enable measurement**。
- 点击 Enable，选择操作和 host，开始选取匹配的操作。持续采集直到 Disable，不选时长。
- 点击 Disable，停止选取新操作。在途测量正常完成，历史样本保留。
- 关闭页面不改变开关。服务重启读取持久化状态，不创建新实验。已有定时实验仍按原期限停止。

使用开关不需要 profiling 环境变量，也不需要重启或部署。Enabled 之后，要有沙盒启动、文件访问或终端操作才会产生样本，开关本身不制造业务操作。

## 决策

原先没有配置 `MODE` 时，既不采集，也不允许 Admin 控制，所以页面显示 Unavailable，没有按钮。

现在把**控制方式**和**采集状态**分开：默认控制方式是 Admin；初始持久化状态没有活动实验，采集仍是 Disabled。这不等于默认打开 profiling，前端也不伪造按钮。

CP、runner、sandbox manager 和 turn 子进程都用共享模块的 `profile_mode()` 解析默认值。空串和未设置行为相同。设计不需要新表、新 API 或新协议，初始关闭的控制状态来自迁移 0018。

## 可选的运维覆盖

| 输入 | 控制方式 | 结果 |
| --- | --- | --- |
| 不设置 `MODE` 或空白；旧 `ENABLED` 未设置或为 false | Admin | 开关可用，没有活动实验就不采集 |
| 不设置 `MODE`，旧 `ENABLED=true` | Environment | 沿用静态实验，仍须有合法 RUN |
| `MODE=admin` | Admin | 忽略旧 ENABLED/RUN，以持久化实验为准 |
| `MODE=environment` | Environment | 只用静态实验配置 |
| `MODE=disabled`，或无效值 | 禁止采集 | Admin 不能启用，也拒绝在途 context；无效值会告警 |

这些覆盖项服务于已有的静态实验和运维禁用，普通部署不需要。

`AGENT_OPERATION_PROFILING_DEPLOYMENT` 是可选的数据库命名空间：一个数据库对应一个部署时用默认值；多个独立部署共用数据库时要用不同名称。它不控制采集开关。

## 进程与边界

- CP 从数据库读配置，runner 通过已有的认证端点同步，sandbox manager 从 runner 接收配置。
- 新的根操作只从当前有效快照选取。没有活动实验、没取到策略或策略不合法，都不创建测量。
- 默认模式下，子进程和 sandbox manager 可以延续可信通道传来的合法选择，不必先自己选中实验。它们仍校验原 manifest、操作范围和截止时间，也不占第二份名额。
- 显式的部署级 `disabled` 拒绝接续，这和 Admin 上的 Disable 不同。
- 权限不变：Admin 写接口需要管理员身份，runner 和 sandbox manager 走已有的认证通道。
- 数据库不可用或缺迁移时，接口报告真实错误，不伪造 Disabled。

## 关闭时的成本

没有活动实验时，业务入口不创建 measurement、span 或导出记录，也不为每次操作查数据库。后台配置同步仍在运行：空闲约 30 秒一次，活动时约 5 秒，出错重试约 10 秒。

按钮表示持久化配置，不保证所有节点同时生效。持续模式下断联的节点要等同步恢复才收到 Disable。持续模式没有累计样本上限，导出队列有容量限制。
