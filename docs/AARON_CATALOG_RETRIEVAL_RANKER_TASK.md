# Aaron 技术任务书：Catalog Normalizer、Hybrid Retrieval 与 Ranker

> 文档版本：1.0
>
> 适用分支基线：`feature/ykzhao0829`
>
> 公共接口版本：`PIPELINE_CONTRACT_VERSION = "1.0"`
>
> 优先级：P0
>
> 预计独立工作量：16–20 小时
>
> 本文是实施说明，不代表对应正式模块已经完成。

## 1. 任务目标

Aaron 负责把 Ethan 提供的只读 `SearchRequest` 转换为可解释的候选与排名结果：

```text
SearchRequest
→ Buying / Browsing 多路召回
→ 按 ASIN 去重并做 RRF
→ 统一 CandidatePool Top-200
→ Catalog Normalizer 查询商品属性
→ MATCH / MISMATCH / UNKNOWN 三态判断
→ 高置信度硬冲突处理
→ 本地可解释 Ranker
→ RankingResult
```

Aaron 不负责用户消息解析、ConversationState 修改、Intent Override 生命周期、RouteDecision 生成、提问策略或 Agent API Response。

## 2. 对标比赛要求

本任务直接覆盖比赛的以下要求：

| 比赛要求 | Aaron 的交付 |
| --- | --- |
| Buying 高精度过滤轨 | 多路 BM25、类别锚点、三态硬约束、结构化重排 |
| Browsing 多样化检索轨 | 类别、raw context、use case、feature route 的异构融合 |
| 多路检索 → 语义/本地排序 | Top-200 CandidatePool + 可解释 Ranker |
| 全内存运行 | SQLite FTS5 和内存属性索引 |
| 不使用重型向量数据库 | P0 不引入外部向量数据库 |
| Amazon 目录只读 | Normalizer 不改写目录和原始 JSONL |
| Hit Rate 与 MRR | 保持召回并提高 Top-10 排名 |
| 可行性与成本 | 核心实现无 API、无 token、无网络依赖 |
| 错误分析 | RouteEvidence、RankingExplanation、过滤计数 |

当前分析表明，完整约束下 BM25 Top-100 覆盖率为 99.5%，而当前 Agent 仍有大量 Top-200 内重排失败。因此 P0 应优先做好候选池、三态判断和本地 Ranker，而不是先增加大型 Dense 模型。

## 3. 当前技术基线

现有 `starter/agent.py` 提供：

- SQLite FTS5；
- Porter stemming；
- title/categories/features/details/store/description 多字段权重；
- current turn、active context、category anchor 三路 RRF；
- 每路默认截断 120；
- RRF 分母常数 60；
- 完全离线 fallback。

当前公开集指标：

| 指标 | 基线 |
| --- | ---: |
| Hit Rate@10 | 0.840000 |
| MRR | 0.476401 |
| MTTC | 4.885 |
| Technical Score | 0.685220 |

Aaron 的模块必须通过消融证明：提升来自 Retriever、Filter 或 Ranker，而不是修改 evaluator 或利用标签泄漏。

## 4. Aaron 的文件范围

### 4.1 允许新建或主要修改

```text
starter/catalog_normalizer.py
starter/constraint_matcher.py
starter/retrieval.py
starter/ranker.py
tests/test_catalog_normalizer.py
tests/test_constraint_matcher.py
tests/test_retrieval.py
tests/test_ranker.py
```

如需拆分内部模块，可以增加：

```text
starter/catalog_types.py
starter/retrieval_index.py
starter/ranking_features.py
```

这些是 Aaron 内部实现，不应加入公共 Contract。

### 4.2 只读依赖

```text
starter/pipeline_contracts.py
starter/attribute_lexicons.py
data/catalog.jsonl
```

### 4.3 禁止修改

```text
starter/conversation_state.py
starter/constraint_parser.py
evaluator/local_evaluator.py
data/catalog.jsonl
data/public_set.jsonl
```

不要在 Retriever 或 Ranker 内调用 `apply_patch()`，不要根据商品结果反向修改用户状态。

