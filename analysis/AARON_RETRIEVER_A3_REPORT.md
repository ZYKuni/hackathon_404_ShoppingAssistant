# Aaron A3 — Hybrid Retriever 验证报告

> 验证日期：2026-08-30  
> 分支：`feature/Aaron0829`  
> 数据：冻结目录 50,000 商品、公开开发集 200 sessions

## 结论

阶段 A3 已完成。Retriever 消费不可变 `SearchRequest`，按 Buying/Browsing
选择不同的 BM25 路由，通过带权 RRF 合并证据，并输出过滤前、ASIN 唯一且
最多 200 个商品的 `CandidatePool`。

完整 intent card 的公开集 Recall@200 为 **0.995**，达到任务书“不低于
99.5%”的 A3 目标。

## 路由

Buying：

```text
active_context_bm25
+ current_turn_bm25
+ category_anchor_bm25
+ structured_constraint_bm25
→ weighted RRF → unique Top-200
```

Browsing：

```text
active_context_bm25
+ current_turn_bm25
+ category_anchor_bm25（同时携带 base request）
+ use_case_bm25
→ weighted RRF → unique Top-200
```

RRF 使用集中配置的 route weight 与 `k=60`。每个候选保存各路原始 rank、
BM25 score 和稳定 route name；最终按 RRF 降序、ASIN 升序确定性排序。

## 公开集检索指标

### 完整约束

| 场景 | Sessions | Recall@200 |
| --- | ---: | ---: |
| Buying | 80 | 0.9875 |
| Browsing | 80 | 1.0000 |
| Intent Override | 30 | 1.0000 |
| Boundary | 10 | 1.0000 |
| **Overall** | **200** | **0.9950** |

目标商品 CandidatePool rank：median `2`，P95 `41`。

可复现脚本本次运行的 FTS 初始化为 `1624.968 ms`；Retriever 平均延迟
`169.898 ms`，P95 `334.074 ms`，最大 `405.522 ms`。这些是当前开发机
测量，不作为跨机器性能承诺。

### 仅首轮消息

只输入 evaluator 首轮披露的信息时，Overall Recall@200 为 `0.5700`：

| 场景 | Recall@200 |
| --- | ---: |
| Buying | 0.7000 |
| Browsing | 0.3625 |
| Intent Override | 0.8333 |
| Boundary | 0.4000 |

该结果说明多轮信息积累对开放浏览场景是必要的，不应把首轮信息不足误判为
完整约束召回失败。

## 故障与边界处理

- 空 query 使用 `popularity_fallback`；
- 单一可选 route 失败时继续融合其他 route；
- 所有有效 route 均失败时抛 `RetrievalError`；
- 无命中时使用 popularity fallback；
- 目录外 ASIN 不进入 CandidatePool；
- 同一路重复 ASIN 只保留首次 rank；
- CandidatePool 是 Matcher 过滤前结果。

## 自动测试

`tests/test_retrieval.py` 覆盖 Buying/Browsing 路由差异、route rank、ASIN
去重、证据合并、RRF、Top-200、目录有效性、空查询、局部失败、全路失败、
fallback 和确定性排序。

复现命令：

```bash
python3 -m unittest -v tests.test_retrieval
python3 analysis/retrieval_a3_benchmark.py --mode full
python3 analysis/retrieval_a3_benchmark.py --mode initial
```

benchmark 使用 evaluator 根据 target 商品生成的公开 intent card，只用于离线
召回分析；Retriever 运行时不读取 ground truth，也不导入 evaluator。

## A3 边界

本阶段不执行 Matcher 过滤或最终特征排序。CandidatePool 后续由 A4 Ranker
消费，Ethan 仍负责 Orchestrator fallback 和最终 Top-K 截取。
