# 商品目录数据质量与检索可用性分析

生成时间：2026-08-29 14:16 中国标准时间

## 技术摘要

- 50,000 件商品的 `parent_asin` 无重复、无格式异常，200 个公开目标商品全部能连接到目录；但目标集并非目录的缩小版，例如价格覆盖率从全目录的 21.1% 升至目标集的 89.0%（+67.9 个百分点，4.23×）。
- 评测器为 200 个目标生成 800 条硬约束或软偏好，主要类型为 feature 50.5%、material 37.8%。因此字段优先级应由约束生成机制和消融实验共同决定，而不能只看目录非空率。
- 使用全部字段时，真实首轮消息的 BM25 Top 10 目标覆盖率为 18.5%；拼接全部隐藏约束后的 oracle 查询为 87.0%。两者差距衡量“获得完整需求”对词法检索的潜在提升，不等同于最终 Agent 分数。
- 商品目录仍存在明显稀疏性：`description` 缺失 47.8%、`features` 缺失 10.4%，数值价格覆盖仅 20.8%。这些问题影响泛化，但公开目标集的覆盖情况必须单独判断。

## 分析范围与指标定义

- **全目录：** 50,000 个商品行，每行粒度为一个 `parent_asin`。
- **目标集：** `public_set.jsonl` 中 200 个不同目标 ASIN。
- **字段覆盖率：** 字段不为 `null`、空字符串、空列表或空对象的商品占比。
- **百分点差：** 目标组覆盖率减去全目录覆盖率；`+10 pp` 表示高10个百分点。
- **覆盖倍数：** 目标组覆盖率除以全目录覆盖率；它不是概率提升或因果效果。
- **首轮查询：** 本地评测器实际生成的第一条用户消息。
- **完整约束查询：** 粗粒度类目加该目标全部隐藏硬约束和软偏好，是诊断检索上限的 oracle，不代表 Agent 在首轮可以获得这些信息。
- **目标排名：** 使用与 Starter 相同的 FTS5 分词和 BM25 权重；首轮、完整约束和字段消融都观察到 Top 100。

## 目标集与全目录存在明显覆盖差异

| 字段 | 全目录覆盖率 | 目标集覆盖率 | 差异 | 倍数 |
| --- | --- | --- | --- | --- |
| `title` | 100.0% | 100.0% | +0.0 pp | 1.00× |
| `features` | 89.6% | 100.0% | +10.4 pp | 1.12× |
| `description` | 52.2% | 44.5% | -7.7 pp | 0.85× |
| `price` | 21.1% | 89.0% | +67.9 pp | 4.23× |
| `categories` | 100.0% | 100.0% | +0.0 pp | 1.00× |
| `details` | 96.7% | 100.0% | +3.3 pp | 1.03× |
| `store` | 99.4% | 100.0% | +0.6 pp | 1.01× |

**解释：** 目标集的 `features`、`details`、`store` 均为100%覆盖，而价格也远高于全目录。全目录缺失率仍决定私有集泛化风险，但公开集上的模块优先级不能直接由全目录平均值推出。

### 不同场景的字段覆盖

| 场景 | 字段 | 全目录覆盖率 | 场景目标覆盖率 | 差异 | 倍数 |
| --- | --- | --- | --- | --- | --- |
| buying | `title` | 100.0% | 100.0% | +0.0 pp | 1.00× |
| buying | `features` | 89.6% | 100.0% | +10.4 pp | 1.12× |
| buying | `description` | 52.2% | 48.8% | -3.5 pp | 0.93× |
| buying | `price` | 21.1% | 95.0% | +73.9 pp | 4.51× |
| buying | `categories` | 100.0% | 100.0% | +0.0 pp | 1.00× |
| buying | `details` | 96.7% | 100.0% | +3.3 pp | 1.03× |
| buying | `store` | 99.4% | 100.0% | +0.6 pp | 1.01× |
| browsing | `title` | 100.0% | 100.0% | +0.0 pp | 1.00× |
| browsing | `features` | 89.6% | 100.0% | +10.4 pp | 1.12× |
| browsing | `description` | 52.2% | 43.8% | -8.5 pp | 0.84× |
| browsing | `price` | 21.1% | 87.5% | +66.4 pp | 4.16× |
| browsing | `categories` | 100.0% | 100.0% | +0.0 pp | 1.00× |
| browsing | `details` | 96.7% | 100.0% | +3.3 pp | 1.03× |
| browsing | `store` | 99.4% | 100.0% | +0.6 pp | 1.01× |
| intent_override | `title` | 100.0% | 100.0% | +0.0 pp | 1.00× |
| intent_override | `features` | 89.6% | 100.0% | +10.4 pp | 1.12× |
| intent_override | `description` | 52.2% | 33.3% | -18.9 pp | 0.64× |
| intent_override | `price` | 21.1% | 76.7% | +55.6 pp | 3.64× |
| intent_override | `categories` | 100.0% | 100.0% | +0.0 pp | 1.00× |
| intent_override | `details` | 96.7% | 100.0% | +3.3 pp | 1.03× |
| intent_override | `store` | 99.4% | 100.0% | +0.6 pp | 1.01× |
| boundary | `title` | 100.0% | 100.0% | +0.0 pp | 1.00× |
| boundary | `features` | 89.6% | 100.0% | +10.4 pp | 1.12× |
| boundary | `description` | 52.2% | 50.0% | -2.2 pp | 0.96× |
| boundary | `price` | 21.1% | 90.0% | +68.9 pp | 4.27× |
| boundary | `categories` | 100.0% | 100.0% | +0.0 pp | 1.00× |
| boundary | `details` | 96.7% | 100.0% | +3.3 pp | 1.03× |
| boundary | `store` | 99.4% | 100.0% | +0.6 pp | 1.01× |