## 5. Pipeline Contract v1：Aaron 消费和输出的接口

公共接口位于 `starter/pipeline_contracts.py`。

### 5.1 输入：`SearchRequest`

Aaron 可读取：

```text
route_decision.route
route_decision.confidence
current_message
raw_context
base_request
structured_query
state.category
state.hard_constraints
state.soft_preferences
state.excluded
profile
candidate_limit
top_k
turn
```

限制：

- `SearchRequest` 不可变；
- Aaron不能重写 route；
- Aaron不能向 hard constraints 添加 profile 标签；
- Aaron不能重新解析用户消息以产生新的 ConversationState；
- 如检索需要 query rewriting，只能生成内部查询，不得修改输入请求。

### 5.2 输出一：`CandidatePool`

```python
CandidatePool(
    candidates=(...),
    requested_limit=request.candidate_limit,
    route=request.route_decision.route,
    retrieval_latency_ms=elapsed_ms,
)
```

每个 Candidate：

```python
Candidate(
    parent_asin="B000...",
    evidence=(
        RouteEvidence("active_context_bm25", rank=2, score=-8.3),
        RouteEvidence("category_anchor_bm25", rank=7, score=-4.1),
    ),
    rrf_score=0.0412,
)
```

限制：

- 最多 200 个；
- ASIN 唯一；
- ASIN 必须在冻结目录中；
- CandidatePool 是过滤前结果；
- 同一候选的 route name 不重复；
- route rank 从 1 开始；
- `rrf_score` 非负；
- 输出顺序必须确定。

### 5.3 输出二：`RankingResult`

```python
RankingResult(
    candidates=(...),
    input_count=200,
    filtered_count=8,
    unknown_preserved_count=73,
    ranking_latency_ms=elapsed_ms,
)
```

每个 RankedCandidate 必须包含：

```python
RankedCandidate(
    parent_asin="B000...",
    final_score=0.82,
    explanation=RankingExplanation(...),
)
```

Ranker 返回有序结果；Ethan 负责最终截取 `top_k` 和组装 Agent API。

### 5.4 Protocol

正式类必须满足：

```python
class HybridRetriever:
    def retrieve(self, request: SearchRequest) -> CandidatePool:
        ...


class LocalConstraintRanker:
    def rank(
        self,
        request: SearchRequest,
        pool: CandidatePool,
    ) -> RankingResult:
        ...
```

Ethan 只通过 `RetrieverProtocol` 和 `RankerProtocol` 调用，不依赖 Aaron 的具体类名。

## 6. Catalog Normalizer

### 6.1 内部数据结构

建议在 Aaron 内部定义：

```python
@dataclass(frozen=True)
class ExtractedValue:
    value: str | float
    source: str
    confidence: float


@dataclass(frozen=True)
class NormalizedProduct:
    parent_asin: str
    category_path: tuple[str, ...]
    leaf_categories: tuple[ExtractedValue, ...]
    audiences: tuple[ExtractedValue, ...]
    materials: tuple[ExtractedValue, ...]
    colors: tuple[ExtractedValue, ...]
    brands: tuple[ExtractedValue, ...]
    sizes: tuple[ExtractedValue, ...]
    styles: tuple[ExtractedValue, ...]
    price: ExtractedValue | None
    features: tuple[str, ...]
    average_rating: float
    rating_number: int
```

`NormalizedProduct` 不进入 `pipeline_contracts.py`。Retriever/Ranker 通过内部 `dict[parent_asin, NormalizedProduct]` 查询。

### 6.2 字段来源优先级

| 标准字段 | 第一来源 | 第二来源 | 第三来源 |
| --- | --- | --- | --- |
| material | `details.Material` | features | title/description |
| color | `details.Color` | features | title/description |
| audience | `details.Department` | categories | title |
| brand | `details.Brand` | store | manufacturer |
| size | `details.Size` | features | title |
| style | `details.Style` | features | title |
| category | 完整 categories path | leaf category | title fallback |
| price | 数值 price | 可靠字符串解析 | unknown |
| feature | 完整 features 原文 | details 中有购买意义的值 | description |

