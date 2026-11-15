# 部署方要自己提供的配置只减不增

- 日期：2026-11-15
- 依据：[精简 `.env`](2026-11-03-slim-env.zh-CN.md)、[新部署不需要 `.env`](2026-11-09-no-env-file.zh-CN.md)、
  [环境变量清单与检查](2026-10-29-env-inventory-and-gates.zh-CN.md)
- 实现：`tests/unit/deployment_config_baseline.json`、`tests/support/env_suppliers.py`、
  `tests/unit/test_env_classification.py`、AGENTS.md 第 3 节

## 1. 要解决的问题

#1092、#1098 和 #1137 把「部署方要手写的配置」一路压了下来：控制面不再需要 `.env`，密钥由 `keys` 服务生成，
主机的 `.env` 由 Admin → Servers → Add server 写入。

但三天后合并的 #1150（平台提供的模型）又加了 11 个变量和一个 Compose profile：`model-relay` profile，
`AGENT_SANDBOX_MODEL_RELAY_URL`，`AGENT_MODEL_TUNNEL_*`，`AGENT_MODEL_RELAY_BIND` / `PORT` / `HOSTNAME`。
主机要跑这类 turn，必须有人手工改这台主机的 `.env`、放一把 SSH key，而 server setup 一个都不写。
GitHub relay 之前就是同一个样子。

现有的检查都没拦住它：

- 清单检查（`test_env_classification.py`）只要求每个变量归一类。新变量归进 `deployment`，就过了。
- `test_server_setup_writes_every_variable_a_host_cannot_start_without` 只管 Compose 里用 `:?` 写的必填变量。
  这些新变量都写成了带空默认值的 `:-`，不设也能启动，只是功能不能用，正好绕开了它。

所以问题不在 `.env` 这个机制还在不在，而在于加一个要人手写的值没有任何成本。把 `.env` 删掉也没用：
下一个功能可以改成「在主机上放个文件」或者「加个启动参数」，换个形式又长回来。

## 2. 为什么不把 `.env` 机制整个删掉

有三类值必须留在本地，换个载体也还是同样的东西：

- **启动时绕不开的值。** 主机要先知道控制面在哪，才能连上去拿其他设置；控制面要先连上数据库，才能读设置。
- **安全边界。** AGENTS.md 第 7 节的沙箱隔离由主机本地决定。即使控制面被攻破，也不能远程放松主机的隔离，
  所以这部分不能从数据库下发。
- **紧急覆盖。** 比如出事时设 `AGENT_MODEL_CONNECTIONS_ENABLED=false` 停掉所有新 turn；回滚时，旧版本也只从
  `.env` 读密钥。

所以要守住的是「没有人需要手写配置」，不是消灭文件格式。

## 3. 做法：一个只减不增的基线

`tests/unit/deployment_config_baseline.json` 列出两样东西：

1. **部署方要自己提供的变量。** 清单（`env_inventory.py`）里归为 `deployment`、`operation`、`design`、
   `candidate` 的变量，去掉有代码会提供的：
   - server setup 和主机 bootstrap 写进主机 `.env` 的（沿用 `test_env_examples.py` 原来的扫描）；
   - `keys` 服务生成和保管的控制面密钥（`deployment_keys.STORED`）；
   - 部署脚本导出的（`AGENT_DEPLOYMENT_SHA`）。

   这几条放在 `tests/support/env_suppliers.py`，两个测试共用。
2. **生产 Compose 文件里的 profile**（`deploy/control-plane/`、`deploy/host/` 下的 `.yml`）。profile 就是一个
   部署方必须自己打开的开关。

`test_no_new_value_a_deployment_must_supply` 在三种情况下失败：

- 出现了不在基线里的变量或 profile：提示改成 Admin 设置、心跳下发的主机设置，或者让 server setup 写入；
  实在不行，就加进基线，并在 PR 里写明为什么别的地方都提供不了。
- 基线里的某一项已经有代码提供了，或者仓库里已经没有了：要求把它从基线删掉。基线因此只会变短。
- 基线没排序或者有重复。

这和 #771 的复杂度与文件大小 ratchet、#1143 的 neutrality allowlist 是一个思路。

**起点：** 135 个变量，4 个 profile（`edge`、`github-relay`、`model-relay`、`pgadmin`）。

## 4. 它管不到的

- **它只看名字，不看是否必填。** 一个带默认值、从来没人改的端口变量，和一个不设功能就不能用的 tunnel key，
  在基线里一样各占一行。哪些是真正需要人手写的，还要靠人判断；基线的作用是让每一次新增都在 diff 里看得见。
- **`deployment` 这一类本来就宽。** 镜像构建参数（`CODEX_VERSION`）、发布身份（`BUILD_SHA`）、部署脚本的输入
  （`DEPLOY_SHA`）也在里面。它们大多由 CI 或发布流程设置，不是运维手写的，但也是部署方提供的值，所以照样计入。
- **「谁提供」靠扫描源码认出来。** server setup 写入的变量是用正则从 `compute_setup.py` 和 `host_bootstrap.py`
  里找的，和原来 `test_env_examples.py` 一样；写法变了，扫描会断言失败，不会悄悄放过。
- **发行版自己的配置不在这里。** 发行版在自己仓库里给 `COMPOSE_PROFILES` 等变量填的值，由发行版自己的规则管。

## 5. 接下来

第一批可以从基线里删掉的，是两个 relay 的手工配置：

- 去掉 `model-relay` 和 `github-relay` 两个 profile：服务一直启动，是否提供模型由 Admin → Settings → Models 决定，
  是否转发 GitHub 由对应的设置决定。
- server setup 给每台主机生成 model tunnel 和 GitHub tunnel 的专用 key，在控制面机器上开好只允许转发对应端口的账号，
  并写入 `AGENT_SANDBOX_MODEL_RELAY_URL`、`AGENT_MODEL_TUNNEL_*`、`AGENT_GITHUB_TUNNEL_*` 和 GitHub relay 的 CA。
  已经加好的主机重跑一次 server setup 补上。

那会是一个单独的 PR，到时基线一次减少十几项。不由 server setup 部署的主机（例如发行版自己用部署 workflow
维护的 staging 和 production 主机）仍要由发行版提供这些值，这一点要和发行版一起处理。
