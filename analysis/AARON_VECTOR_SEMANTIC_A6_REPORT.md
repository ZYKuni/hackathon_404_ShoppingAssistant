# Aaron Todo 4.4 / 4.6 — Vector Retrieval 与语义重排报告

> 验证日期：2026-08-30  
> 分支：`feature/Aaron0829`  
> 数据：冻结目录 50,000 商品、公开开发集 200 sessions

## 结论

飞书 Todo 4.4 与 4.6 原有缺口已实现：

- Browsing 新增完全内存运行的稀疏 TF-IDF cosine vector route；
- Browsing Top-30 新增确定性的 leaf-category 多样化，且不改变 Top-200 成员；
- Ranker 新增只处理 Top-30 的本地 TF-IDF 语义相似度特征；
- vector route 或 semantic scorer 不可用时自动保留 BM25/category 与本地 Ranker；
- 无网络、模型下载、API Key、外部 token 或外部费用。

Vector route 在固定完整约束 CandidatePool 上产生正向提升，因此默认开启。Top-30
语义重排的 `0.02–0.12` 权重全部降低 Top-10，因此实现保留为显式 opt-in，默认关闭。

## 4.4 Browsing 检索轨道

当前 Browsing route：

```text
active_context_bm25
+ current_turn_bm25
+ category_anchor_bm25
+ use_case_bm25
+ tfidf_vector_similarity
→ weighted RRF
→ unique pre-filter Top-200
→ Top-30 category diversity reorder（成员不变）
```

TF-IDF index 使用 typed-array sparse postings，避免重型向量数据库。标题和类别使用
显式字段先验；查询和商品通过相同的 word TF-IDF 空间计算 cosine similarity。

### Vector 消融

| 阶段 | Recall@200 | Top-10 | MRR |
| --- | ---: | ---: | ---: |
| BM25/Category/Use-case Hybrid RRF | 0.995 | 0.795 | 0.551219 |
| **+ TF-IDF vector route** | **0.995** | **0.800** | **0.569393** |

Vector route 保持 Recall@200，并带来 `+0.005` Top-10 和 `+0.018174` MRR。
Browsing Top-30 平均包含 `5.811` 个 leaf category。

## 4.6 Top-30 语义 Ranker

语义 Ranker 在高置信度过滤和本地特征排序后，只对当前 Top-30 调用共享 TF-IDF
index 的 `score_many()`。`RankingExplanation.semantic_similarity` 规范化到 `[0,1]`。

| 语义权重 | Top-10 | MRR |
| ---: | ---: | ---: |
| 0（Local Ranker） | **0.900** | **0.729816** |
| 0.02 | 0.895 | 0.725457 |
| 0.04 | 0.895 | 0.725261 |
| 0.06 | 0.895 | 0.721905 |
| 0.08 | 0.895 | 0.721364 |
| 0.10 | 0.890 | 0.723628 |
| 0.12 | 0.890 | 0.723510 |

因此 `Agent.with_local_pipeline()` 默认不启用语义重排。实验时可显式使用：

```python
Agent.with_local_pipeline(
    "data/catalog.jsonl",
    enable_semantic_rerank=True,
)
```

这是消融门控，不是缺少实现：功能、Top-30 边界、Explanation、异常降级及测试均已
存在，但没有把公开集负收益策略强制上线。

## 工程指标

| 指标 | 结果 |
| --- | ---: |
| TF-IDF 文档数 | 50,000 |
| Vocabulary | 29,550 |
| Sparse postings | 2,723,174 |
| Posting buffer 估算 | 20.967 MiB |
| `tracemalloc` 峰值 | 58.239 MiB |
| TF-IDF 初始化（开启追踪） | 52.799 s |
| 完整正式 Pipeline 初始化 | 67.206 s |
| Lexical retrieval mean / P95 | 171.430 / 321.814 ms |
| Vector retrieval mean / P95 | 198.709 / 391.569 ms |
| Local Ranker mean / P95 | 22.514 / 45.240 ms |
| Semantic-only mean / P95 | 3.168 / 5.988 ms |
| 外部 token / 成本 | 0 / $0 |

## 端到端安全检查

默认配置（vector 开启、semantic 关闭、Official guarded rerank）：

| 指标 | 结果 |
| --- | ---: |
| Hit@10 | **0.840000** |
| MRR | 0.492200 |
| MTTC | **4.885000** |
| Technical Score | 0.689960 |
| fallback 事件 | 0 |

相对 A5，Hit 与 MTTC 不回退；MRR 从 `0.493280` 轻微变化为 `0.492200`。飞书的
“Hit Rate 不低于 0.84”已满足，但 MRR `≥0.51` 目标仍未达到。

## 测试

共 `100` 项测试全部通过。新增覆盖：

- TF-IDF 搜索相关性、确定性、范围和 category metadata；
- Browsing route 包含 vector、Buying 不调用 vector；
- vector 故障时 lexical fallback；
- diversity 重排不改变 CandidatePool 成员；
- semantic 只处理配置的 Top-N；
- semantic scorer 故障时保持本地 Ranker 顺序；
- 语义 Top-N 上限为 30；
- Agent 语义重排显式 opt-in。

复现：

```bash
python3 -m unittest discover -s tests
python3 analysis/semantic_a6_benchmark.py
python3 analysis/integration_a5_benchmark.py \
  --output analysis/vector_semantic_e2e_metrics.json
git diff --check
```