**解释：** 每个场景的样本量不同，尤其 Boundary 只有10个样本，因此其覆盖倍数只用于描述，不应当作稳定规律。

### 不同难度的字段覆盖

| 难度 | 字段 | 全目录覆盖率 | 难度组目标覆盖率 | 差异 | 倍数 |
| --- | --- | --- | --- | --- | --- |
| easy | `title` | 100.0% | 100.0% | +0.0 pp | 1.00× |
| easy | `features` | 89.6% | 100.0% | +10.4 pp | 1.12× |
| easy | `description` | 52.2% | 48.8% | -3.5 pp | 0.93× |
| easy | `price` | 21.1% | 95.0% | +73.9 pp | 4.51× |
| easy | `categories` | 100.0% | 100.0% | +0.0 pp | 1.00× |
| easy | `details` | 96.7% | 100.0% | +3.3 pp | 1.03× |
| easy | `store` | 99.4% | 100.0% | +0.6 pp | 1.01× |
| medium | `title` | 100.0% | 100.0% | +0.0 pp | 1.00× |
| medium | `features` | 89.6% | 100.0% | +10.4 pp | 1.12× |
| medium | `description` | 52.2% | 44.4% | -7.8 pp | 0.85× |
| medium | `price` | 21.1% | 87.8% | +66.7 pp | 4.17× |
| medium | `categories` | 100.0% | 100.0% | +0.0 pp | 1.00× |
| medium | `details` | 96.7% | 100.0% | +3.3 pp | 1.03× |
| medium | `store` | 99.4% | 100.0% | +0.6 pp | 1.01× |
| hard | `title` | 100.0% | 100.0% | +0.0 pp | 1.00× |
| hard | `features` | 89.6% | 100.0% | +10.4 pp | 1.12× |
| hard | `description` | 52.2% | 33.3% | -18.9 pp | 0.64× |
| hard | `price` | 21.1% | 76.7% | +55.6 pp | 3.64× |
| hard | `categories` | 100.0% | 100.0% | +0.0 pp | 1.00× |
| hard | `details` | 96.7% | 100.0% | +3.3 pp | 1.03× |
| hard | `store` | 99.4% | 100.0% | +0.6 pp | 1.01× |

**解释：** 难度标签来自公开集设定。本表能显示元数据完整性是否与难度相关，但不能证明缺失字段导致了难度。

公开集中场景与难度高度绑定：

| 场景 | 难度 | 样本数 |
| --- | --- | --- |
| buying | easy | 80 |
| browsing | medium | 80 |
| intent_override | hard | 30 |
| boundary | medium | 10 |

因此，easy/medium/hard 的差异不能与场景效应分离：easy 全部是 Buying，hard 全部是 Intent Override，medium 由 Browsing 和 Boundary 构成。后续不应把“难度组差异”解释成独立的难度因果效应。

## 评测器数据生成链路

