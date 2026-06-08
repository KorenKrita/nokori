# 规则生命周期

[← 返回主文档](../../README.zh-CN.md)

---

## 状态机

```
candidate → active → trusted
      │          │         │
      └──────────┴─────────┴→ suppressed → candidate（仅恢复自动化可做）
                              └→ archived（终态）
```

| 状态 | 参与提醒？ | 会 Gate？ | 怎么来的 |
|------|-----------|-----------|----------|
| `candidate` | 否；只做 shadow / 证据 | 否 | `nokori add` 或冷路径提取 |
| `active` | 是；未观察到有用前最多 WARM | 不会直接 Gate | 冷路径 fast lane 或 shadow 证据推动 |
| `trusted` | 是 | 可能（仅 `gate_eligible`） | 观察到实际有用后由自治生命周期授信 |
| `suppressed` | 否；只做 shadow recovery | 否 | false-positive / harmful 证据 |
| `archived` | 否 | 否 | 用户 dismiss 或归档策略 |

---

## 怎么变 active / trusted

- **手动 `nokori add` 永远创建 `candidate`**。即使 `--severity high_risk`，也不会绕过生命周期。
- **冷路径 fast-lane 直达 active** 要通过 matcher 编译、归档指纹检查、merge policy、synthetic eval 与 cold-fast-lane 阈值。
- **Candidate → active 晋升**通过影子证据；跨多个 session 积累足够影子匹配则不需要 synthetic eval。
- **trusted / gate-capable** 需要自治 posthoc / shadow 证据；`nokori edit --status` 会被刻意拒绝。

---

## 运行时证据与 posthoc

热路径会编译 trigger 数据，检查 required concepts / exclusions，应用动态 IDF trigger evidence，记录完整 fire events，并在 SessionEnd 后排 posthoc 评估。

---

## Project ID

Nokori 用 `git rev-parse --show-toplevel` 找项目根，拼出 `<目录名>-<路径 hash 前 8 位>` 当 project_id。不是 git 目录就退回用 cwd。

### Project / global scope

- `project_scope=project`：本项目 + global 规则
- `project_scope=global`：进入正式池后可在所有项目可见

作用域不是绕过 trust 的捷径。

---

## 维护任务

维护挂在 `SessionStart` 上，按各自间隔到点才跑：

| 任务 | 间隔 | 说明 |
|------|------|------|
| 生命周期迁移 | 每天 | posthoc/shadow 证据更新状态 |
| Candidate 清理 | 最多每 30 天 | 删 20 天的普通 candidate、40 天的 anti_pattern |
| Replacement 恢复检查 | 最多每 90 天 | archived replacement 目标不在就恢复 |
| Session 文件清理 | — | 删结束超 60 天的 registry |
| Hook 合并清理 | — | 删超 24 小时的 claim 文件 |
| Prompt ack 清理 | — | 删超 24 小时的 ack/deferred |
| Fire event 清理 | 最多每 7 天 | 删 30 天前的 fire events |

手动立即执行：

```bash
nokori maintain
```

---

## 数据库

所有规则存放在 SQLite 文件 `rules.db` 中，首次使用时自动创建。换机或升级后打不开，先 `nokori export` 备份。