### 6.3 初始置信度建议

| 来源 | 置信度 |
| --- | ---: |
| 明确 details 属性 | 0.95 |
| category path | 0.85 |
| store / manufacturer | 0.85 |
| feature 精确别名 | 0.75 |
| title 精确别名 | 0.65 |
| description 精确别名 | 0.50 |
| 模糊 substring | 不用于硬冲突 |

这些是工程初值，后续可以消融，但不能使用目标标签为单个商品人工调置信度。

### 6.4 标准化规则

必须复用 `starter/attribute_lexicons.py`：

- `grey → gray`；
- `women's → women`；
- `road running → running_shoes`；
- `light weight → lightweight`；
- `faux leather → faux_leather`。

未知开放值不能静默丢弃。可标准化为空格/小写形式后保留，但低置信度值不得参与硬过滤。

### 6.5 多来源去重

相同 canonical value：

- 合并为一个值；
- 使用最高置信度；
- 保留最高置信度来源；
- 如需完整解释，可内部保留所有来源；
- 不允许低置信度值覆盖高置信度值。

### 6.6 价格解析

规则：

- JSON number → 高置信度数值；
- 纯数字字符串或明确 `$12.99` → 可解析；
- `from $12.99` 只能作为下限或低置信度价格，不得伪装为确定价格；
- 无法解析的字符串 → `None`；
- 缺失价格 → `None`；
- 负价格或明显非法值不进入过滤。

价格未知不能等同于超预算。

### 6.7 内存要求

目录有 50,000 商品。避免为每个商品重复保存大型 raw searchable text：

- FTS 表已经存储检索文本；
- NormalizedProduct 只保存过滤和排序需要的紧凑值；
- 共享 canonical string 可以考虑 intern，但 P0 不必过度优化；
- 初始化时记录耗时和峰值内存。

## 7. 三态 Constraint Matcher

建议内部定义：

```python
class MatchState(str, Enum):
    MATCH = "match"
    MISMATCH = "mismatch"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ConstraintMatch:
    field: str
    expected: tuple[str | int | float, ...]
    state: MatchState
    confidence: float
    reason: str
```

### 7.1 通用规则

- 商品有高置信度值且满足 → MATCH；
- 商品有高置信度值且明确冲突 → MISMATCH；
- 商品缺值、来源弱或无法可靠比较 → UNKNOWN；
- UNKNOWN 必须保留；
- 只有高置信度 hard MISMATCH 才允许硬过滤；
- soft MISMATCH 只降权；
- excluded 命中的惩罚高于普通 soft mismatch。

建议硬冲突过滤阈值：

```text
product attribute confidence >= 0.85
```

### 7.2 多值正向约束

用户接受：

```text
color = [black, blue]
```

语义：

- 商品包含任一值 → MATCH；
- 商品明确只有其他值 → MISMATCH；
- 商品颜色未知 → UNKNOWN。

### 7.3 excluded

```text
excluded.color = [white, red]
```

- 商品明确包含 white → MISMATCH；
- 商品明确是 black → MATCH；
- 商品颜色未知 → UNKNOWN；
- 多色商品同时包含 black 和 white → MISMATCH。

### 7.4 材质组合

```text
95% polyester, 5% spandex
```

用户要求 polyester 或 spandex 时均可 MATCH。

必须避免 substring 错误：

- `faux_leather != leather`；
- `synthetic_leather != genuine leather`；
- canonical token 集匹配优先于字符串包含。

### 7.5 价格

- `price_max=120`，商品价格 100 → MATCH；
- `price_max=120`，商品价格 150 → MISMATCH；
- 商品价格未知 → UNKNOWN；
- `price_min`/`price_max` 同时存在时分别判断；
- 不因目录大面积缺价而批量过滤。

### 7.6 类别

类别匹配建议分层：

- canonical leaf 完全相同 → 1.0；
- 用户类目是商品 path 的上级 → 0.75；
- 同一大类但末级不同 → 0.25–0.5；
- 明确跨类 → MISMATCH；
- 无法映射 → UNKNOWN。

