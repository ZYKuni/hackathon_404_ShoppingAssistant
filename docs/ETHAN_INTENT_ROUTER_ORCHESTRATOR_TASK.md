# Ethan 技术任务书：Intent Router、Agent 编排与状态生命周期

> 文档版本：1.0
>
> 适用分支基线：`feature/ykzhao0829`
>
> 公共接口版本：`PIPELINE_CONTRACT_VERSION = "1.0"`
>
> 优先级：P0
>
> 预计独立工作量：10–14 小时
>
> 本文是实施说明，不代表对应正式模块已经完成。

## 1. 任务目标

Ethan 负责把当前单体 `Agent` 演进为一个可替换、可降级、可测试的编排层，同时保持比赛规定的 `Agent.reset()` 和 `Agent.respond()` 接口完全不变。

目标调用链如下：

```text
用户消息
→ 更新 ConversationState
→ 处理完整或局部 Intent Override
→ 生成 StateSnapshot / ProfileSnapshot
→ Intent Router 生成 RouteDecision
→ 构造 SearchRequest
→ 调用 RetrieverProtocol
→ 调用 RankerProtocol
→ 调用现有或后续 Question Policy
→ 截取 Top-K 并组装 Agent API Response
```

本任务完成后，Ethan 不负责具体商品字段抽取、BM25 SQL、Top-200 融合、三态匹配或排序公式。这些属于 Aaron 的模块。

## 2. 对标比赛要求

本任务直接覆盖比赛的以下要求：

| 比赛要求 | Ethan 的交付 |
| --- | --- |
| Buying / Browsing 双轨路由 | 可解释的 `IntentRoute` 与 `RouteDecision` |
| 多轮状态管理 | `ConversationState` 生命周期和快照适配 |
| Intent Override | 更新状态后重新路由、召回和排序 |
| 动态上下文编程 | 每轮根据 active raw evidence 和结构化状态构造 `SearchRequest` |
| 最多 10 轮 | 保持 `turn` 和 `top_k` 契约校验 |
| 离线与低成本 | 核心编排不依赖网络或 LLM |
| 可复现和鲁棒性 | Legacy fallback、故障注入测试、可解释诊断 |
| API 契约 | `message`、`ask_attribute`、`recommendations`、`usage` 不变化 |

## 3. 当前代码基线

当前已经完成：

- `parse_message → StatePatch → apply_patch → ConversationState` 已进入 `Agent` 主链路；
- raw evidence 保存在 `SessionState.active_messages`；
- 完整 Override 可以清理旧结构化商品条件；
- `no_preference` 和 `asked_attributes` 已进入结构化状态；
- `user_profile` 与当前会话状态分开保存；
- 当前系统完全离线，token 和 API 成本为 0。

当前公开集基线必须保留：

| 指标 | 基线 |
| --- | ---: |
| Hit Rate@10 | 0.840000 |
| MRR | 0.476401 |
| MTTC | 4.885 |
| Technical Score | 0.685220 |

在新 Retriever 和 Ranker 尚未正式启用时，Legacy 模式必须复现以上指标。

## 4. Ethan 的文件范围

### 4.1 允许新建或主要修改

```text
starter/intent_router.py
starter/orchestrator.py
starter/state_adapter.py
starter/agent.py
tests/test_intent_router.py
tests/test_orchestrator.py
tests/test_state_adapter.py
tests/test_agent.py
```

### 4.2 只读依赖

```text
starter/pipeline_contracts.py
starter/conversation_state.py
starter/constraint_parser.py
starter/attribute_lexicons.py
```

公共契约如需修改，必须与 Aaron 共同确认。不要在 Ethan 的分支中单方面新增第二套 Candidate、SearchRequest 或 RankingResult。

### 4.3 禁止修改

```text
evaluator/local_evaluator.py
data/catalog.jsonl
data/public_set.jsonl
```

不得读取 `scenario_type`、ground truth 或隐藏 intent card 来生成运行时路由。

## 5. Pipeline Contract v1：Ethan 负责的接口部分

公共定义位于 `starter/pipeline_contracts.py`。所有对象均为不可变 dataclass。

### 5.1 `IntentRoute`

```python
class IntentRoute(str, Enum):
    BUYING = "buying"
    BROWSING = "browsing"
```

Override 是事件，不是第三种 route。发生 Override 时，应先更新状态，再重新生成 Buying/Browsing 路由。

### 5.2 `RouteDecision`

```python
RouteDecision(
    route=IntentRoute.BUYING,
    confidence=0.91,
    reason="A price ceiling and an excluded color are known.",
    signals=("has_budget", "has_exclusion"),
    override_detected=False,
)
```

Ethan 负责产生全部字段：

