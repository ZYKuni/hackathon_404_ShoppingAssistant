# Aaron A5 — Ethan Pipeline 联合集成报告

> 验证日期：2026-08-30  
> 分支：`feature/Aaron0829`  
> 数据：冻结目录 50,000 商品、公开开发集 200 sessions

## 结论

A5 的工程集成已完成：现有 `Agent.reset()` / `Agent.respond()` 接口不变，Aaron 的
`HybridRetriever` 和 `LocalConstraintRanker` 通过 `RetrieverProtocol`、
`RankerProtocol` 注入编排器。正式链路具备不可变状态适配、可解释 Buying/Browsing
路由、Official/Development 两种运行模式以及检索/排序故障降级。

纯正式链路在真实多轮状态下未达到联合指标，因此没有直接替换 Legacy Top-10。
Official 模式采用消融验证后的安全上线策略：保留 Legacy Top-10 候选集合，正式
Ranker 以 `0.4` 权重做集合内重排。该策略保持基线 Hit 和 MTTC，并提升 MRR；但
MRR、Technical Score 与 Intent Override 指标仍低于任务书目标，需要下一轮继续优化。

## 集成边界

```text
ConversationState + UserProfile
→ immutable StateSnapshot / ProfileSnapshot
→ explainable IntentRouter
→ immutable SearchRequest
→ RetrieverProtocol (HybridRetriever)
→ RankerProtocol (LocalConstraintRanker)
→ Official guarded rerank / controlled fallback
→ unchanged Agent API response
```

- `Agent(catalog_path)` 继续使用 Legacy 路径，保证原调用兼容；
- `Agent.with_local_pipeline(catalog_path)` 显式启用正式链路；
- Retriever 失败时 Official 模式回退 Legacy；
- Ranker 失败时先使用 CandidatePool 的 RRF，再由 Legacy 候选集保护召回；
- Development 模式直接抛出预期 Pipeline Error，便于定位；
- 每个 session 的最近 fallback 可由 `pipeline_fallbacks()` 检查；
- 未修改 evaluator、比赛数据或 Aaron A1–A4 内部实现。

## 200-session 联合评测

| 策略 | Hit@10 | MRR | MTTC | Technical Score |
| --- | ---: | ---: | ---: | ---: |
| Legacy 基线 | **0.840000** | 0.476401 | **4.885000** | 0.685220 |
| 纯正式 Retriever + Ranker | 0.790000 | 0.374804 | 5.290000 | 0.621641 |
| **Official 安全重排（正式权重 0.4）** | **0.840000** | **0.493280** | **4.885000** | **0.690284** |
| 任务书目标 | ≥0.840000 | ≥0.510000 | ≤4.885000 | ≥0.695000 |

安全重排相对 Legacy：

- Hit@10：无回退；
- MRR：`+0.016879`（约 `+3.54%`）；
- MTTC：无回退；
- Technical Score：`+0.005064`；
- fallback 事件：`0`。

## 安全重排分场景结果

| 场景 | Sessions | Hit@10 | MRR | MTTC |
| --- | ---: | ---: | ---: | ---: |
| Buying | 80 | 0.887500 | 0.506349 | 4.925000 |
| Browsing | 80 | 0.862500 | 0.488224 | 4.025000 |
| Intent Override | 30 | 0.666667 | 0.425225 | 6.700000 |
| Boundary | 10 | 0.800000 | 0.633333 | 6.000000 |

Intent Override Hit Rate 为 `0.666667`，低于联合目标 `0.73`。本轮没有使用 ground
truth 作为运行时特征；ground truth 仅用于 evaluator 和离线消融计分。

## 消融判断

对 Legacy Top-10 候选集合测试正式 Ranker 权重 `0.0–1.0`：

- `0.0` 对应 Legacy，MRR `0.476401`；
- `0.4` 达到最高整体 MRR `0.493280`；
- `1.0` 降至 `0.438353`；
- 纯正式候选集合的 Hit 降至 `0.79`。

这说明 A4 在完整、可信 `StateSnapshot` 上的 uplift 尚不能直接迁移到实时 Parser
产生的部分状态。当前主要瓶颈是状态收敛与 Override 后候选召回，而不是 Protocol
连接或 fallback 稳定性。

## 测试与复现

共 `90` 项测试全部通过，覆盖：

- mutable state 到 immutable contract 的隔离；
- negative/no-preference 不进入正向 structured query；
- Buying/Browsing 规则、理由、signals 与 override 标记；
- Fake Retriever/Ranker 调用顺序；
- Official retrieval/ranking fallback；
- Development 异常透传；
- 正式 Agent API 与 session reset 联调；
- Official 候选集保护。

```bash
python3 -m unittest discover -s tests
python3 analysis/integration_a5_ablation.py
python3 analysis/integration_a5_benchmark.py
git diff --check
```

当前机器正式链路初始化约 `61.65 s`，主要来自 50,000 商品 CatalogNormalizer；
不依赖网络、外部 API 或 API Key。

## 下一步

1. 修正实时 Parser 对产品 feature 文案的误解析，并为字段增加来源置信度；
2. 针对完整 override 重新构造 active evidence，重点恢复目标候选召回；
3. 将 CatalogNormalizer 与检索索引构建合并或缓存，降低约一分钟冷启动；
4. 每次调整均继续使用消融门槛，只有同时不降低 Hit/MTTC 时才提高正式权重。
