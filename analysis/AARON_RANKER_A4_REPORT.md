# Aaron A4 — Local Constraint Ranker 验证报告

> 验证日期：2026-08-30  
> 分支：`feature/Aaron0829`  
> 数据：冻结目录 50,000 商品、公开开发集 200 sessions

## 结论

阶段 A4 已完成。Ranker 消费 `SearchRequest + CandidatePool`，使用 A1 商品索引
和 A2 三态 Matcher，先移除高置信度 hard/excluded 冲突，再计算可解释本地特征并
输出确定性的 `RankingResult`。

在相同 CandidatePool 上：

| 指标 | 纯 RRF | Local Ranker |
| --- | ---: | ---: |
| MRR | 0.557292 | **0.719400** |
| Top-10 Hit Rate | 0.855000 | **0.920000** |

Target filter survival 为 **1.000000**，超过任务书 `≥99%` 的要求。

## 排序流程

```text
CandidatePool
→ A2 ConstraintMatch
→ 过滤 confidence >= 0.85 的 hard/excluded MISMATCH
→ 保留 UNKNOWN 和 soft MISMATCH
→ 计算 RankingExplanation
→ 本地加权分数
→ final_score 降序、parent_asin 升序
→ RankingResult
```

## 特征与权重

| 特征 | 权重 | 说明 |
| --- | ---: | --- |
| rrf | 1.00 | CandidatePool 内按最大 RRF 稳定归一 |
| exact_phrase | 0.35 | 结构化类目/约束短语命中比例 |
| feature_overlap | 0.25 | query token 在商品规范化文本中的 recall |
| category_match | 0.25 | leaf 精确 1.0、path 上级 0.75 |
| hard_match | 0.25 | 可判断 hard constraints 的满足比例 |
| soft_match | 0.15 | 可判断 soft preferences 的满足比例 |
| popularity | 0.03 | `log1p(rating_number)` 池内归一 |
| profile_alignment | 0.03 | profile tag token 低权重匹配 |
| violation_penalty | -0.80 | hard/excluded 冲突及折半 soft mismatch |

`profile_alignment` 在配置校验中强制不超过 `0.03`。所有
`RankingExplanation` 特征均限制在 `[0,1]`。

## 固定 CandidatePool 评测

| 场景 | Sessions | RRF MRR | Ranker MRR |
| --- | ---: | ---: | ---: |
| Buying | 80 | 0.676906 | **0.817074** |
| Browsing | 80 | 0.388722 | **0.611374** |
| Intent Override | 30 | 0.726088 | **0.730997** |
| Boundary | 10 | 0.442549 | **0.767424** |

其他结果：

- Candidate Recall@200：`0.995`；
- target filter survival：`1.000`；
- 平均过滤商品数：`12.845 / session`；
- 平均因 UNKNOWN 保留商品数：`105.730 / session`；
- Ranker 平均延迟：`19.318 ms`；
- Ranker P95：`42.354 ms`；
- Ranker 最大延迟：`56.257 ms`；
- Normalizer 初始化：`55,384.618 ms`；
- Retriever 初始化：`1,491.132 ms`。

初始化与延迟为当前开发机测量，不作为跨机器性能承诺。

## Benchmark 输入边界

`ranker_a4_benchmark.py` 使用 evaluator 公开逻辑生成的完整 intent card，并构造
人工可信 `StateSnapshot`。Base category 只从开头类目锚点解析；尺寸文案不会被
当成预算，feature 文本中的其他商品词不会被当成类别覆盖。

这是 Aaron 独立模块评测，不是对 Ethan Parser 的联合评测。Ground truth 只用于
计算 target rank 和 filter survival，从未传给 Retriever 或 Ranker。

## 自动测试

`tests/test_ranker.py` 覆盖：

- Explanation 全部特征范围；
- UNKNOWN 保留与计数；
- hard MISMATCH 过滤；
- soft MISMATCH 保留并降权；
- excluded 命中过滤；
- Ranker uplift；
- ASIN tie-break；
- input/filtered/unknown 计数；
- profile 权重上限；
- 缺失商品和 route 不一致时抛 `RankingError`。

复现命令：

```bash
python3 -m unittest -v tests.test_ranker
python3 analysis/ranker_a4_benchmark.py
```

## A4 边界

Ranker 不修改对话状态、不重新判断 intent、不截取最终 Top-K，也不执行 Orchestrator
fallback。A5 由 Ethan 通过 `RankerProtocol` 注入并联合验证端到端指标。