- `route`：最终路线；
- `confidence`：0–1；
- `reason`：面向调试和演示的明确解释；
- `signals`：实际命中的规则；
- `override_detected`：本轮是否发生 Override。

Aaron 只能消费该决定，不能在 Retriever 内重新判定 route。

### 5.3 `StateSnapshot`

`StateSnapshot` 是 `ConversationState` 的只读传输形式：

```python
StateSnapshot(
    schema_version="0.1.0",
    turn=2,
    category="running_shoes",
    hard_constraints=(ConstraintTerm("price_max", (120,)),),
    soft_preferences=(ConstraintTerm("feature", ("lightweight",)),),
    excluded=(ConstraintTerm("color", ("white",)),),
    no_preference=("brand",),
    asked_attributes=("material", "brand"),
)
```

Ethan 必须保证：

- 值已经经过现有共享词典标准化；
- 每个分组内同一字段只出现一次；
- 所有 list 转换为 tuple；
- 输出顺序确定，建议按字段名排序；
- `snapshot.turn == SearchRequest.turn`；
- 不把可变的 `ConversationState` 直接交给 Retriever 或 Ranker。

### 5.4 `ProfileSnapshot`

将安全画像字段转换为只读值：

```python
ProfileSnapshot(
    preference_tags=("comfort", "durability"),
    average_prior_rating=4.5,
    purchase_frequency="3-4 prior purchases",
    rating_style="usually positive",
)
```

画像不得进入 `hard_constraints`。若画像与本轮明确要求冲突，本轮状态优先。P0 可以只传输画像而不使用画像打分。

### 5.5 `SearchRequest`

Ethan 是 `SearchRequest` 的唯一构造方：

```python
SearchRequest(
    session_id=session_id,
    turn=turn,
    top_k=top_k,
    candidate_limit=200,
    route_decision=decision,
    current_message=user_message,
    raw_context=active_raw_context,
    base_request=session.base_request,
    structured_query=structured_query,
    state=state_snapshot,
    profile=profile_snapshot,
)
```

契约限制：

- `turn` 为 1–10；
- `top_k` 为 1–10；
- `candidate_limit` 在 `top_k` 与 200 之间；
- `current_message` 非空；
- raw/base/structured query 必须是字符串；
- 状态 turn 必须与请求 turn 一致。

### 5.6 `RetrieverProtocol` 与 `RankerProtocol`

Orchestrator 只依赖 Protocol：

```python
class RetrieverProtocol(Protocol):
    def retrieve(self, request: SearchRequest) -> CandidatePool:
        ...


class RankerProtocol(Protocol):
    def rank(
        self,
        request: SearchRequest,
        pool: CandidatePool,
    ) -> RankingResult:
        ...
```

Ethan 不得 import Aaron 的内部 `NormalizedProduct` 或 Matcher 类型。

## 6. `state_adapter.py` 设计

建议集中实现三个纯函数或无状态方法：

```python
def to_state_snapshot(state: ConversationState) -> StateSnapshot:
    ...


def to_profile_snapshot(profile: dict) -> ProfileSnapshot:
    ...


def build_structured_query(snapshot: StateSnapshot) -> str:
    ...
```

### 6.1 状态转换规则

```text
ConversationState.category
→ StateSnapshot.category

hard_constraints[field] = scalar/list
→ ConstraintTerm(field, tuple(values))

soft_preferences
→ 独立 ConstraintTerm 分组

excluded
→ 独立 ConstraintTerm 分组
```

不要把 excluded 值拼入正向 `structured_query`，否则 `not white` 会让 BM25 奖励 white 商品。

### 6.2 `structured_query` 最小规则

P0 只拼接正向信息：

```text
category
+ hard constraint values
+ soft preference values
```

要求：

- 保持确定性顺序；
- 去重；
- `_` 转为空格；
- 不加入 no preference；
- 不加入 asked attributes；
- 不加入 excluded；
- 不把 profile 自动加入主查询。

### 6.3 raw context 责任

Ethan 负责决定哪些 raw evidence 当前有效。Aaron只接收最终 `raw_context` 字符串。

P0 可以继续使用现有 active message 列表，但必须区分：

- 完整意图替换：旧商品级 raw evidence 失效；
- 单字段替换：只替换冲突字段，不应删除无关偏好；
- 类别替换：旧类别相关条件失效；
- no preference：不能作为正向检索文本；
- excluded：保留在结构化状态，不作为正向关键词。

如果单字段 raw evidence 无法安全删除，应优先让结构化 Ranker处理冲突，并记录限制；不要用脆弱字符串替换伪造精确清理。

## 7. Intent Router v1

### 7.1 路由输入