P0 应避免仅靠少量枚举硬过滤 800 个长尾 leaf category。

## 8. Hybrid Retrieval

### 8.1 Route 名称

使用 Contract 中约定的稳定字符串：

```text
active_context_bm25
current_turn_bm25
category_anchor_bm25
structured_constraint_bm25
use_case_bm25
popularity_fallback
vector_similarity        # P1 预留，P0 不要求
```

不要在不同模块中使用 `active`、`context`、`full_query` 等不一致名字。

### 8.2 Buying 轨道

```text
active_context_bm25
+ current_turn_bm25
+ category_anchor_bm25
+ structured_constraint_bm25
→ RRF
→ unique Top-200
```

目标：

- 精确召回；
- 保留 category anchor；
- 利用结构化硬约束；
- 不在形成 CandidatePool 前做激进过滤。

### 8.3 Browsing 轨道

P0：

```text
active_context_bm25
+ category_anchor_bm25
+ base_request route
+ use_case_bm25
→ RRF
→ unique Top-200
```

可以在 Top-10 最终阶段评估多样性，但不能为了表面多样性显著损害隐藏目标排名。所有多样性逻辑必须有消融结果。

### 8.4 P0 不做的内容

- sentence-transformer；
- 外部 embedding API；
- 重型向量数据库；
- LLM query rewrite；
- LLM reranker；
- API Key 依赖。

只有在固定 Top-200 分析证明 Browsing 存在明显语义召回缺口时，才进入 P1 vector route。

### 8.5 RRF

通用公式：

```text
RRF(document) = Σ route_weight / (k + route_rank)
```

建议从当前 `k=60` 开始，避免同时改变太多变量。

要求：

- 每路保存原始 rank；
- route weight 在配置中集中管理；
- 相同 ASIN 合并 RouteEvidence；
- final tie-breaker 使用 `parent_asin`；
- 先形成过滤前 Top-200；
- 不允许根据 ground truth 动态调整单个会话权重。

### 8.6 空查询和局部失败

- 空 query 使用 popularity fallback；
- 单路失败时继续使用其他 route；
- 可选 route 不可用不能让主流程失败；
- 所有有效 route 都失败时抛 `RetrievalError`；
- 不在 Retriever 内调用 Ethan 的 Legacy fallback，正式 fallback 由 Orchestrator 决定。

## 9. CandidatePool Top-200

唯一正式定义：

```text
各 route 返回候选
→ ASIN 去重
→ 汇总 RouteEvidence
→ RRF 排序
→ 截断 candidate_limit（最大 200）
→ CandidatePool
→ 三态判断和过滤
```

因此：

- CandidatePool 是过滤前候选；
- Top-200 Recall 使用 CandidatePool 计算；
- Filter survival 使用 CandidatePool 与 RankingResult 对比；
- 不允许把过滤后的列表称为 Top-200；
- 不允许 CandidatePool 包含目录外或重复 ASIN。

## 10. Local Constraint Ranker v1

### 10.1 输入

```text
SearchRequest
+ CandidatePool
+ internal NormalizedProduct index
```

### 10.2 第一版特征

| 特征 | 定义 | 范围 |
| --- | --- | ---: |
| rrf | CandidatePool 内 RRF min-max 或稳定归一 | 0–1 |
| exact_phrase | 有意义完整短语的命中比例 | 0–1 |
| feature_overlap | query feature token recall | 0–1 |
| category_match | 类目层级匹配 | 0–1 |
| hard_match | 可判断 hard constraint 的满足比例 | 0–1 |
| soft_match | 可判断 soft preference 的满足比例 | 0–1 |
| violation_penalty | hard/excluded 明确冲突强度 | 0–1 |
| popularity | `log1p(rating_number)` 归一 | 0–1 |
| profile_alignment | 画像标签的低权重匹配 | 0–1 |

所有值写入公共 `RankingExplanation`。

### 10.3 初始公式

