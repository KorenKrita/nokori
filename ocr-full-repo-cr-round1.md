[ocr] Summary: 289 file(s) reviewed, 45 comment(s), ~4385089 token(s) used (input: ~4166557, output: ~218532), cache(read: ~3480576, write: ~0), 9m45s elapsed

[2m─── .github/workflows/publish-pypi.yml:2-2 ───[0m
工作流注释引用了 `.github/PUBLISHING.md`，但该文件在仓库中不存在。缺少此文档可能导致维护者不清楚 PyPI 发布流程（如 API Token
的配置方式、发布前需要满足的条件等）。建议补充该文档或在注释中直接说明关键信息。

[91m[48;2;70;0;0m-[0m[48;2;70;0;0m # API token lives in GitHub Secrets (never in the repo). See .github/PUBLISHING.md.[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m # API token lives in GitHub Secrets (never in the repo).[0m[0m


[2m─── nokori/_packaging.py:0-0 ───[0m
在 PEP 517 构建流程下（默认 `pip install .`），setuptools 会走 `build_wheel` 而非 `install`
命令。`InstallWithPrefetch` 可能永远不会被触发，post-install 预取对非 editable 安装无效。建议同时重写 `bdist_wheel` 命令，或在 wheel
安装后通过其他机制（如 entry-points、`.pth` 文件）触发预取。

[92m[48;2;0;60;0m+[0m[48;2;0;60;0m from setuptools.command.bdist_wheel import bdist_wheel[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m [0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m [0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m class BdistWheelWithPrefetch(bdist_wheel):[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m     """``pip install .[local-embed]`` – PEP 517 wheel build."""[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m [0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m     def run(self) -> None:[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m         super().run()[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m         _prefetch_after_pip()[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m [0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m [0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m class InstallWithPrefetch(install):[0m[0m
[91m[48;2;70;0;0m-[0m[48;2;70;0;0m     """``pip install .[local-embed]`` (non-editable)."""[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m     """``pip install .[local-embed]`` (non-editable, legacy / --no-build-isolation)."""[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m [0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m     def run(self) -> None:[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m         super().run()[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m         _prefetch_after_pip()[0m[0m


[2m─── nokori/cli.py:179-181 ───[0m
`_build_parser()` 在 `main()` 和 `_dispatch()` 中各调用一次（当用户不带子命令时触发后者）。每次调用都会重新构建完整的 ArgumentParser 及所有子
parser，产生不必要开销。建议将 parser 实例在模块级缓存，或由 `main()` 传入 `_dispatch()`。

[2m[48;2;38;38;38m [0m[48;2;38;38;38m     if cmd is None:[0m[0m
[91m[48;2;70;0;0m-[0m[48;2;70;0;0m         _build_parser().print_help()[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m         parser.print_help()[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m         return 0[0m[0m


[2m─── nokori/cli.py:177-179 ───[0m
`_dispatch` 中将近 20 个 `if cmd == ...` 分支组成的长链不利于维护和扩展。建议使用命令字典映射替代，例如 `_COMMANDS = {'add':
(commands.add, 'run'), ...}`，通过 `_COMMANDS[cmd][0].__dict__[_COMMANDS[cmd][1]](args, cfg)`
或更清晰的结构来调度。这样新增命令时只需追加一行映射，而无须修改 `_dispatch` 的控制流。

[92m[48;2;0;60;0m+[0m[48;2;0;60;0m _COMMAND_MAP: dict[str, Callable[[argparse.Namespace, Config], int]] = {[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m     "add": lambda a, c: _import_run("nokori.commands.add", a, c),[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m     # ...[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m }[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m [0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m [0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m def _dispatch(args: argparse.Namespace, cfg: Config) -> int:[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m     cmd = args.command[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m     if cmd is None:[0m[0m


[2m─── nokori/cold/roles.py:322-325 ───[0m
`_validate_type` 递归校验没有深度限制。当前所有角色 schema 深度较浅（≤3层），暂不会触发问题；但若未来 LLM 返回恶意构造的深层嵌套 JSON（如
`[[[[...]]]]`），可能导致 `RecursionError` 并终止进程。建议添加 `max_depth` 参数（如 20），超出时返回错误而非递归。

[91m[48;2;70;0;0m-[0m[48;2;70;0;0m def _validate_type(value: Any, schema: dict[str, Any], path: str) -> list[str]:[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m def _validate_type(value: Any, schema: dict[str, Any], path: str, _depth: int = 0) -> list[str]:[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m     """Minimal JSON schema type/required/enum validation. Returns error messages."""[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m     if _depth > 20:[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m         return [f"{path}: exceeded max validation depth"][0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m     errors: list[str] = [][0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m     expected_type = schema.get("type")[0m[0m


[2m─── nokori/cold/roles.py:412-413 ───[0m
`_normalize_role_output` 直接对传入字典做 `pop` / 赋值等原地修改操作。当前该函数仅被 `validate_role_output` 调用且传入的是
`json.loads` 新创建的字典，暂无实际副作用；但作为内部工具函数缺少行为文档说明，若未来有其他调用方复用且持有原字典引用，容易出现难以追踪的 bug。建议在 docstring
中明确标注“会修改传入的 dict”，或改为返回浅拷贝后的新字典。

[2m[48;2;38;38;38m [0m[48;2;38;38;38m def _normalize_role_output(role: str, data: dict[str, Any]) -> dict[str, Any]:[0m[0m
[91m[48;2;70;0;0m-[0m[48;2;70;0;0m     """Fix common LLM schema deviations before validation."""[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m     """Fix common LLM schema deviations before validation.[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m [0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m     NOTE: This function mutates *data* in-place. Callers should not rely[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m     on the original dict remaining unchanged.[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m     """[0m[0m


[2m─── nokori/cold/roles.py:412-414 ───[0m
`_normalize_role_output` 中多个角色的规范化边界路径（如 `admission_judge` 分数扁平化 + 字符串转浮点、`merge_planner` 的
`target_rule_ids` 字符串展开、`rule_rewriter` 字段别名回退、`posthoc_evaluator` 的 `reason_code`
匹配与丢弃逻辑）缺少直接测试。这些路径对 LLM 输出可靠性至关重要，建议在 `tests/test_llm_roles.py` 中补充覆盖这些规范化场景的测试用例。



[2m─── nokori/cold/pipeline.py:1089-1092 ───[0m
当 `target_rule_ids` 为空时，`next(...)` 会回退到 `existing_rules[0]`，但该规则可能与当前新规则无关。`apply_merge_policy`
会将此无关规则的 `id` 作为
`target_rule_id`，若合并操作是破坏性的（`replace_existing`/`suppress_existing`/`archive_existing`），将错误地修改或归档该无关规
则。

建议：当 `target_rule_ids` 为空时，将 `existing` 设为 `None`，让 `apply_merge_policy` 进入 "无现有规则可操作"
的安全回退（`keep_both`）。

[92m[48;2;0;60;0m+[0m[48;2;0;60;0m         # When the planner identifies no specific target, treat as no relevant[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m         # existing rule to prevent destructive operations on unrelated rules.[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m         existing = next([0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m             (r for r in existing_rules if r["id"] in target_ids),[0m[0m
[91m[48;2;70;0;0m-[0m[48;2;70;0;0m             existing_rules[0],[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m             None,[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m         )[0m[0m


[2m─── nokori/cold/pipeline.py:1777-1790 ───[0m
`_handle_split_required` 通过 `run_cold_pipeline` 递归调用自身，但没有递归深度限制。若 LLM 对分割后的子规则再次返回
`split_required`，将形成无限递归导致栈溢出。

建议：增加 `_recursion_depth` 参数，初始值 0，递归时 +1，超过阈值（如 3）时直接拒绝而不是继续分割。同时需要将深度参数从 `_run_cold_pipeline_inner`
逐层传递到 `_handle_split_required`。

[2m[48;2;38;38;38m [0m[48;2;38;38;38m def _handle_split_required([0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m     db: Db,[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m     llm,[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m     rule_data: dict[str, Any],[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m     role_models: dict[str, str] | None,[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m     default_model: str | None,[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m     transcript_ref: str,[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m     source_origin: SourceOrigin,[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m     idf_stats: IdfPoolStats | None,[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m     global_adversarial_cases: list[dict[str, Any]] | None,[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m     role_max_tokens: dict[str, int] | None = None,[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m     role_timeouts: dict[str, int] | None = None,[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m     project_id: str | None = None,[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m     _split_depth: int = 0,[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m ) -> list[ColdPipelineResult]:[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m     if _split_depth >= 3:[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m         log.warning("split_required recursion depth exceeded; rejecting")[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m         return [][0m[0m


[2m─── nokori/archive/fingerprints.py:226-238 ───[0m
性能风险：`_find_related_fingerprint` 使用 `db.fetchall` 无过滤条件地拉取 `archived_fingerprints` 表中所有
user/system/replacement 强度的行，然后在 Python 中逐一计算 token 重叠度。随着归档指纹累积，每次规则入库都会触发全表扫描和 Python 侧 O(n)
的模糊匹配，严重影响冷路径吞吐。

建议方案（按优先级）：
1. 在 `archived_fingerprints` 表上增加 `(archive_strength)` 单列索引以减少初始过滤成本，但无法根本解决行数增长问题；
2. 考虑将模糊匹配下推到 SQLite 中（如利用 FTS5 全文索引做 token 级别预筛选），或限制拉取行数（如加 LIMIT + ORDER BY created_at DESC）仅对最近
N 条指纹做模糊匹配；
3. 若归档指纹量预期不大（<1000），加注释说明设计假设并补充 LIMIT 兜底。

[2m[48;2;38;38;38m [0m[48;2;38;38;38m def _find_related_fingerprint([0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m     db: Db, trigger_canonical: str, action_instruction: str[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m ):[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m     new_tokens = _content_tokens(f"{trigger_canonical} {action_instruction}")[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m     if not new_tokens:[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m         return None[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m     # NOTE: Full scan of archived_fingerprints — acceptable while row count[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m     # stays small (< ~500). If growth becomes a concern, add FTS or LIMIT.[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m     rows = db.fetchall([0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m         "SELECT id, signature, scope_summary, blocked_trigger_area, "[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m         "blocked_action_area, archive_strength, "[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m         "can_be_overridden_by_changed_scope, rule_id, created_at "[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m         "FROM archived_fingerprints "[0m[0m
[91m[48;2;70;0;0m-[0m[48;2;70;0;0m         "WHERE archive_strength IN ('user','system','replacement')",[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m         "WHERE archive_strength IN ('user','system','replacement')"[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m         "ORDER BY created_at DESC LIMIT 500",[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m     )[0m[0m


[2m─── nokori/cold/pipeline.py:864-866 ───[0m
改写器的 system prompt 要求 LLM 输出 `concepts` 键，但 `_compile_concept_group`（编译器）实际读取的是 `all_of` 键。改写后的规则在
Stage f（编译）会因 `all_of` 为空而抛出 `CompilationError`，导致所有经过 rewrite 路径的候选规则被拒绝。

建议：将 prompt 中的 `"concepts"` 修正为 `"all_of"`，与 `_draft_concept_groups` 和编译器保持一致。

[2m[48;2;38;38;38m [0m[48;2;38;38;38m         "- \"required_concept_groups\" (array of objects, REQUIRED): each object has:\n"[0m[0m
[91m[48;2;70;0;0m-[0m[48;2;70;0;0m         "  - \"concepts\" (array of strings): terms that must all be present\n"[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m         "  - \"all_of\" (array of strings): concept IDs that must all be present\n"[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m         "  - \"match\" (string): \"all\" or \"any\"\n"[0m[0m


[2m─── nokori/cold/pipeline.py:882-883 ───[0m
改写器 prompt 的示例 JSON 中也使用了错误的键名 `concepts`，应与编译器期望的 `all_of` 保持一致。

[2m[48;2;38;38;38m [0m[48;2;38;38;38m         "  \"required_concept_groups\": [\n"[0m[0m
[91m[48;2;70;0;0m-[0m[48;2;70;0;0m         "    {\"concepts\": [\"git push\", \"force\"], \"match\": \"all\"}\n"[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m         "    {\"id\": \"grp1\", \"all_of\": [\"concept_0\"], \"match\": \"all\"}\n"[0m[0m


[2m─── nokori/cold/jobs.py:142-152 ───[0m
**【高】Type 1 断路器冷却期判断使用最近一次作业（不限状态）的 `updated_at`，而非最近一次失败作业的时间。**

当失败率 ≥ 50% 触发断路器后，若后续有成功的作业持续进入，每次成功都会更新 `updated_at`，使得 `rows[0]['updated_at']`
始终是最近的（成功）作业时间，冷却期永远无法度过（`elapsed < 300` 始终成立），导致断路器被锁死在打开状态，整个角色的 LLM 调用被无限期阻塞。

**修复建议**：冷却期应以最近一次 *失败* 作业的 `updated_at` 为基准：
```python
last_failure = next((r["updated_at"] for r in rows if r["status"] == "failed"), None)
if last_failure:
    recent_dt = datetime.fromisoformat(last_failure.replace("Z", "+00:00"))
    ...
```

[2m[48;2;38;38;38m [0m[48;2;38;38;38m         if failure_rate >= CIRCUIT_BREAKER_RATE_THRESHOLD:[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m             # Check cooldown: if most recent failure is within cooldown, breaker open[0m[0m
[91m[48;2;70;0;0m-[0m[48;2;70;0;0m             most_recent = rows[0]["updated_at"] if rows else None[0m[0m
[91m[48;2;70;0;0m-[0m[48;2;70;0;0m             if most_recent:[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m             last_failure = next([0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m                 (r["updated_at"] for r in rows if r["status"] == "failed"), None[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m             )[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m             if last_failure:[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                 try:[0m[0m
[91m[48;2;70;0;0m-[0m[48;2;70;0;0m                     recent_dt = datetime.fromisoformat(most_recent.replace("Z", "+00:00"))[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m                     recent_dt = datetime.fromisoformat(last_failure.replace("Z", "+00:00"))[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                     elapsed = (datetime.now(timezone.utc) - recent_dt).total_seconds()[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                     if elapsed < CIRCUIT_BREAKER_COOLDOWN_SECONDS:[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                         return True[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                 except (ValueError, TypeError):[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                     return True[0m[0m


[2m─── nokori/cold/jobs.py:102-111 ───[0m
**【中】`mark_job_failed` 未对重试次数设置上限。**

`retries` 字段无限增长会导致两个问题：
1. 数据库列值膨胀（`INTEGER` 理论上可达 2^63，但过度重试浪费资源）。
2. `_retry_backoff_seconds` 中 `2**retries` 虽然被 cap 在 900 秒，但每次 `2**retries` 计算在 Python
中仍会产生无意义的大整数对象。

此外，当 `next_retry_at` 到期后若存在后台重试调度器，无上限重试会导致作业永久处于 `failed` 状态反复重试而不转为终态 `dead` 或 `permanent_failed`。

**修复建议**：添加上限常量并在达到上限时将状态设为永久失败：
```python
MAX_JOB_RETRIES = 6  # 2^6 * 30 = 1920 → capped at 900s

if new_retries > MAX_JOB_RETRIES:
    tx.execute("UPDATE llm_jobs SET status = 'dead', updated_at = ? WHERE id = ?", (now, job_id))
    return
```

[92m[48;2;0;60;0m+[0m[48;2;0;60;0m MAX_JOB_RETRIES = 6[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m [0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m def mark_job_failed(db: Db, job_id: str, error_info: str | None = None) -> None:[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m     """Increment retries, set next_retry_at. Stores error_info in output_json for circuit breaker classification."""[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m     now = _now_iso()[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m     with db.transaction() as tx:[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m         row = tx.execute([0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m             "SELECT role, retries FROM llm_jobs WHERE id = ?", (job_id,)[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m         ).fetchone()[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m         if row is None:[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m             return[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m         new_retries = row["retries"] + 1[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m         if new_retries > MAX_JOB_RETRIES:[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m             tx.execute([0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m                 "UPDATE llm_jobs SET status = 'dead', updated_at = ? WHERE id = ?",[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m                 (now, job_id),[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m             )[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m             return[0m[0m


[2m─── nokori/cold/jobs.py:146-147 ───[0m
**【中】时间字符串解析依赖手动 `replace("Z", "+00:00")`，格式假设脆弱。**

本项目存在两个 `_now_iso()` 实现：
- `nokori/cold/jobs.py` 使用 `strftime("%Y-%m-%dT%H:%M:%SZ")` → 输出 `2026-06-07T14:01:00Z`
- `nokori/archive/fingerprints.py` 使用 `.isoformat(timespec="seconds")` → 输出
`2026-06-07T14:01:00+00:00`

虽然当前仅有 `jobs.py` 写入 `llm_jobs` 表，但若未来任何模块以不同格式写入 `updated_at`，`fromisoformat` 将抛出 `ValueError`，被
`except` 捕获后直接返回 `True`（断路器打开），造成误判。

**修复建议**：统一时间格式工具函数到公共模块（如 `nokori/utils/time.py`），并在此处使用健壮的解析方案（如
`datetime.fromisoformat(ts.replace("Z", "+00:00"))` 本身已能正确处理大多数 ISO 8601 变体，但核心问题是确保写入侧格式一致）。



[2m─── nokori/cold/jobs.py:156-158 ───[0m
**【低】Type 2 断路器硬编码 `LIMIT 5`，未使用语义化常量。**

Type 1 使用 `CIRCUIT_BREAKER_SAMPLE_SIZE = 10` 作为采样窗口，Type 2 却硬编码 `LIMIT 5`。虽然两者的阈值语义不同（角色失败率 vs
认证/限流失败次数），但应使用独立命名常量（如 `PROVIDER_AUTH_SAMPLE_SIZE = 5`）以提高可维护性和可配置性。

[92m[48;2;0;60;0m+[0m[48;2;0;60;0m PROVIDER_AUTH_SAMPLE_SIZE = 5[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m [0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m ...[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m         provider_rows = db.fetchall([0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m             "SELECT status, output_json, updated_at FROM llm_jobs WHERE model_id = ? "[0m[0m
[91m[48;2;70;0;0m-[0m[48;2;70;0;0m             "ORDER BY updated_at DESC LIMIT 5",[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m             "ORDER BY updated_at DESC LIMIT ?",[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m             (model_id, PROVIDER_AUTH_SAMPLE_SIZE),[0m[0m


[2m─── nokori/cold/pipeline.py:1968-1973 ───[0m
CAS 更新后未检查 `rowcount`。若并发修改导致 CAS 失败（`rowcount == 0`），函数会静默返回，调用方无法感知更新未生效。

建议：参考 `_apply_merge_side_effects` 的实现，检查 `tx.execute(...).rowcount` 并在失败时记录警告或抛出异常。同一函数中 synthetic
eval 失败后的 revert 更新也存在相同问题。

[2m[48;2;38;38;38m [0m[48;2;38;38;38m         with db.transaction() as tx:[0m[0m
[91m[48;2;70;0;0m-[0m[48;2;70;0;0m             tx.execute([0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m             cur = tx.execute([0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                 f"UPDATE rules SET {', '.join(updates)} "[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                 f"WHERE id = ? AND rule_version = ? AND status = ? {rpv_where}",[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                 tuple(params) + rpv_params,[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m             )[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m             if cur.rowcount != 1:[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m                 log.warning([0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m                     "non_destructive_merge CAS failed for rule=%s version=%s",[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m                     target_rule_id, current_version,[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m             )[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m                 return[0m[0m


[2m─── nokori/archive/fingerprints.py:288-292 ───[0m
边界条件不完整：`_is_narrower_scope` 仅当 `scope_summary == "general"`（精确匹配）时识别结构收窄。但
`create_archived_fingerprint_from_data` 在 `domain_tags` 非空时生成的 `scope_summary` 格式为
`"domain:foo,bar"`，而非 `"general"`。

这导致两种遗漏：
1. 当旧指纹 `scope_summary="domain:python,testing"` 且新规则
`domain_tags=["python"]`（子集，真正的收窄）时，`has_structural_narrowing` 为 False，只能依赖 token 维度判定，可能漏判；
2. 测试 helper `_create_fp` 使用 `domain_tags=["general"]` 生成 `scope_summary="domain:general"`，与生产路径的
`domain_tags=[]` → `scope_summary="general"` 不一致，导致测试无法覆盖结构收窄路径。

建议：将判断改为 `scope_summary == "general" or (new_domain_tags and
set(new_domain_tags).issubset(_parse_domain_tags(scope_summary)))`，使结构收窄检测覆盖所有域标签子集关系。

[91m[48;2;70;0;0m-[0m[48;2;70;0;0m     has_structural_narrowing = ([0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m     has_structural_narrowing = bool([0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m         new_domain_tags[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m         and len(new_domain_tags) > 0[0m[0m
[91m[48;2;70;0;0m-[0m[48;2;70;0;0m         and scope_summary == "general"[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m         and ([0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m             scope_summary == "general"[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m             or ([0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m                 scope_summary.startswith("domain:")[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m                 and set(new_domain_tags).issubset([0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m                     set(scope_summary[len("domain:"):].split(","))[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m                 )[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m             )[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m         )[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m     )[0m[0m


[2m─── nokori/cold/pipeline.py:468-469 ───[0m
当 `merge_op` 是破坏性操作但 `merge_info` 中缺少 `existing_rule` 时，`target_rule_id` 会是
`None`。`validate_merge_transaction` 不会直接访问 `existing_rule`（安全），但后续 `_apply_merge_side_effects` 因
`target_rule_id=None` 提前返回，导致新规则已插入但旧规则未被归档/抑制，造成状态不一致。

建议：在进入破坏性合并逻辑之前，增加显式检查确保 `existing_rule` 非空。

[92m[48;2;0;60;0m+[0m[48;2;0;60;0m     # --- Ensure destructive merge ops have a target rule ---[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m     if merge_op in DESTRUCTIVE_MERGE_OPS and not existing_rule:[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m         return ColdPipelineResult([0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m             status="rejected",[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m             rule_id=None,[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m             rejection_reason="merge_transaction_invalid: no existing rule for destructive merge",[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m             scores=scores,[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m         )[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m [0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m     # --- Handle non-destructive merge ops that update existing rules ---[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m     if merge_op in ("merge_into_existing", "update_existing_fields"):[0m[0m


[2m─── nokori/commands/edit.py:31-37 ───[0m
severity 参数缺少合法性校验。根据 models.py，合法值仅限 `reminder`、`high_risk`、`gate_eligible`。当前仅拦截
`gate_eligible`，用户可传入任意非法字符串（如 `high_riskk`），下游 blocker.py 中的 `format_injection` 按 `severity ==
"high_risk"` 排序，非法值将被静默归入最低优先级，导致规则行为异常。建议在 append 前校验 `args.severity` 是否在合法值集合内。

[92m[48;2;0;60;0m+[0m[48;2;0;60;0m         _VALID_SEVERITIES = frozenset({"reminder", "high_risk", "gate_eligible"})[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m         if getattr(args, "severity", None) is not None:[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m             if args.severity not in _VALID_SEVERITIES:[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m                 raise NokoriError([0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m                     f"invalid severity {args.severity!r}; "[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m                     f"must be one of {sorted(_VALID_SEVERITIES)}"[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m                 )[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m             if args.severity == "gate_eligible":[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                 raise NokoriError([0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                     "manual gate_eligible severity changes are disabled in v6; "[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                     "Gate eligibility is assigned autonomously"[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                 )[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m             updates.append(("severity", args.severity))[0m[0m


[2m─── nokori/cold/pipeline.py:1343-1353 ───[0m
审核记录中 `model_id` 和 `input_hash` 硬编码为 `None`，丢失了可追溯信息。`rule_reviews` 表结构允许这两个字段为空，但建议传入实际值以支持审计和调试。

建议：将 `insert_rule_from_pipeline` 的签名扩展，接收这两个参数并传入记录，或从调用上下文（`_run_admission_judge`）传递。

[2m[48;2;38;38;38m [0m[48;2;38;38;38m                 ([0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                     "admission_judge",[0m[0m
[91m[48;2;70;0;0m-[0m[48;2;70;0;0m                     None,[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m                     admission_model_id or None,[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                     PROMPT_VERSIONS.get("admission_judge"),[0m[0m
[91m[48;2;70;0;0m-[0m[48;2;70;0;0m                     None,[0m[0m
[91m[48;2;70;0;0m-[0m[48;2;70;0;0m                     None,[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m                     admission_input_hash or None,[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m                     admission_output_json or None,[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                     dumps_json(scores),[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                     status,[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                     rule_id,[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                     now,[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                 ),[0m[0m


[2m─── nokori/commands/add.py:95-99 ───[0m
**并发安全隐患：短 ID 在事务外生成**

`fetch_short_ids(db)` 和 `short_id_for(rid, existing)` 在事务之前执行。SQLite 的 `short_id TEXT UNIQUE NOT
NULL` 约束意味着：两个并发的 `nokori add` 调用可能拿到相同的现有短 ID 集合，计算出相同的短 ID，导致第二个 INSERT 触发 IntegrityError
而崩溃（未被捕获）。

`export_import.py` 的 `run_import` 有同样的问题，但它在内存中追踪 `existing_short_ids.add(sid)` 来避免同批次内冲突。而 `add`
命令每次只插入一条，无法享受此保护。

**建议**：将 `fetch_short_ids` + `short_id_for` + INSERT 整体移入同一个事务中，或在 INSERT 处捕获 IntegrityError
并重试（如重新计算短 ID 后重试一次）。

[2m[48;2;38;38;38m [0m[48;2;38;38;38m     db = open_db(cfg.db_path)[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m     try:[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m         with db.transaction() as tx:[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m         existing = fetch_short_ids(db)[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m         sid = short_id_for(rid, existing)[0m[0m
[91m[48;2;70;0;0m-[0m[48;2;70;0;0m         with db.transaction() as tx:[0m[0m


[2m─── nokori/commands/add.py:47-48 ───[0m
**aliases 列表缺少去重逻辑**

`aliases` 由 `trigger` 和 `variants` 拼接而成，但未做去重。若 `args.variants` 中包含与 `args.trigger` 相同（或经
`split_csv` strip 后相同）的文本，aliases 会出现重复条目。

对比下方的 `variant_entries` 循环正确使用了 `seen` set 进行去重，aliases 应保持一致的处理方式。重复 alias 可能引起匹配阶段的冗余处理或非预期行为。

**建议**：对 aliases 也进行去重，或在构建时检查是否已存在相同 text。

[92m[48;2;0;60;0m+[0m[48;2;0;60;0m     seen_aliases = {trigger}[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m     aliases = [{"text": trigger, "strength": "strong"}][0m[0m
[91m[48;2;70;0;0m-[0m[48;2;70;0;0m     aliases.extend({"text": v, "strength": "strong"} for v in variants)[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m     for v in variants:[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m         v_stripped = v.strip()[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m         if v_stripped and v_stripped not in seen_aliases:[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m             seen_aliases.add(v_stripped)[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m             aliases.append({"text": v_stripped, "strength": "strong"})[0m[0m


[2m─── nokori/commands/add.py:68-69 ───[0m
**缺少单元测试覆盖**

新增的 `add` CLI 命令没有对应的测试文件（`tests/test_add.py` 不存在）。核心逻辑路径缺少覆盖：
- 输入校验（trigger 长度、action 非空、边界值 `_MAX_TRIGGER` / `_MAX_ACTION`）
- 数据库写入与短 ID 生成
- `_manual_trigger_structure` 和 `_variant_entry` 的结构正确性
- `split_csv` 与 `variants`/`terms_en`/`terms_zh` 的集成
- `--severity` 合法值、`--project-id` 作用域切换

**建议**：参考 `tests/test_export_import.py` 或 `tests/test_cli.py` 的测试模式，新增 `tests/test_add.py` 覆盖上述路径。



[2m─── nokori/cold/pipeline.py:1757-1769 ───[0m
LLM 响应已在 `_validate_eval_cases_response` 回调中完整验证（通过 `validate_response` 参数传入
`_call_llm_role`），但响应解析后又重复执行了相同的验证逻辑（case 检查、positive 检查），这会导致维护时两边容易不一致。

建议：移除下方重复的验证代码块，直接使用已验证的 `case_list`（`_validate_eval_cases_response` 已验证 `has_positive`
约束），或改为从验证回调中返回已验证的 case_list 以复用。



[2m─── nokori/commands/export_import.py:287-291 ───[0m
**并发竞态条件**: `existing_ids` 和 `existing_short_ids` 在事务外查询（第 309-310 行），然后在事务内（第 345 行 `with
db.transaction()`）执行 INSERT。在查询与 `BEGIN IMMEDIATE` 之间的窗口期，另一个并发导入进程可能会插入相同 ID 的记录，导致事务因 UNIQUE
约束冲突而整体回滚。

**建议**: 将查重逻辑移入事务内部，或使用 `INSERT OR IGNORE` / `ON CONFLICT` 让数据库层处理冲突。例如：
1. 将 `existing_ids` 查询移到 `with db.transaction()` 内部
2. 或者在 INSERT 失败时捕获 `IntegrityError` 并跳过冲突记录，而不是让整个事务回滚

[2m[48;2;38;38;38m [0m[48;2;38;38;38m     inserted = skipped = 0[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m     inserted_sids: list[str] = [][0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m     try:[0m[0m
[91m[48;2;70;0;0m-[0m[48;2;70;0;0m         existing_ids = {r["id"] for r in db.fetchall("SELECT id FROM rules")}[0m[0m
[91m[48;2;70;0;0m-[0m[48;2;70;0;0m         existing_short_ids = fetch_short_ids(db)[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m         with db.transaction() as tx:[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m             existing_ids = {r["id"] for r in tx.execute("SELECT id FROM rules").fetchall()}[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m             existing_short_ids = {r["short_id"] for r in tx.execute("SELECT short_id FROM rules").fetchall()}[0m[0m


[2m─── nokori/commands/maintain.py:34-35 ───[0m
Issue 1 (High): `open_db(cfg.db_path)` 位于 `try` 块外部。若数据库打开失败（如路径无效、权限不足），`db` 变量不会被绑定，但 `finally` 中的
`db.close()` 仍会执行，抛出 `NameError` 并掩盖原始异常，导致维护命令崩溃且难以排查根因。

建议：将 `open_db` 移入 `try` 块内，或使用 `try: db = open_db(...)` 并在失败时 return 0。参考 `health.py` 中
`_check_db()` 的嵌套 try/finally 模式。

[91m[48;2;70;0;0m-[0m[48;2;70;0;0m     db = open_db(cfg.db_path)[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m     try:[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m         db = open_db(cfg.db_path)[0m[0m


[2m─── nokori/commands/maintain.py:44-46 ───[0m
Issue 2 (Medium): `_PosthocLLMAdapter(cfg)` 初始化未被异常保护。若 LLMAdapter
构造函数在未来版本中增加校验逻辑导致失败，异常会中断整个维护流程，使后续不需要 LLM 的任务（`expire_stale_ingest_jobs`、IDF 重建）无法执行，降低系统健壮性。

建议：将 `llm = _PosthocLLMAdapter(cfg)` 移入第一个使用它的 `try` 块内，并设置 `llm = None` 初始值，这样若初始化失败，只影响 posthoc 和
shadow 任务，不影响后续流程。

[91m[48;2;70;0;0m-[0m[48;2;70;0;0m         llm = _PosthocLLMAdapter(cfg)[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m         posthoc_summary: dict | None = None[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m         try:[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m             llm = _PosthocLLMAdapter(cfg)[0m[0m


[2m─── nokori/commands/export_import.py:0-0 ───[0m
**缺少规则数量上限约束**: 当前仅限制了文件大小（100 MiB），未对规则数量设置上限。如果导入文件包含数万条规则，`pending` 列表会占用大量内存，且后续单事务批量 INSERT
会长时间持锁（SQLite `BEGIN IMMEDIATE` 获取保留锁），阻塞其他读写操作。

**建议**: 增加规则数量上限（如 `_MAX_IMPORT_RULES = 10_000`），或在 `rules_in` 循环前先检查 `len(rules_in)`。同时考虑分批提交（每 N
条一个子事务）以减少锁持有时间。

[2m[48;2;38;38;38m [0m[48;2;38;38;38m     rules_in = data.get("rules") or [][0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m     if len(rules_in) > _MAX_IMPORT_RULES:[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m         raise NokoriError([0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m             f"import file contains {len(rules_in)} rules, exceeds limit of {_MAX_IMPORT_RULES}"[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m         )[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m [0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m     db = open_db(cfg.db_path)[0m[0m


[2m─── nokori/commands/health.py:206-209 ───[0m
只捕获了 json.JSONDecodeError，未处理 Path.read_text() 可能抛出的 PermissionError、OSError 等 I/O
异常。如果设置文件存在但不可读（如权限问题），健康检查将直接崩溃而非优雅报告失败。建议参考同项目中 install.py 的 _read_json_file() 工具函数，该函数已正确处理
OSError 并包装为 ValueError。可改为捕获 (json.JSONDecodeError, OSError) 或复用 _read_json_file。

[2m[48;2;38;38;38m [0m[48;2;38;38;38m     try:[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m         data = json.loads(settings.read_text(encoding="utf-8"))[0m[0m
[91m[48;2;70;0;0m-[0m[48;2;70;0;0m     except json.JSONDecodeError as e:[0m[0m
[91m[48;2;70;0;0m-[0m[48;2;70;0;0m         return ("fail", f"json parse error: {e}")[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m     except (json.JSONDecodeError, OSError) as e:[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m         return ("fail", f"cannot read {settings}: {e}")[0m[0m


[2m─── nokori/commands/health.py:233-236 ───[0m
与 _check_claude_hooks_registered 相同的问题：只捕获 json.JSONDecodeError，未处理 Path.read_text() 可能抛出的 OSError /
PermissionError 等 I/O 异常。应同时捕获 OSError。

[2m[48;2;38;38;38m [0m[48;2;38;38;38m     try:[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m         data = json.loads(path.read_text(encoding="utf-8"))[0m[0m
[91m[48;2;70;0;0m-[0m[48;2;70;0;0m     except json.JSONDecodeError as e:[0m[0m
[91m[48;2;70;0;0m-[0m[48;2;70;0;0m         return ("fail", f"json parse error: {e}")[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m     except (json.JSONDecodeError, OSError) as e:[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m         return ("fail", f"cannot read {path}: {e}")[0m[0m


[2m─── nokori/commands/show.py:0-0 ───[0m
若 `open_db()` 抛出异常（如路径无效、DB 迁移失败），`db` 变量不会被赋值，`finally` 块中的 `db.close()` 将引发
`NameError`，掩盖原始异常，影响错误诊断。建议将 `db = open_db(...)` 移入 `try` 块，并在 `finally` 中检查 `db` 是否已绑定。

[91m[48;2;70;0;0m-[0m[48;2;70;0;0m     db = open_db(cfg.db_path)[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m     db = None[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m     try:[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m         db = open_db(cfg.db_path)[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m         rule = fetch_rule_by_short_id(db, args.short_id)[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m         if rule is None:[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m             raise NokoriError(f"no rule with short_id {args.short_id!r}")[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m [0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m         # Posthoc history[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m         posthoc_counts = count_evaluated_fire_events(db, rule.id, window_days=30)[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m [0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m         # Archived fingerprint summary[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m         archived_fp = None[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m         if rule.status == "archived":[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m             archived_fp = {[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                 "reason": rule.archived_reason,[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                 "replacement_id": rule.replacement_id,[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m             }[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m     finally:[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m         if db is not None:[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m         db.close()[0m[0m


[2m─── nokori/commands/install.py:78-78 ───[0m
`sys.executable` 可能包含空格（如 Windows 下 `C:\Program Files\...`），直接通过 f-string
拼接会导致命令被错误分割为多个参数，钩子执行失败。建议使用 `shlex.quote()` 对路径进行转义，或改用列表形式构建。项目中其他位置均使用 `[sys.executable, "-m",
"nokori", ...]` 的列表形式。

[91m[48;2;70;0;0m-[0m[48;2;70;0;0m     return f"{sys.executable} -I -m nokori hook"[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m     import shlex[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m     return f"{shlex.quote(sys.executable)} -I -m nokori hook"[0m[0m


[2m─── nokori/commands/extract.py:127-138 ───[0m
在 `--dry-run` 模式下，`_process_path` 仍会创建 `LLMAdapter` 实例并调用 `extract_candidates()`，这会发起真实的 LLM
请求，产生不必要的费用和时间开销。`dry_run` 的目标通常是快速预览候选数量，不应产生副作用。建议在 `dry_run` 时跳过 LLM 调用，或至少先检查是否已有缓存的候选结果。

[92m[48;2;0;60;0m+[0m[48;2;0;60;0m         if dry_run:[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m             # Dry-run: skip LLM call; report 0 candidates without cost[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m             return (0, 0, False)[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m [0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m         llm = LLMAdapter(cfg)[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m         candidates, llm_ok = extract_candidates(text, llm)[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m         if not llm_ok:[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m             log.warning("extract failed (llm): %s", path)[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m             write_event([0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                 db, source="cli_extract",[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                 outcome="llm_failure",[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                 details={"transcript": path.name, "project_id": project_id},[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m             )[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m             return (0, 0, False)[0m[0m
[91m[48;2;70;0;0m-[0m[48;2;70;0;0m         if dry_run:[0m[0m
[91m[48;2;70;0;0m-[0m[48;2;70;0;0m             return (len(candidates), 0, False)[0m[0m


[2m─── nokori/commands/extract.py:38-54 ───[0m
`_ColdLLMAdapter.call` 直接调用了 `LLMAdapter._call_openai_compatible` 私有方法，这形成了脆弱的耦合。如果 `LLMAdapter`
内部重构、私有方法改名或签名变更，此处将产生运行时错误。冷管线需要传入 `model_id` 参数，但 `LLMAdapter` 的公开接口 `complete_messages`
未暴露此参数。建议在 `LLMAdapter` 中新增公开方法（如 `complete_messages_with_model`）或为 `complete_messages` 增加可选的
`model_id` 参数。

[92m[48;2;0;60;0m+[0m[48;2;0;60;0m class _ColdLLMAdapter:[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m     """Adapts LLMAdapter to the cold pipeline's llm.call() interface."""[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m [0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m     def __init__(self, llm: LLMAdapter):[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m         self._llm = llm[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m [0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m def call([0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m     self,[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m     model: str,[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m     system: str,[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m     user: str,[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m     max_tokens: int = 2000,[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m     timeout: int = 30,[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m ) -> str:[0m[0m
[91m[48;2;70;0;0m-[0m[48;2;70;0;0m     if self._llm.configured():[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m         # Prefer complete_messages if it supports model_id; otherwise fall back[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m         try:[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m             result = self._llm.complete_messages([0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m                 system, user, max_tokens=max_tokens, timeout=timeout,[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m                 model_id=model,[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m             )[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m         except TypeError:[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m             # Fallback for LLMAdapter without model_id support[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m         result = self._llm._call_openai_compatible([0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m             system, user, max_tokens, timeout, model_id=model[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m         )[0m[0m
[91m[48;2;70;0;0m-[0m[48;2;70;0;0m     else:[0m[0m
[91m[48;2;70;0;0m-[0m[48;2;70;0;0m         result = self._llm._fallback_claude_cli(system, user, timeout)[0m[0m
[91m[48;2;70;0;0m-[0m[48;2;70;0;0m     if result is None:[0m[0m
[91m[48;2;70;0;0m-[0m[48;2;70;0;0m         raise RuntimeError("LLM call returned None")[0m[0m
[91m[48;2;70;0;0m-[0m[48;2;70;0;0m     return result[0m[0m


[2m─── nokori/commands/install.py:312-316 ───[0m
`mkdir`、`write_text`、`os.replace` 都可能抛出 `OSError`（权限不足、磁盘满等），但此处与 `_read_json_file`（已将 OSError 包装为
ValueError）不同，没有任何保护。建议捕获 OSError 并包装为 ValueError，保持与 `_read_json_file` 一致的错误处理模式；或者直接复用已有的
`nokori.utils.fs.atomic_write_json` 工具函数。

[2m[48;2;38;38;38m [0m[48;2;38;38;38m def _write_json_file(path: Path, data: dict) -> None:[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m     try:[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m     path.parent.mkdir(parents=True, exist_ok=True)[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m     tmp = path.with_suffix(path.suffix + ".tmp")[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m     tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m     os.replace(tmp, path)[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m     except OSError as e:[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m         raise ValueError(f"cannot write {path}: {e}") from e[0m[0m


[2m─── nokori/commands/report.py:152-155 ───[0m
当 `model_id` 或 `outcome` 在数据库中为 NULL 时（两个字段在 `write_event`/`write_error` 中均可为 None），Python 行对象中对应值为
`None`，Markdown 输出会显示 "None: count"，对用户不够友好。建议在输出前将 None 替换为可读字符串，如 `(unspecified)` 或 `(none)`。

[2m[48;2;38;38;38m [0m[48;2;38;38;38m         if errors["by_model"]:[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m             print("### By Model")[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m             for row in errors["by_model"]:[0m[0m
[91m[48;2;70;0;0m-[0m[48;2;70;0;0m                 print(f"  {row['model_id']}: {row['count']}")[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m                 label = row['model_id'] or '(unspecified)'[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m                 print(f"  {label}: {row['count']}")[0m[0m


[2m─── nokori/commands/health.py:179-190 ───[0m
外层 try/except 只捕获了 open_db 的异常，但内部 total_rule_count、db.fetchone 等调用如果失败（例如 rule_embeddings 表因 schema
未迁移而不存在），异常会直接传播并导致 health 命令崩溃，无法在表格中优雅展示失败状态。这与 _check_rule_count（外层捕获所有异常）不一致。建议将内部的 try 改为
try/except Exception 以兜底。

[2m[48;2;38;38;38m [0m[48;2;38;38;38m     try:[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m         count = total_rule_count(db)[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m         if not embedding_search.auto_enabled(cfg, count):[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m             return ("skip", "embedding not enabled for this library size")[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m         row = db.fetchone([0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m             "SELECT COUNT(*) AS n FROM rules r "[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m             "WHERE r.status IN ('active', 'trusted') "[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m             "AND NOT EXISTS (SELECT 1 FROM rule_embeddings e WHERE e.rule_id = r.id)"[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m         )[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m         missing = int(row["n"]) if row else 0[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m     except Exception as e:[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m         return ("fail", str(e))[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m     finally:[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m         db.close()[0m[0m


[2m─── nokori/commands/install.py:131-131 ───[0m
`hooks.get(event)` 返回的值可能是用户手动编辑的非列表类型（如字符串、字典），此时 `list()` 不会报错但会产生非预期结果（如将字符串拆为字符列表），后续
`_strip_nokori_claude()` 迭代时可能崩溃。建议添加 `isinstance` 守卫，非列表时视为空列表。`_merge_cursor_hooks` 中第 131
行存在相同问题。

[91m[48;2;70;0;0m-[0m[48;2;70;0;0m         spec = list(hooks.get(event) or [])[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m         raw = hooks.get(event)[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m         spec = list(raw) if isinstance(raw, list) else [][0m[0m


[2m─── nokori/commands/install.py:131-131 ───[0m
与 `_merge_claude_settings`（第 121 行）相同的类型安全问题：`hooks.get(event)` 可能返回非列表值。建议同样添加 `isinstance` 检查。

[91m[48;2;70;0;0m-[0m[48;2;70;0;0m         spec = list(hooks.get(event) or [])[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m         raw = hooks.get(event)[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m         spec = list(raw) if isinstance(raw, list) else [][0m[0m


[2m─── nokori/commands/extract.py:185-201 ───[0m
`run_cold_pipeline` 的异常处理器覆盖了 `RuntimeError, OSError, TimeoutError, ConnectionError, ValueError`，但
`LlmError`（包括 `LlmTimeoutError`、`LlmRateLimitError`）继承自 `NokoriError(Exception)`，不属于上述任何类型。当冷管线中 LLM
调用失败（重试耗尽后），`LlmError` 会穿透 `run_cold_pipeline` 到达此处的 `except Exception`，导致
`all_ok=False`，转录文件不会被标记为已提取，下一次运行将重复处理相同内容。建议 `run_cold_pipeline` 应将 `LlmError` 也纳入 pending
处理，或在此处对 LLM 错误做更精细的分类（如仅对 LLM 错误做退避而非直接失败）。

[2m[48;2;38;38;38m [0m[48;2;38;38;38m             try:[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                 result = run_cold_pipeline([0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                     db,[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                     cold_llm,[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                     transcript_ref=transcript_ref,[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                     extractor_output=extractor_output,[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                     role_models=cfg.role_models,[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                     default_model=cfg.llm_model,[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                     project_id=project_id,[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                     role_max_tokens=cfg.role_max_tokens,[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                     role_timeouts=cfg.role_timeouts,[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                 )[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                 if result.rule_id is not None:[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                     rules_created += 1[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m             except Exception as exc:[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                 log.warning("cold pipeline failed for candidate: %s (%s)", cand.trigger[:60], exc)[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m                 # LLM errors are transient; mark incomplete but don't block[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m                 # other candidates. run_cold_pipeline should ideally handle[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m                 # LlmError internally and return pending status.[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                 all_ok = False[0m[0m


[2m─── nokori/commands/install.py:154-155 ───[0m
`merged.get("hooks", {})` 只在键缺失时返回 `{}`；若 hooks 被用户手动设为非 dict 类型（如字符串），后续 `hooks.get(event)` 将抛出
AttributeError。建议添加 `isinstance(hooks, dict)` 守卫。`_remove_nokori_cursor` 第 171 行存在相同问题。

[2m[48;2;38;38;38m [0m[48;2;38;38;38m     hooks = merged.get("hooks", {})[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m     if not isinstance(hooks, dict):[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m         hooks = {}[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m     for event in [e for e, *_ in _CLAUDE_HOOK_SPECS]:[0m[0m


[2m─── nokori/commands/report.py:121-125 ───[0m
`_build_report` 中收集了 `events_by_outcome` 数据（按 outcome 分组的事件统计），但 `_print_markdown` 函数没有输出该部分。JSON
模式下可以正常看到该数据，但 Markdown 模式下该统计信息会丢失。建议在 `events_by_source` 输出之后补充 `events_by_outcome` 的 Markdown 输出。

[2m[48;2;38;38;38m [0m[48;2;38;38;38m     if usage["events_by_source"]:[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m         print("### Events by Source")[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m         for row in usage["events_by_source"]:[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m             print(f"  {row['source']}: {row['count']}")[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m         print()[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m [0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m     if usage["events_by_outcome"]:[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m         print("### Events by Outcome")[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m         for row in usage["events_by_outcome"]:[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m             label = row['outcome'] or '(unspecified)'[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m             print(f"  {label}: {row['count']}")[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m         print()[0m[0m


[2m─── nokori/commands/report.py:94-94 ───[0m
`outcome` 字段在数据库中也允许 NULL（`write_event` 参数默认为 None），GROUP BY 聚合后行中值为 `None`，输出会显示 "None:
count"。建议同样替换为可读标签。

[91m[48;2;70;0;0m-[0m[48;2;70;0;0m "events_by_outcome": [dict(r) for r in events_by_outcome],[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m     if usage["events_by_outcome"]:[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m         print("### Events by Outcome")[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m         for row in usage["events_by_outcome"]:[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m             label = row['outcome'] or '(unspecified)'[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m             print(f"  {label}: {row['count']}")[0m[0m


[2m─── nokori/commands/report.py:127-131 ───[0m
同上，pipeline funnel 中的 `outcome` 字段也可能为 NULL，输出会显示 "None: count"。

[2m[48;2;38;38;38m [0m[48;2;38;38;38m     funnel = data["pipeline_funnel"][0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m     if funnel:[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m         print("## Pipeline Funnel")[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m         for outcome, count in sorted(funnel.items(), key=lambda x: -x[1]):[0m[0m
[91m[48;2;70;0;0m-[0m[48;2;70;0;0m             print(f"  {outcome}: {count}")[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m             label = outcome or '(unspecified)'[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m             print(f"  {label}: {count}")[0m[0m

2m─── nokori/commands/status.py:53-54 ───[0m
open_db() 调用在 try 块之外。若 open_db() 抛出异常（如数据库损坏/权限不足），db 变量不会被赋值，但 finally 中的 db.close() 仍会执行，引发
NameError 并掩盖原始异常。建议将 open_db() 移入 try 块内，或初始化为 None 并在 finally 中判空。参考 health.py 的嵌套 try/finally 模式。

[91m[48;2;70;0;0m-[0m[48;2;70;0;0m     db = open_db(cfg.db_path)[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m     db = None[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m     try:[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m         db = open_db(cfg.db_path)[0m[0m


[2m─── nokori/commands/show.py:21-23 ───[0m
当 open_db 抛出异常（如 DbError）时，变量 db 未被赋值，finally 块中的 db.close() 会引发 UnboundLocalError，掩盖原始错误信息。建议将 db
初始化提前，在 finally 中做空值检查。

[2m[48;2;38;38;38m [0m[48;2;38;38;38m def run(args: argparse.Namespace, cfg: Config) -> int:[0m[0m
[91m[48;2;70;0;0m-[0m[48;2;70;0;0m     db = open_db(cfg.db_path)[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m     db = None[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m     try:[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m         db = open_db(cfg.db_path)[0m[0m


[2m─── nokori/commands/show.py:38-39 ───[0m
需要配合上方的改动，在 close 前增加空值检查，避免 db 为 None 时出现 AttributeError。

[2m[48;2;38;38;38m [0m[48;2;38;38;38m     finally:[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m         if db is not None:[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m         db.close()[0m[0m


[2m─── nokori/commands/show.py:0-0 ───[0m
当 open_db 抛出异常（如 DbError）时，变量 db 未被赋值，finally 块中的 db.close() 会引发 UnboundLocalError，掩盖原始错误信息。建议将
open_db 调用移入 try 块内，或提前捕获异常。

[2m[48;2;38;38;38m [0m[48;2;38;38;38m def run(args: argparse.Namespace, cfg: Config) -> int:[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m     db = open_db(cfg.db_path)[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m     try:[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m         rule = fetch_rule_by_short_id(db, args.short_id)[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m         if rule is None:[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m             raise NokoriError(f"no rule with short_id {args.short_id!r}")[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m [0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m         # Posthoc history[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m         posthoc_counts = count_evaluated_fire_events(db, rule.id, window_days=30)[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m [0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m         # Archived fingerprint summary[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m         archived_fp = None[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m         if rule.status == "archived":[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m             archived_fp = {[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                 "reason": rule.archived_reason,[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                 "replacement_id": rule.replacement_id,[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m             }[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m     finally:[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m         db.close()[0m[0m


[2m─── nokori/commands/install.py:0-0 ───[0m
`_process_path` 函数缺少顶层异常处理。内部调用的 `load_last_byte_offset`、`read_after`、`mark_extracted`
及数据库操作若抛出未捕获异常，将直接传播至 `run` 函数，导致整个命令崩溃。尤其在批量模式下，一个 transcript 的异常会中断后续所有 pending job
的处理，并可能留下锁未释放。建议在函数体内添加 try/except 包裹主逻辑，捕获异常后记录日志并返回 `(0, 0, False)`，确保批量处理不会中断。

[2m[48;2;38;38;38m [0m[48;2;38;38;38m def _process_path(path: Path, project_id: str | None, cfg: Config,[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                   *, dry_run: bool) -> tuple[int, int, bool]:[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m     """Read transcript, extract candidates, enqueue through cold pipeline.[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m [0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m     Returns (candidates_found, rules_created, finished).[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m     """[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m     try:[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m         return _process_path_impl(path, project_id, cfg, dry_run=dry_run)[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m     except Exception as exc:[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m         log.exception("unhandled error in _process_path: %s", path)[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m         return (0, 0, False)[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m [0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m [0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m def _process_path_impl(path: Path, project_id: str | None, cfg: Config,[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m                        *, dry_run: bool) -> tuple[int, int, bool]:[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m     db = open_db(cfg.db_path)[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m     try:[0m[0m


[2m─── nokori/commands/maintain.py:0-0 ───[0m
db.fetchone（去重查询）和 enqueue_transcript_ingest 位于 try/except 块之外。若这些数据库操作因锁冲突、schema异常或 I/O
错误而失败，异常会中断整个候选循环，导致当前及后续候选的冷管道处理被跳过。建议将这两个操作也纳入 try 块，或使用独立的 try/except 包裹并记录警告后 continue。

[2m[48;2;38;38;38m [0m[48;2;38;38;38m             # Dedup: skip if this segment was already successfully ingested[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m             try:[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m             existing_job = db.fetchone([0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                 "SELECT id FROM transcript_ingest_jobs "[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                 "WHERE segment_hash = ? AND status = 'done'",[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                 (seg_hash,),[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m             )[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m             except Exception as exc:[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m                 log.warning("dedup check failed for segment %s: %s", seg_hash[:8], exc)[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m                 all_ok = False[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m                 continue[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m             if existing_job:[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                 continue[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m [0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m             try:[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m             enqueue_transcript_ingest([0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                 db,[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                 transcript_ref=transcript_ref,[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                 segment_hash=seg_hash,[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                 extractor_prompt_version=PROMPT_VERSIONS["extractor"],[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m             )[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m             except Exception as exc:[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m                 log.warning("enqueue_transcript_ingest failed for segment %s: %s", seg_hash[:8], exc)[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m                 all_ok = False[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m                 continue[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m [0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m             # Run through cold pipeline[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m             try:[0m[0m


[2m─── nokori/commands/maintain.py:0-0 ───[0m
批量处理循环中 _process_path 调用缺少 try/except 保护。若某个 transcript 的处理因数据库、文件 I/O 或 LLM
异常而失败，异常会直接传播并中断整个批量处理，导致后续任务被跳过。建议在循环体内用 try/except 包裹，记录错误日志后 continue 处理下一个任务。

[92m[48;2;0;60;0m+[0m[48;2;0;60;0m             try:[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m             cands, applied, finished = _process_path([0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                 path, job.get("project_id"), cfg, dry_run=args.dry_run[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m             )[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m             except Exception as exc:[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m                 log.warning("extract job failed: %s (%s)", job_path.name, exc)[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m                 continue[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m             total_cands += cands[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m             total_applied += applied[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m             if not args.dry_run:[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                 if finished:[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                     job_io.delete_job(job_path)[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                 else:[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                     log.warning([0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                         "extract job kept pending (not finished): %s", job_path.name[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                     )[0m[0m


[2m─── nokori/commands/maintain.py:0-0 ───[0m
session 路径下的 _process_path 调用同样缺少异常处理。若处理单个 transcript 时发生数据库错误或 LLM 调用失败，异常会直接传播到 CLI
入口，用户只能看到未格式化的 traceback。建议增加 try/except 并输出友好的错误信息。

[2m[48;2;38;38;38m [0m[48;2;38;38;38m         if args.session:[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m             path = Path(args.session).expanduser().resolve()[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m             if not path.exists():[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                 print(f"nokori: transcript not found: {path}")[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                 return 1[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m             if getattr(args, "project", None):[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                 project_id = args.project[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m             else:[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                 project_id = job_io.find_project_id_for_transcript(cfg, path)[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                 if project_id is None:[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                     project_id = resolve_project_id(os.getcwd())[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m             try:[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m             cands, applied, _finished = _process_path([0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                 path, project_id, cfg, dry_run=args.dry_run[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m             )[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m             except Exception as exc:[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m                 log.error("extract session failed: %s (%s)", path, exc)[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m                 print(f"nokori: extract failed for {path}: {exc}")[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m                 return 1[0m[0m


[2m─── nokori/commands/install.py:0-0 ───[0m
`enqueue_transcript_ingest` 调用未被 `try`
代码块包裹。若该函数因数据库错误等原因抛出异常，将中断整个候选处理循环（当前及后续候选的冷管道处理被跳过），且已处理的部分可能未正确标记提取状态。建议将该调用移入已有的 try 块内，或单独包裹
try/except 并记录 warning。

[92m[48;2;0;60;0m+[0m[48;2;0;60;0m             try:[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m             enqueue_transcript_ingest([0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                 db,[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                 transcript_ref=transcript_ref,[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                 segment_hash=seg_hash,[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                 extractor_prompt_version=PROMPT_VERSIONS["extractor"],[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m             )[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m             except Exception as exc:[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m                 log.warning("enqueue_transcript_ingest failed: %s", exc)[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m                 all_ok = False[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m                 continue[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m [0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m             # Run through cold pipeline[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m             try:[0m[0m


[2m─── nokori/commands/install.py:0-0 ───[0m
`refresh_job_mtime` 的返回值未做 None 检查，直接赋值给 `job_path` 并用于后续 `read_job` 调用。`refresh_job_mtime`
内部在重命名失败等场景下可能返回 `None`，导致后续 `read_job(None)` 引发 `TypeError`。建议在赋值后检查 `job_path` 是否为 None，若为空则跳过当前
job。

[91m[48;2;70;0;0m-[0m[48;2;70;0;0m                 job_path = job_io.refresh_job_mtime([0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m                 new_job_path = job_io.refresh_job_mtime([0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                     cfg, job_path, path, job.get("project_id"), current_mtime,[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                 )[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m                 if new_job_path is None:[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m                     log.warning("refresh_job_mtime returned None for: %s", path)[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m                     continue[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m                 job_path = new_job_path[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                 job = job_io.read_job(job_path)[0m[0m


[2m─── nokori/commands/install.py:0-0 ───[0m
构建 `extractor_output` 字典时，直接使用了 `cand` 的多个字段（如
`trigger_text_zh`、`action_zh`、`trigger_variants_zh`、`search_terms` 等），这些字段在 `Candidate` 数据类中默认为
`None`。若 LLM 未返回这些字段，`None` 值传入 `run_cold_pipeline` 可能导致其内部在字符串操作时抛出 `AttributeError`。建议在构建字典时为可能为
None 的字段提供空字符串默认值（如 `cand.trigger_text_zh or ""`）。

[2m[48;2;38;38;38m [0m[48;2;38;38;38m             extractor_output = {[0m[0m
[91m[48;2;70;0;0m-[0m[48;2;70;0;0m                 "trigger": cand.trigger,[0m[0m
[91m[48;2;70;0;0m-[0m[48;2;70;0;0m                 "trigger_draft": cand.trigger,[0m[0m
[91m[48;2;70;0;0m-[0m[48;2;70;0;0m                 "trigger_zh": cand.trigger_text_zh,[0m[0m
[91m[48;2;70;0;0m-[0m[48;2;70;0;0m                 "action": cand.action,[0m[0m
[91m[48;2;70;0;0m-[0m[48;2;70;0;0m                 "action_draft": cand.action,[0m[0m
[91m[48;2;70;0;0m-[0m[48;2;70;0;0m                 "action_zh": cand.action_zh,[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m                 "trigger": cand.trigger or "",[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m                 "trigger_draft": cand.trigger or "",[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m                 "trigger_zh": cand.trigger_text_zh or "",[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m                 "action": cand.action or "",[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m                 "action_draft": cand.action or "",[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m                 "action_zh": cand.action_zh or "",[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                 "evidence_quotes": _candidate_evidence_quotes(cand, text),[0m[0m
[91m[48;2;70;0;0m-[0m[48;2;70;0;0m                 "trigger_variants_draft": cand.trigger_variants,[0m[0m
[91m[48;2;70;0;0m-[0m[48;2;70;0;0m                 "trigger_variants_zh": cand.trigger_variants_zh,[0m[0m
[91m[48;2;70;0;0m-[0m[48;2;70;0;0m                 "search_terms_draft": cand.search_terms,[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m                 "trigger_variants_draft": cand.trigger_variants or [],[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m                 "trigger_variants_zh": cand.trigger_variants_zh or [],[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m                 "search_terms_draft": cand.search_terms or [],[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                 "domain_tags": [],[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                 "tool_tags": [],[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                 "path_patterns": [],[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m             }[0m[0m


[2m─── nokori/commands/maintain.py:0-0 ───[0m
_ColdLLMAdapter.call() 直接调用了 LLMAdapter 的私有方法 _call_openai_compatible 和
_fallback_claude_cli。这破坏了封装性，若 LLMAdapter 内部实现变更则此适配器将静默失效。此外 _call_openai_compatible 的
response_format 参数默认为 {"type": "json_object"}，这对所有冷管道角色均强制要求 JSON 格式输出。建议使用 LLMAdapter 的公开方法
complete_role() 或为冷管道提供专用的公开接口。

[2m[48;2;38;38;38m [0m[48;2;38;38;38m class _ColdLLMAdapter:[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m     """Adapts LLMAdapter to the cold pipeline's llm.call() interface."""[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m [0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m     def __init__(self, llm: LLMAdapter):[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m         self._llm = llm[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m [0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m     def call([0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m         self,[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m         model: str,[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m         system: str,[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m         user: str,[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m         max_tokens: int = 2000,[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m         timeout: int = 30,[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m     ) -> str:[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m         # Use public API: complete_messages with model_id override via configure()[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m         # or use the existing public complete_role if model routing is needed.[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m         if self._llm.configured():[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m             result = self._llm._call_openai_compatible([0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                 system, user, max_tokens, timeout, model_id=model[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m             )[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m         else:[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m             result = self._llm._fallback_claude_cli(system, user, timeout)[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m         if result is None:[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m             raise RuntimeError("LLM call returned None")[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m         return result[0m[0m


[2m─── nokori/commands/stream.py:64-68 ───[0m
`_follow_mode` 循环内 `query_events` 同时传入了 `after_id=last_id` 和 `since=since`（原始时间）。当 `after_id`
生效后，`since` 过滤器是冗余的，且可能与 `after_id` 产生不必要的逻辑交集——如果某事件 `created_at` 早于原始 `since` 但 `rowid`
在游标之后（极端情况下），它会被静默排除。建议：当 `last_id` 非 None 时只传 `after_id`，移除 `since` 参数，使游标逻辑更清晰。

[2m[48;2;38;38;38m [0m[48;2;38;38;38m                 events = query_events([0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                     db, session_id=session_id, source=source,[0m[0m
[91m[48;2;70;0;0m-[0m[48;2;70;0;0m                     after_id=last_id, since=since,[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m                     after_id=last_id,[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                     limit=50,[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                 )[0m[0m


[2m─── nokori/commands/stream.py:91-97 ───[0m
`_print_event` 在 verbose 模式下直接修改了传入的事件字典（`event["details"] =
json.loads(details)`），这会污染调用方的数据。虽然当前调用方未复用该字典，但不符合无副作用函数的惯例，未来复用可能引入隐蔽 bug。建议：使用浅拷贝或仅修改局部变量再序列化。

[2m[48;2;38;38;38m [0m[48;2;38;38;38m         details = event.get("details")[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m         if isinstance(details, str):[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m             try:[0m[0m
[91m[48;2;70;0;0m-[0m[48;2;70;0;0m                 event["details"] = json.loads(details)[0m[0m
[92m[48;2;0;60;0m+[0m[48;2;0;60;0m                 event = {**event, "details": json.loads(details)}[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m             except (json.JSONDecodeError, TypeError):[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m                 pass[0m[0m
[2m[48;2;38;38;38m [0m[48;2;38;38;38m         print(json.dumps(event, ensure_ascii=False))[0m[0m


[2m─── nokori/commands/stream.py:19-19 ───[0m
新增的 `nokori/commands/stream.py` 缺少对应的测试文件（未找到 `test_stream*`）。关键路径如 `_follow_mode` 的轮询循环、游标重置（cursor
被清理后的回退逻辑）、`_dump_mode` 的分页输出均无自动化测试覆盖，这些边界场景依赖人工验证容易遗漏。建议：新增 `tests/test_stream.py`，至少覆盖 dump
模式基本输出、follow 模式游标推进和 cursor 丢失回退场景。

[2m[48;2;38;38;38m [0m[48;2;38;38;38m def run(args: argparse.Namespace, cfg: Config) -> int:[0m[0m