```python
def route(
    current_message: str,
    state: StateSnapshot,
    override_detected: bool,
) -> RouteDecision:
    ...
```

### 7.2 Buying 信号

强信号：

- 存在 hard constraint；
- 存在价格上限或下限；
- 存在 size；
- 存在 excluded；
- 用户使用 `must`、`only`、`under`、`below`、`no more than`；
- 明确类目与明确属性同时出现。

弱信号：

- `need`；
- `looking for`；
- 明确材质、颜色或品牌；
- 当前类目较具体。

### 7.3 Browsing 信号

- `still exploring`；
- `ideas`；
- `something for`；
- `not sure`；
- `open to`；
- 只有宽泛类目；
- 当前没有 hard constraint；
- 需求主要描述场景而非属性。

`need` 不能单独决定 Buying，例如 `I need some ideas` 仍应倾向 Browsing。

### 7.4 推荐决策顺序

```text
1. 读取已经更新完成的 StateSnapshot
2. 收集强 Buying 信号
3. 收集强 Browsing 信号
4. 若存在明确 hard constraint，优先 Buying
5. 若没有 hard constraint 且存在强 Browsing 表达，选择 Browsing
6. 若只有类目，选择 Browsing
7. 使用弱信号打分
8. 生成 reason 和 signals
```

P0 不要求训练分类器，不允许根据 public sample 的标签拟合隐藏规则。

### 7.5 解释性要求

好例子：

```text
Only a broad category is known and no hard constraint is present.
```

坏例子：

```text
The model selected browsing.
```

`reason` 必须可以由 `signals` 复核。

## 8. Override 生命周期

处理顺序必须是：

```text
检测消息中的 Override 语义
→ 生成和应用 StatePatch
→ 清理相应 raw evidence
→ 生成 StateSnapshot
→ 重新路由
→ 重新召回
→ 重新排序
→ 重新选择问题
```

### 8.1 完整 Override

示例：

```text
Ignore my earlier preference. What I need is a winter boot.
```

行为：

- 清理旧商品级 hard/soft/excluded；
- 清理 no preference 和 asked attributes；
- 类别如发生变化，以新类别为准；
- profile 不清理；
- 新消息成为 active evidence。

### 8.2 单字段 Override

示例：

```text
Actually, make the budget $150.
```

行为：

- 只替换 `price_max`；
- 保留其他有效约束；
- 不清空整个会话；
- RouteDecision 根据更新后的状态重新生成。

### 8.3 exclude → allow

示例：

```text
Black is okay now.
```

行为：

- 从 excluded 中移除 black；
- 不自动把 black 升级为正向偏好；
- 不修改其他颜色条件。

## 9. Orchestrator 设计

### 9.1 依赖注入

```python
class AgentOrchestrator:
    def __init__(
        self,
        retriever: RetrieverProtocol,
        ranker: RankerProtocol,
        ...,
    ) -> None:
        ...
```

测试时注入 Fake；集成时注入 Aaron 的正式模块。

### 9.2 推荐主流程伪代码

```python
def respond(session_id, user_message, turn, top_k):
    session = require_session(session_id)
    update_session_state(session, user_message, turn)

    snapshot = to_state_snapshot(session.conversation_state)
    profile = to_profile_snapshot(session.user_profile)
    decision = router.route(user_message, snapshot, override_detected)
    request = build_search_request(...)

    pool = retrieve_with_fallback(request)
    ranking = rank_with_fallback(request, pool)
    recommendations = ranking.candidates[:top_k]
    question = choose_question_with_fallback(...)

    return build_agent_response(question, recommendations)
```

### 9.3 Agent API 输出

保持：

```python
{
    "message": str,
    "ask_attribute": str | None,
    "recommendations": [
        {"parent_asin": str, "score": float}
    ],
    "usage": {
        "prompt_tokens": int,
        "completion_tokens": int,
    },
}
```

只有前 10 个有效、唯一 ASIN 会被评分。Orchestrator 必须保证顺序稳定。

## 10. 安全降级

建议支持：

```python
class RuntimeMode(str, Enum):
    DEVELOPMENT = "development"
    OFFICIAL = "official"
```

| 失败 | Development | Official |
| --- | --- | --- |
| `RoutingError` | 抛出 | 使用确定性默认路由 |
| `RetrievalError` | 抛出 | Legacy BM25/RRF |
| `RankingError` | 抛出 | CandidatePool RRF 顺序 |
| Question Policy 预期错误 | 抛出 | 当前固定问题顺序 |

限制：

- 只捕获 `RoutingError`、`RetrievalError`、`RankingError` 等预期异常；
- 禁止无条件 `except Exception` 静默隐藏错误；
- fallback 后不得返回空推荐；
- fallback 是否触发必须进入诊断日志；
- 网络、API Key 或模型不可用不能破坏离线主链路。