```text
public_set 中的 ground_truth.parent_asin
  → 回查 catalog 目标商品
  → categories 最后两级生成粗粒度初始品类
  → title / features / details / description / categories / store
       检测 material 与 color
  → features 条目 + details 键值 + 可选 price
       按固定顺序选前2条硬约束、后2条软偏好
  → scenario 决定首轮透露方式
       Buying：品类 + 第一条硬约束
       Browsing / Boundary：只给品类
       Intent Override：先给旧偏好，第3或4轮再覆盖为新约束
  → Agent 的 ask_attribute 决定下一条可透露约束
  → Agent 返回最多10个 parent_asin
  → 精确 ID 相等才命中
```

本脚本独立复现了公开目标的 intent card，并与评测器输出逐项对照；不一致样本数为 **0**。这项分析只用于理解公开评测机制，Agent 运行时不会收到目标商品或隐藏约束。

## 隐藏需求主要由 feature 与 material 构成

| 约束类型 | 数量 | 占比 |
| --- | --- | --- |
| feature | 404 | 50.5% |
| material | 302 | 37.8% |
| color | 60 | 7.5% |
| style | 19 | 2.4% |
| size | 11 | 1.4% |
| use_case | 4 | 0.5% |

| 约束来源 | 数量 | 占比 |
| --- | --- | --- |
| features | 602 | 75.2% |
| material_regex:features | 114 | 14.2% |
| material_regex:title | 36 | 4.5% |
| color_regex:features | 17 | 2.1% |
| color_regex:title | 14 | 1.8% |
| color_regex:details | 7 | 0.9% |
| details | 5 | 0.6% |
| material_regex:description | 3 | 0.4% |
| color_regex:description | 2 | 0.2% |

下表展示数量最多的“来源—类型”组合：

| 来源 | 约束类型 | 数量 |
| --- | --- | --- |
| features | feature | 401 |
| features | material | 149 |
| material_regex:features | material | 114 |
| material_regex:title | material | 36 |
| features | color | 20 |
| features | style | 17 |
| color_regex:features | color | 17 |
| color_regex:title | color | 14 |
| features | size | 11 |
| color_regex:details | color | 7 |
| features | use_case | 4 |
| material_regex:description | material | 3 |
| details | feature | 3 |
| details | style | 2 |
| color_regex:description | color | 2 |

**解释：** `material_regex:字段名` 和 `color_regex:字段名` 表示评测器在合并文本中正则命中，并由脚本追溯到最早出现该词的商品字段。其余来源表示约束直接来自 features、details、price 或标题回退。

## 完整需求显著改变 BM25 目标排名

### 按场景观察

| 场景 | 查询阶段 | Top 10 | Top 50 | Top 100 | 命中样本中位排名 |
| --- | --- | --- | --- | --- | --- |
| buying | 首轮 | 23.8% | 47.5% | 58.8% | 22.00 |
| buying | 完整约束 | 86.2% | 95.0% | 98.8% | 1.00 |
| browsing | 首轮 | 2.5% | 18.8% | 36.2% | 50.00 |
| browsing | 完整约束 | 87.5% | 98.8% | 100.0% | 1.00 |
| intent_override | 首轮 | 53.3% | 66.7% | 83.3% | 3.00 |
| intent_override | 完整约束 | 86.7% | 96.7% | 100.0% | 1.00 |
| boundary | 首轮 | 0.0% | 30.0% | 40.0% | 21.50 |
| boundary | 完整约束 | 90.0% | 100.0% | 100.0% | 1.00 |

**注意：** Intent Override 在新意图到达前禁止转化，因此它的首轮排名只是诊断旧偏好造成的偏移，不能与 Buying 的首轮转化能力直接比较。

### 按难度观察

| 难度 | 查询阶段 | Top 10 | Top 50 | Top 100 | 命中样本中位排名 |
| --- | --- | --- | --- | --- | --- |
| easy | 首轮 | 23.8% | 47.5% | 58.8% | 22.00 |
| easy | 完整约束 | 86.2% | 95.0% | 98.8% | 1.00 |
| medium | 首轮 | 2.2% | 20.0% | 36.7% | 47.00 |
| medium | 完整约束 | 87.8% | 98.9% | 100.0% | 1.00 |
| hard | 首轮 | 53.3% | 66.7% | 83.3% | 3.00 |
| hard | 完整约束 | 86.7% | 96.7% | 100.0% | 1.00 |

**解释：** 若完整约束后目标仍未进入 Top 100，主要瓶颈更可能是词法召回、字段噪声或同词商品竞争；若已进入 Top 100 但不在 Top 10，则更适合优先改进重排。

## BM25字段消融揭示单字段能力与边际贡献