```text
final_score =
    1.00 * rrf
  + 0.35 * exact_phrase
  + 0.25 * feature_overlap
  + 0.25 * category_match
  + 0.25 * hard_match
  + 0.15 * soft_match
  + 0.03 * popularity
  + 0.03 * profile_alignment
  - 0.80 * violation_penalty
```

这些权重是实验初值，不是最终答案。必须逐项消融，不能在完整 200 条数据上无限调参。

### 10.4 过滤与排序顺序

```text
CandidatePool
→ 计算 ConstraintMatch
→ 移除高置信度 hard/excluded MISMATCH
→ 保留 UNKNOWN
→ 计算全部特征
→ final_score
→ final_score 降序
→ parent_asin 稳定 tie-break
→ RankingResult
```

### 10.5 `RankingResult` 计数

- `input_count`：CandidatePool 数量；
- `filtered_count`：明确硬冲突而删除的数量；
- `unknown_preserved_count`：因未知属性而保留的数量；
- `ranking_latency_ms`：不含 Retriever 时间。

`len(candidates)` 不能超过 `input_count - filtered_count`。

### 10.6 画像使用

P0 建议默认权重不超过 0.03：

- 画像只做 tie-break；
- 不进入 hard filter；
- 当前明确需求优先；
- 若消融无稳定提升，应设置为 0；
- 不根据公开 target 为单个画像手调权重。

## 11. 异常边界

### 11.1 Aaron 内部吸收

- 单个商品字段不可解析；
- 某一个非必要 route 失败；
- 可选 vector route 不可用；
- 某一属性无法确定 → UNKNOWN；
- 个别非法价格 → price unknown。

### 11.2 对 Orchestrator 抛出

- 所有可用检索 route 失败；
- FTS 索引不可用；
- CandidatePool 无法构建；
- Ranker 输入违反 Contract；
- Ranker 无法返回确定性结果。

使用：

```python
raise RetrievalError("...")
raise RankingError("...")
```

不要返回结构不完整的 dict，也不要捕获所有异常后静默返回空列表。

## 12. 测试要求

### 12.1 `test_catalog_normalizer.py`

- 50,000 商品可完整批处理；
- ASIN 不丢失；
- 原始 product dict 不被修改；
- material/color/audience/brand 来源优先级；
- canonical value 去重；
- `grey → gray`；
- `faux_leather != leather`；
- 数值、字符串、缺失、非法价格；
- unknown 开放值保留；
- confidence 范围合法。

### 12.2 `test_constraint_matcher.py`

- MATCH/MISMATCH/UNKNOWN；
- unknown price 不过滤；
- price_min/price_max；
- 多值任一匹配；
- excluded 任一命中；
- 多色包含禁用颜色；
- 材质组合；
- faux leather；
- 弱来源冲突不硬过滤；
- 类目上级/末级匹配。

### 12.3 `test_retrieval.py`

- Buying/Browsing 使用不同 route 配置；
- 各 route rank 正确；
- ASIN 去重；
- RouteEvidence 合并；
- RRF 计算；
- CandidatePool 最多 200；
- CandidatePool 过滤前定义；
- ASIN 全部目录有效；
- 空查询 popularity fallback；
- 单 route 失败；
- 所有 route 失败抛 RetrievalError；
- 输出顺序确定。

### 12.4 `test_ranker.py`

- 所有 Explanation 特征在 0–1；
- UNKNOWN 保留；
- hard MISMATCH 过滤；
- soft MISMATCH 不过滤；
- excluded 惩罚；
- final score 顺序；
- ASIN tie-break；
- 计数正确；
- RankingResult 无重复；
- profile 权重不超过约定；
- 故障时抛 RankingError。

## 13. 消融与评测顺序

不要一次启用所有功能。建议：

| 实验 | CandidatePool | Matcher | Ranker |
| --- | --- | --- | --- |
| A0 | Legacy | 无 | Legacy |
| A1 | Top-200 RRF | 无 | RRF |
| A2 | Top-200 RRF | 三态但不硬过滤 | RRF |
| A3 | Top-200 RRF | 高置信度过滤 | RRF |
| A4 | Top-200 RRF | 高置信度过滤 | Local Ranker |
| A5 | 同 A4 | 同 A4 | 加 profile 低权重 |