## 11. Legacy Adapter

在 Aaron 模块未集成前，Ethan 需要通过 Adapter 包装现有行为，而不是立刻删除旧 `_search()` 和 `_rank()`。

建议：

```text
LegacyRetrieverAdapter
LegacyRankerAdapter
LegacyQuestionPolicyAdapter
```

Legacy 模式的 Definition of Done：

- 完整 200 会话指标与当前基线一致；
- 现有 Agent 测试不回归；
- Agent API 不变化；
- 新 Orchestrator 可以仅靠 Fake 或 Legacy Adapter 完整运行。

## 12. 测试要求

### 12.1 `test_state_adapter.py`

- 所有状态 list 转换为 tuple；
- 字段排序确定；
- excluded 不进入 structured query；
- no preference 不进入 structured query；
- profile 与 session state 分离；
- snapshot 不可变；
- turn 保持一致。

### 12.2 `test_intent_router.py`

至少覆盖：

- 明确预算 → Buying；
- 明确尺寸 → Buying；
- excluded → Buying；
- `still exploring` → Browsing；
- `need ideas` → Browsing；
- 只有宽泛类目 → Browsing；
- 有 hard constraint 和 exploring 冲突时的优先级；
- Override 后重新判路由；
- reason 非空；
- confidence 范围合法。

### 12.3 `test_orchestrator.py`

- 调用顺序正确；
- SearchRequest 字段完整；
- Fake Retriever/Ranker 可接入；
- Retriever 故障触发 Legacy fallback；
- Ranker 故障保留 RRF 顺序；
- fallback 不返回空推荐；
- Top-K 不超过 10；
- 推荐 ASIN 唯一；
- `reset()` 隔离 session；
- 开发模式抛出预期异常；
-正式模式捕获预期异常。

### 12.4 Override 专项测试

- 同类商品替换颜色；
- 替换预算；
- 完整类别切换；
- 开放词汇替换；
- exclude → allow；
- Override 前已询问多个字段；
- Override 后旧 raw evidence、excluded、no preference、asked attributes 的处理。

## 13. 执行顺序

### 阶段 E1：状态适配，2–3 小时

- 完成 `state_adapter.py`；
- 将现有状态转换为 Contract v1；
- 补齐确定性测试。

### 阶段 E2：Router，3–4 小时

- 完成规则和解释；
- 不接触 Aaron 的实现；
- 使用人工 StateSnapshot 测试。

### 阶段 E3：Orchestrator + Fake，3–4 小时

- 依赖 Protocol；
- 使用 Fake Retriever/Ranker；
- 确认完整调用顺序。

### 阶段 E4：Legacy Adapter 与回归，2–3 小时

- 保留当前行为；
- 跑全部单元测试；
- 跑完整 200 会话评测。

### 阶段 E5：Aaron 模块接入，联合工作

- 替换依赖注入对象；
- 不改 Aaron 内部实现；
- 记录 before/after 和分场景指标。

## 14. Ethan 独立验收标准

### 功能

- `RouteDecision` 可解释；
- Override 后重新执行路由与完整流水线；
- `SearchRequest` 完整且不可变；
- Agent API 不变化；
- Legacy/Fake/正式实现均可通过 Protocol 注入；
- fallback 可通过故障注入验证。

### 测试

- 当前 32 项测试全部通过；
- 新增 Ethan 单元测试全部通过；
- 不修改 evaluator 和比赛数据；
- `git diff --check` 通过。

### 指标

- Legacy 模式必须保持：Hit `0.84`、MRR `0.476401`、MTTC `4.885`、Score `0.68522`；
- Intent Override Hit Rate `≥0.73` 是与 Aaron 集成后的联合目标，不作为 Ethan 独立模块的硬指标；
- 无网络环境可以完整运行。

## 15. Ethan 明确不负责

- 商品目录字段抽取；
- `NormalizedProduct` 结构；
- 属性来源置信度；
- MATCH/MISMATCH/UNKNOWN 实现；
- BM25 SQL 和字段权重；
- RRF 公式与 Top-200 生成；
- 商品过滤规则；
- Ranker 特征和权重；
- Dense/vector 模型；
- 修改 evaluator；
- 使用 private labels 优化 Router。

## 16. 交付清单

```text
[ ] state_adapter.py
[ ] intent_router.py
[ ] orchestrator.py
[ ] Legacy adapters
[ ] test_state_adapter.py
[ ] test_intent_router.py
[ ] test_orchestrator.py
[ ] 7+ Override 专项案例
[ ] 现有测试全部通过
[ ] Legacy 完整指标无回归
[ ] 接口使用说明
[ ] fallback 诊断示例
```
