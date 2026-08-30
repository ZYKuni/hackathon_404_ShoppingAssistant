# Aaron A2 — Constraint Matcher 验证报告

> 验证日期：2026-08-30  
> 分支：`feature/Aaron0829`  
> 依赖：A1 `Catalog Normalizer`

## 结论

阶段 A2 已完成。Matcher 对商品属性使用 `MATCH / MISMATCH / UNKNOWN`
三态判断；默认只有置信度不低于 `0.85` 的 hard 或 excluded 冲突会触发
`should_filter`。UNKNOWN 和 soft mismatch 均保留给 Ranker。

## 实现范围

- 不可变 `ConstraintMatch` 和 `ProductConstraintEvaluation`；
- 可配置的高置信度硬过滤阈值，默认 `0.85`；
- hard、soft、excluded 三组约束的独立评估；
- `StateSnapshot.category` 自动作为 hard category constraint；
- `price_min`、`price_max` 和未知价格；
- 多值正向约束的“任一匹配”语义；
- excluded 任一高置信度命中即冲突；
- 结构化多色值拆分及禁用颜色检测；
- 材质组合与 `faux_leather != leather`；
- 类目 leaf、path 上级和明确跨类判断；
- 弱 title/feature evidence 不产生硬冲突；
- feature/use-case 文本可用于正向匹配，但缺失或未命中保持 UNKNOWN。

## 关键安全边界

```text
hard/excluded MISMATCH + confidence >= threshold → 可过滤
hard/excluded UNKNOWN                              → 保留
soft MISMATCH                                     → 保留并交给 Ranker 降权
```

Matcher 不修改 `ConversationState`、`SearchRequest` 或商品目录，也不进行用户
意图解析。

## 自动测试

`tests/test_constraint_matcher.py` 覆盖：

- MATCH、MISMATCH、UNKNOWN；
- unknown price 不过滤；
- price min/max；
- 多值任一匹配；
- excluded 命中与多色商品；
- 材质组合与 faux leather；
- 弱来源冲突不硬过滤；
- soft mismatch 不过滤；
- 类目 leaf/path/cross-category；
- feature 匹配；
- threshold 配置；
- 非法多价格边界。

复现命令：

```bash
python3 -m unittest -v tests.test_constraint_matcher
python3 -m unittest discover -v
```

## A2 边界

本阶段只产生可解释的约束判断及过滤决定，不负责 CandidatePool 构建、RRF、
最终排序分数或 `RankingResult`。这些分别属于阶段 A3 和 A4。