所有消融都使用完整约束查询，并使用原 Starter 对应字段权重。`only_*` 只允许指定字段参与匹配，`without_*` 从全字段索引中移除一个字段；结果观察至 Top 100。

全字段基准：Top 10 = 87.0%，Top 50 = 97.0%，Top 100 = 99.5%，MRR@100 = 0.7257。

### 单字段能力

| 仅使用字段 | Top 10 | Top 50 | Top 100 | MRR@100 |
| --- | --- | --- | --- | --- |
| title | 12.5% | 29.0% | 32.5% | 0.0746 |
| categories | 1.5% | 23.0% | 36.5% | 0.0140 |
| features | 68.0% | 81.0% | 84.0% | 0.5798 |
| details | 4.5% | 6.0% | 7.0% | 0.0164 |

### 移除单字段后的变化

| 移除字段 | Top 10 | 相对全字段 | Top 100 | 相对全字段 | MRR@100 |
| --- | --- | --- | --- | --- | --- |
| title | 86.5% | -0.5 pp | 99.5% | +0.0 pp | 0.7370 |
| categories | 71.0% | -16.0 pp | 88.5% | -11.0 pp | 0.5919 |
| features | 21.0% | -66.0 pp | 52.5% | -47.0 pp | 0.1236 |
| details | 86.0% | -1.0 pp | 98.5% | -1.0 pp | 0.7212 |

**解释：** Single-field 衡量独立可召回性，drop-one 衡量在其他字段已存在时的边际贡献。字段之间高度重复，因此两类结果不应混为一谈；这些结果也只适用于当前公开集和固定查询构造。

**重要限制：** 评测器本身主要从目标商品 `features` 抽取约束，完整约束查询又包含这些原文，因此 features 的优势部分来自数据生成机制的直接耦合。它是本赛题公开评测的重要信号，但不能外推为真实电商数据中的普遍字段价值。

## 目录主键可靠，但稀疏字段会影响过滤

本分析以一行一个 `parent_asin` 为商品粒度。空值同时包含 JSON `null`、空字符串、空列表和空对象。

| 字段 | 空值数 | 空值率 | 观测类型 |
| --- | --- | --- | --- |
| `parent_asin` | 0 | 0.0% | string: 50000 |
| `title` | 2 | 0.0% | string: 50000 |
| `features` | 5,219 | 10.4% | list: 50000 |
| `description` | 23,887 | 47.8% | list: 50000 |
| `price` | 39,473 | 78.9% | null: 39473, number: 10410, string: 117 |
| `categories` | 0 | 0.0% | list: 50000 |
| `details` | 1,670 | 3.3% | object: 50000 |
| `average_rating` | 0 | 0.0% | number: 50000 |
| `rating_number` | 0 | 0.0% | number: 50000 |
| `store` | 314 | 0.6% | string: 49686, null: 314 |

**检索影响：** `parent_asin` 可安全用于评分和连接；价格与描述只能作为部分覆盖信号。`details` 虽然非空，但键名自由变化，需要先规范化才能用于结构化过滤。

## 多字段文本召回是必要条件

下表展示每个 Starter 搜索字段的非空覆盖率。这里的“覆盖”只表示字段有内容，不表示内容一定包含用户所需属性。

| 检索字段 | 非空商品数 | 覆盖率 |
| --- | --- | --- |
| `title` | 49,998 | 100.0% |
| `categories` | 50,000 | 100.0% |
| `features` | 44,781 | 89.6% |
| `details` | 48,330 | 96.7% |
| `store` | 49,686 | 99.4% |
| `description` | 26,113 | 52.2% |

**说明：** 非空覆盖只能说明字段可用，不代表字段有助于排名。具体字段优先级应以上面的 single-field 与 drop-one 消融为准。

## 价格信号覆盖不足且存在边界值

| 指标 | 值 |
| --- | --- |
| 有数值价格的商品 | 10,410 |
| 字符串价格 | 117 |
| 可解析字符串价格 | 5 |
| 不可解析字符串价格 | 112 |
| 最小值 | $0.00 |
| 25 分位 | $14.99 |
| 中位数 | $22.88 |
| 75 分位 | $39.99 |
| 95 分位 | $138.92 |
| 最大值 | $4,119.00 |
| 非正价格 | 1 |
| 高于 $1,000 | 22 |

**风险：** 如果预算过滤只保留数值价格，会一次性丢掉 79.2% 的目录，显著降低目标召回率。字符串值中有 112 个无法可靠转换，另有 5 个形如 `from 12.99` 的下限价格。报告只识别统计异常，不断言高价商品一定错误。