每次记录：

- Overall Hit/MRR/MTTC/Score；
- Buying/Browsing/Intent Override/Boundary 指标；
- Top-200 target recall；
- target filter survival；
- rank uplift/downlift；
- 初始化时间；
- Retriever P50/P95；
- Ranker P50/P95；
- 峰值内存。

## 14. 执行顺序

### 阶段 A1：Normalizer，5–7 小时

- 内部类型；
- 字段抽取；
- 共享词典；
- 50,000 商品验证；
- 初始化和内存记录。

### 阶段 A2：Matcher，3–4 小时

- 三态语义；
- 价格和多值；
- confidence threshold；
- 单元测试。

### 阶段 A3：Retriever，4–5 小时

- 消费人工 SearchRequest；
- Buying/Browsing route；
- RRF 和 Top-200；
- 局部失败处理。

### 阶段 A4：Ranker，4–5 小时

- 归一化特征；
- Explanation；
- 高置信度过滤；
- RankingResult。

### 阶段 A5：Ethan 集成，联合工作

- 通过 Protocol 注入；
- 不修改 Orchestrator 内部状态语义；
- 按消融顺序开启功能；
- 共同检查指标变化。

## 15. Aaron 独立验收标准

### 功能

- 50,000 商品全部可标准化；
- CandidatePool Top-200 无重复且 ASIN 有效；
- Buying/Browsing 使用不同 route 配置；
- UNKNOWN 不被硬过滤；
- RankingExplanation 完整；
- 无网络和 API Key 可运行。

### 测试

- 当前 32 项测试全部通过；
- 新增 Aaron 测试全部通过；
- 不修改 evaluator 和比赛数据；
- 使用人工 SearchRequest 即可独立运行；
- `git diff --check` 通过。

### 指标

独立模块：

- 完整约束 Top-200 Recall 不低于当前 Top-100 参考值 99.5%；
- target filter survival `≥99%`；
- 固定 CandidatePool 上 Ranker MRR 高于纯 RRF；
- 初始化时间、内存和延迟有记录。

联合集成：

- Hit Rate@10 `≥0.84`；
- MRR 目标 `≥0.51`；
- MTTC 不高于 `4.885`；
- Technical Score 最低目标 `≥0.695`；
- Intent Override Hit Rate `≥0.73` 是与 Ethan 的联合目标。

## 16. Aaron 明确不负责

- 用户消息转 StatePatch；
- 调用 `apply_patch()`；
- 完整或单槽位 Override 语义；
- active raw evidence 生命周期；
- Intent Router；
- 生成或修改 RouteDecision；
- Agent session 管理；
- Question Policy；
- 截取最终 Top-K；
- 组装 Agent API Response；
- Orchestrator fallback；
- 修改 evaluator；
- 使用 ground truth 做运行时特征。

## 17. P1 / Stretch 边界

只有 P0 达标后再考虑：

### P1

- 小型 TF-IDF 或本地向量 route；
- Top-20/30 轻量语义重排；
- precomputed embedding；
- Browsing 多样性策略。

### Stretch

- sentence-transformer；
- 可选外部 LLM reranker；
- token、成本和网络配置；
- 更复杂的用户画像对齐。

任何 P1/Stretch 都必须：

- 完全内存运行；
- 无重型向量数据库；
- 无网络时自动回到本地方案；
- 记录模型大小、初始化、内存和延迟；
- 不降低核心 Hit Rate。

## 18. 交付清单

```text
[ ] catalog_normalizer.py
[ ] constraint_matcher.py
[ ] retrieval.py
[ ] ranker.py
[ ] test_catalog_normalizer.py
[ ] test_constraint_matcher.py
[ ] test_retrieval.py
[ ] test_ranker.py
[ ] 50,000 商品标准化报告
[ ] Top-200 Recall 报告
[ ] Filter survival 报告
[ ] Ranker 特征与权重说明
[ ] A0–A4 消融结果
[ ] 初始化/内存/延迟记录
[ ] 无网络复现命令
```
