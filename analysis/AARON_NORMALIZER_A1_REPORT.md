# Aaron A1 — Catalog Normalizer 验证报告

> 验证日期：2026-08-30  
> 分支基线：`feature/ykzhao0829`  
> 数据：冻结的 `data/catalog.jsonl`

## 结论

阶段 A1 已完成：Catalog Normalizer 能在本地离线处理完整目录，所有
`parent_asin` 均保留且唯一，源商品字典不被修改。

| 项目 | 结果 |
| --- | ---: |
| 输入商品 | 50,000 |
| 标准化商品 | 50,000 |
| 唯一 ASIN | 50,000 |
| 丢失 ASIN | 0 |
| 开启 `tracemalloc` 后的初始化时间 | 89.084 秒 |
| Python 峰值分配内存 | 110.182 MiB |

时间和峰值内存均来自开启 Python 逐分配追踪的保守测量。

## 已实现范围

- 不可变 `ExtractedValue`、`NormalizedProduct` 与构建统计类型；
- material、color、audience、brand、size、style、category、price 抽取；
- details → category/store → features → title → description 的置信度层级；
- 复用 `attribute_lexicons.py`，包括 `grey → gray`、
  `road running → running_shoes` 与 `faux leather → faux_leather`；
- canonical value 去重，高置信度来源不被低置信度来源覆盖；
- 数值价格和严格价格字符串解析；缺失、非法及 `from $...` 保持 UNKNOWN；
- 未知的结构化开放值经基础归一化后保留；
- 完整 feature 文本经轻量归一化后保留，支持开放词汇排序；
- 紧凑的 ASIN → `NormalizedProduct` 内存索引；
- 重复 ASIN、缺失 ASIN 和非对象 JSONL 的显式错误。

## 自动测试覆盖

`tests/test_catalog_normalizer.py` 覆盖：

- 输入不可变与 ASIN 保留；
- 字段来源优先级和 canonical 去重；
- 材质组合；
- `faux_leather != leather`；
- 未知开放值保留；
- 数值、字符串、缺失和非法价格；
- 类目与 feature 归一化；
- confidence 边界；
- 重复 ASIN 拒绝。

复现命令：

```bash
python3 -m unittest -v tests.test_catalog_normalizer
python3 -c "from starter.catalog_normalizer import benchmark_catalog; print(benchmark_catalog('data/catalog.jsonl')[1])"
```

## A1 边界

本阶段不实现三态 Matcher、Hybrid Retriever 或 Ranker。低置信度 title/description
抽取结果仅作为证据保存；是否允许硬过滤由阶段 A2 的 Matcher 决定。