## 商品类目呈长尾分布

目录共有 863 个不同类目标签和 800 个不同末级类目。类目路径中位深度为 5.0。

| 高频末级类目 | 商品数 |
| --- | --- |
| T-Shirts | 2,807 |
| Shoes | 1,299 |
| Westlake | 1,136 |
| Casual | 1,099 |
| Wrist Watches | 1,034 |
| Fashion Sneakers | 1,017 |
| Flats | 927 |
| Blouses & Button-Down Shirts | 691 |
| Loafers & Slip-Ons | 665 |
| Dresses | 656 |
| Pumps | 630 |
| Sets | 610 |
| Sandals | 586 |
| Platforms & Wedges | 545 |
| Sunglasses | 540 |

**检索影响：** 不宜只维护少量固定品类枚举。建议同时保存完整类目路径和末级类目，并为高频同义词建立轻量映射。

## `details` 键名丰富，需要规范化属性层

`details` 中共出现 287 种键名，最常见的键如下。

| Details 键 | 出现次数 |
| --- | --- |
| Date First Available | 46,886 |
| Department | 43,582 |
| Item model number | 27,729 |
| Package Dimensions | 27,061 |
| Manufacturer | 23,512 |
| Is Discontinued By Manufacturer | 13,070 |
| Product Dimensions | 10,210 |
| Item Weight | 3,243 |
| Color | 2,439 |
| Brand | 2,328 |
| Material | 2,069 |
| Style | 1,752 |
| Best Sellers Rank | 1,127 |
| Age Range (Description) | 1,104 |
| Size | 925 |
| Country of Origin | 705 |
| Brand Name | 610 |
| Part Number | 576 |
| Suggested Users | 535 |
| Package Weight | 532 |

**建议：** 优先映射 `Department`、`Manufacturer`、尺寸/材质相关键；日期、包装尺寸等字段可保留在搜索文本中，但不应默认当作用户购买约束。

## 公开会话构成与连接完整性

| 场景 | 样本数 | 占比 |
| --- | --- | --- |
| buying | 80 | 40.0% |
| browsing | 80 | 40.0% |
| intent_override | 30 | 15.0% |
| boundary | 10 | 5.0% |

| 难度 | 样本数 | 占比 |
| --- | --- | --- |
| medium | 90 | 45.0% |
| easy | 80 | 40.0% |
| hard | 30 | 15.0% |

共有 200 个不同目标 ASIN，目录连接缺失数为 0。这说明公开集可用于目标商品回查和离线错误分析。

## 方法、限制与稳健性

- 数据源：`data/catalog.jsonl` 与 `data/public_set.jsonl`。
- 所有统计直接读取 JSONL，不对目录内容做修补。隐藏 intent card 由公开评测器逻辑在本地重新生成，不读取任何私有文件。
- 标题重复采用小写字母数字规范化后精确匹配；它只能发现明显重复，不能识别语义近似商品。
- 价格分布只统计 JSON 数值；脚本单独识别可解析与不可解析字符串，不推断币种，也不把极端值自动判定为错误。
- BM25 使用 Starter 的分词方式、字段权重与 OR 查询。排名只计算到设定截断值，未出现的目标只能解释为“未进入 Top N”，不是完整目录中的精确名次。
- 完整约束查询是诊断 oracle，不能作为真实在线表现；所有公开集结论仍可能对800个私有会话过拟合。

## 建议的下一步

1. 根据 drop-one 消融结果选择第一轮字段权重实验，不再仅按字段非空率设权重。
2. 将每个会话的首轮排名、完整约束排名和最终 Agent 结果连接，形成“召回失败 / 重排失败 / 对话效率失败”分类。
3. 从四种场景各抽样目标商品，人工核对“用户表达—商品字段—可抽取槽位”。
4. 建立规范化商品文档与属性层，再用相同查询集复跑本报告，比较 Top 10、Top 100 和 MRR@100。
5. 将 intent card 复现一致性、主键唯一性、目标连接完整性和字段消融基准加入持续测试。

## 仍需回答的问题

- 完整约束后仍在 Top 100 之外的目标，主要失败模式是同义词、类目噪声还是同款竞争？
- 不同 ask_attribute 顺序能够为候选池带来多少信息增益？
- 当前公开集得到的字段贡献是否能在留出子集或未来私有评测中稳定复现？
