# Ethan 流水线实现说明

当前状态：Ethan 的 E1–E4 以及 E5 中可独立完成的状态生命周期工作，已在
`feature/ykzhao0829_02` 分支实现。未来可以直接注入 Aaron 的 Retriever 和
Ranker，无需修改比赛规定的 Agent API 或 Pipeline Contract v1。

## 1. 运行流程

```text
Agent.respond(session_id, user_message, turn, top_k)
  → update_session_state
  → parse_message / apply_patch
  → StateSnapshot + ProfileSnapshot
  → IntentRouter.route
  → SearchRequest
  → RetrieverProtocol.retrieve
  → RankerProtocol.rank
  → QuestionPolicyProtocol.choose
  → 比赛规定的 Agent 响应
```

Override 是 `RouteDecision` 上的运行时事件，而不是第三种搜索路线。系统会先完成
当前轮次的状态更新和 raw evidence 清理，再执行路由，因此 Retriever 和 Ranker
看到的始终是当前轮次更新后的最新状态。

## 2. 模块职责边界

| 模块 | 负责内容 | 不负责内容 |
| --- | --- | --- |
| `state_adapter.py` | 生成不可变的状态/画像快照，以及正向 structured query | 消息解析、检索、排序 |
| `intent_router.py` | 生成可解释的 Buying/Browsing 决策 | 根据公开标签推断意图、生成候选商品 |
| `orchestrator.py` | Session 生命周期、请求构造、依赖调用、故障降级和诊断 | 商品目录标准化、排序公式 |
| `agent.py` | 对外 Agent API 和当前 SQLite 索引 | 流水线决策逻辑 |

在 Ethan 的开发范围内，`pipeline_contracts.py`、`conversation_state.py`、
`constraint_parser.py` 和 `attribute_lexicons.py` 继续作为共享的只读依赖。

## 3. 接入 Aaron 的实现

Aaron 的对象只需要满足现有的、支持运行时检查的 Protocol：

```python
from starter.orchestrator import AgentOrchestrator

orchestrator = AgentOrchestrator(
    retriever=aaron_retriever,          # RetrieverProtocol
    ranker=aaron_ranker,                # RankerProtocol
    question_policy=question_policy,    # QuestionPolicyProtocol
    fallback_retriever=legacy_retriever,
    fallback_question_policy=legacy_question_policy,
)
```

Retriever 每轮只接收一个不可变的 `SearchRequest`。Retriever 必须直接使用
`request.route_decision.route` 中已经确定的路线，不能在内部重新判断用户意图。
Ranker 接收同一个 `SearchRequest`，以及 Retriever 返回的 `CandidatePool`。

Ethan 的代码不会导入 Aaron 内部的商品结构、Matcher、Normalizer 或排序特征类型；
Aaron 的代码也不需要访问可变的 Session 状态。

## 4. SearchRequest 保证

- `state.turn == request.turn`。
- `top_k` 在 1–10 之间。
- `candidate_limit` 不超过 200。
- `raw_context` 保留当前仍然有效的开放词汇用户原始证据。
- `structured_query` 只包含商品类目、硬约束和正向软偏好。
- `excluded`、`no_preference`、`asked_attributes` 和用户画像标签不会被加入
  `structured_query`。
- 用户画像始终作为单独的不可变 `ProfileSnapshot` 传递，绝不会自动成为当前
  Session 的硬约束。

## 5. 运行模式与故障降级

`RuntimeMode.DEVELOPMENT` 会重新抛出预期的流水线异常，避免测试和本地开发期间
静默隐藏缺陷。`RuntimeMode.OFFICIAL` 只会在对应边界捕获文档明确规定的异常类型：

| 故障 | Official 模式的降级方案 |
| --- | --- |
| `RoutingError` | 使用基于当前状态的确定性 Buying/Browsing 默认决策 |
| `RetrievalError` | 使用注入的 Legacy Retriever |
| `RankingError` | 保持 CandidatePool 的 RRF 顺序 |
| `QuestionPolicyError` | 使用注入的 Legacy Question Policy |

未预期异常不会被吞掉，包括表示程序缺陷的 `RuntimeError`。主 Retriever 返回空候选池
时会被视为 `RetrievalError`；如果 Legacy Retriever 仍然返回空结果，系统会显式抛出
错误，而不是返回具有误导性的空响应。

无需修改比赛 Agent API，也可以读取降级诊断信息：

```python
response = agent.respond("session-1", "Show me running shoes", 1, 10)
diagnostics = agent.orchestrator.diagnostics("session-1")

print(diagnostics.events)
# 示例：("retrieval_fallback", "ranking_fallback")

print(diagnostics.route_decision.reason)
# 根据实际命中的路由信号生成、可供人工阅读的解释。
```

诊断事件使用稳定的英文标识符，便于测试、日志检索和后续生成消融报告。

## 6. Legacy 行为

`LegacyRetrieverAdapter` 对现有的三路 SQLite FTS 检索进行封装：

1. 当前有效的 raw context，权重为 1.40；
2. 当前用户消息，权重为 0.85；
3. 初始 base request，权重为 0.25。

它会生成符合 Contract v1 的候选商品，保留每条检索路线的证据和原有融合 RRF 分数。
`LegacyRankerAdapter` 保持这一候选顺序；`LegacyQuestionPolicyAdapter` 保持原有的固定
提问顺序。

该兼容路径的作用是让架构改造与检索效果优化相互独立。其公开集指标必须保持：

| 指标 | 要求值 |
| --- | ---: |
| Hit Rate@10 | 0.840000 |
| MRR | 0.476401 |
| MTTC | 4.885 |
| Technical Score | 0.685220 |

## 7. 测试命令

仓库使用 Python 标准库提供的 `unittest`：

```bash
python3 -m unittest discover -s tests -v
python3 -m evaluator.local_evaluator
git diff --check
```

Ethan 的测试覆盖确定性状态适配、Router 优先级与解释、Fake Protocol 接入、故障注入、
Session 隔离、Legacy Adapter、Top-K 校验，以及八个 Override 生命周期案例。

## 8. 当前联合开发边界

Aaron 负责的正式 Hybrid Retriever 和结构化 Ranker 目前尚未出现在仓库中。因此，
Protocol 注入边界已经完成，但在最终提交前，团队仍需将当前主要依赖的 Legacy 对象
替换为 Aaron 的正式实现，并记录替换前后的总体指标和分场景指标。

在接入 Aaron 的实现后，Legacy 对象仍应保留为 Official 模式下完全离线、可复现的
安全降级路径。
