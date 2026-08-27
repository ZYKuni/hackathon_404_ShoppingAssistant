# Shopping Copilot 当前工作进度

> 更新时间：2026-08-27
>
> 当前分支：`feature/ykzhao0826_01`
> 本文用于帮助团队成员快速理解已完成内容、设计约定和下一步接口。

## 总览

目前完成了两个基础步骤：

1. **第一步：补全可运行的 `agent.py` 基线**——建立离线、多轮、可评测的 BM25 Agent。
2. **第二步：冻结结构化对话状态协议**——定义 State、StatePatch、共享词典、合并规则和 30 条黄金案例。

第二步当前是独立基础层，**尚未接入 `agent.py` 的线上响应路径**。这是有意的：先验证状态语义，再替换现有简单消息列表，避免破坏第一步已跑通的评测基线。

---

## 第一步：可运行的 Agent 基线

主要文件：`starter/agent.py`

当前实现包括：

- SQLite FTS5 + BM25 商品检索；
- title、categories、features、details、store、description 分字段加权；
- Porter stemming；
- 每个 session 独立的多轮消息记忆；
- 主动询问 material、feature、color、style、size、use_case、budget、brand；
- 忽略“没有偏好”等无效检索文本；
- 识别简单的意图覆盖；
- 当前消息、累计消息和类别锚点的 RRF 融合；
- 空查询热门商品 fallback；
- 完全离线，无 LLM、无 API Key、token 成本为 0。

当前本地 `results.json` 记录的公开集结果：

| 指标 | 当前结果 |
|---|---:|
| Hit Rate@10 | 0.840000 |
| MRR | 0.476401 |
| MTTC | 4.885 |
| Technical Score | 0.685220 |

以上是公开 200 会话上的开发结果，不代表隐藏 800 会话的最终成绩。

---

## 第二步：结构化对话状态协议

### 目标

第二步解决的问题不是“怎样自动理解用户”，而是先定义：

> 给定上一轮 State 和本轮 StatePatch，什么才是唯一、正确、可测试的新 State。

后续无论采用正则、LLM 还是小模型解析用户消息，都必须生成同一种 StatePatch，并通过相同的黄金案例。

### 新增文件

| 文件 | 作用 |
|---|---|
| `starter/conversation_state.py` | State、Patch、操作类型和确定性合并逻辑 |
| `starter/attribute_lexicons.py` | 对话端和商品清洗端共用的标准值及别名 |
| `starter/state_patch_cases.jsonl` | 30 条人工标注的黄金状态更新案例 |
| `tests/test_conversation_state.py` | 黄金案例和边界语义的自动化测试 |
| `starter/PROJECT_PROGRESS.md` | 当前工作说明与团队交接文档 |

### ConversationState v0.1.0

状态包含：

```json
{
  "schema_version": "0.1.0",
  "category": "running_shoes",
  "hard_constraints": {
    "audience": ["women"],
    "price_max": 120
  },
  "soft_preferences": {
    "feature": ["lightweight"]
  },
  "excluded": {
    "color": ["white"]
  },
  "no_preference": [],
  "asked_attributes": [],
  "turn": 1
}
```

当前允许的内部字段：

```text
category
audience
price_min
price_max
color
material
brand
size
style
use_case
feature
```

其中：

- 单值字段：category、price_min、price_max；
- 多值字段：audience、color、material、brand、size、style、use_case、feature；
- category、audience、price、size 默认是硬约束；
- color、material、brand、style、use_case、feature 默认是软偏好；
- 用户语言中的 `must`、`only` 等强表达可以覆盖默认强度。

### StatePatch

Patch 只描述本轮发生的变化，不直接重写整个 State。

支持的操作：

| 操作 | 语义 |
|---|---|
| `set` | 设置或覆盖字段 |
| `add` | 向多值字段追加可接受值 |
| `replace` | 明确替换字段全部旧值 |
| `remove` | 移除一个正向值 |
| `clear` | 清空字段，但不声明用户无偏好 |
| `exclude` | 添加明确排除值 |
| `allow` | 撤销一个排除值，但不创建正向偏好 |
| `set_no_preference` | 清空正向偏好并记录无需继续追问 |
| `reset_scope` | 清空整个 session 的商品级条件 |

示例：

```json
{
  "source_turn": 2,
  "operations": [
    {
      "op": "replace",
      "field": "price_max",
      "value": 150,
      "strength": "hard"
    },
    {
      "op": "exclude",
      "field": "color",
      "value": "white"
    }
  ]
}
```

### 已冻结的合并语义

1. **最新明确单值覆盖旧值**：预算 100 改成 150 后只保留 150。
2. **`add` 累积多值偏好**：black 后补充 blue，结果为 `[black, blue]`。
3. **`replace` 完整替换字段**：blue instead of black 后只保留 blue。
4. **硬约束优先**：同一值从 soft 升级为 hard 时，从 soft 中移除。
5. **否定独立存储**：`not white` 保存为 `excluded.color=[white]`。
6. **排除覆盖正向冲突**：先喜欢 white、后来明确不要 white，最终只保留排除。
7. **unknown 不等于 no preference**：后者表示已确认无需继续询问。
8. **无偏好可与排除共存**：任意颜色都可以，但不能是白色。
9. **类别变化自动清空商品级条件**：防止鞋的尺码、材质污染夹克意图。
10. **同义类别不触发重置**：`road running` 与 `running shoes` 都归一为 `running_shoes`。
11. **冲突预算采用最新值**：原有最低 100，后来要求最高 80，则清除旧最低价。
12. **Patch 合并不修改输入 State**：`apply_patch` 返回新状态，便于回放和调试。

### 共享词典

`attribute_lexicons.py` 是对话解析与商品清洗的唯一共享词典来源。目前包含：

- audience 别名：womens、women's、ladies → women；
- color 别名：grey → gray，multi/multicoloured → multicolor；
- material 别名：poly → polyester，faux leather 与 leather 分开；
- category 别名：road running → running_shoes，trainers → sneakers；
- use_case 别名：walk → walking，workout → gym；
- feature 别名：water proof → waterproof，light weight → lightweight；
- 内部字段到官方 `ask_attribute` 的映射。

未知开放词汇不会被静默删除，而是经过基础标准化后保留。当前词典是 MVP v0.1，不是完整 Amazon 类目词典。

### 30 条黄金案例

`state_patch_cases.jsonl` 每行包含：

```text
case_id
scenario
previous_state
user_message
人工标注 patch
expected_state
reason
```

覆盖范围：

- 单轮明确购买；
- 模糊浏览与多轮累积；
- 硬约束和软偏好；
- 颜色、材质等否定条件；
- 单字段覆盖；
- 完整类别切换；
- 无偏好与 Boundary；
- 正向偏好和排除冲突；
- 预算上下界冲突；
- 同义词标准化。

这些案例目前验证的是“Patch 合并是否正确”。自然语言到 Patch 的自动抽取器尚未实现；实现后应新增测试，比较模型/规则输出与案例中的人工 Patch。

---

## 如何运行验证

在仓库根目录运行：

```bash
python3 -m compileall -q starter tests evaluator
python3 -m unittest discover -v
```

完整公开集评测：

```bash
python3 -m evaluator.local_evaluator
```

验收要求：

- 30 条黄金案例全部通过；
- 原 Agent 和 evaluator 测试不能回归；
- `apply_patch` 不修改 previous state；
- 非法字段、非法置信度和非法操作必须抛出异常；
- 50,000 商品和 evaluator 文件不得被修改。

---

## 当前边界与未完成内容

第二步没有完成以下内容：

- 尚无自然语言 → StatePatch 的自动解析器；
- `Agent` 仍使用原有 `SessionState.active_messages`，未切换至 ConversationState；
- 尚未实现商品侧共享字段清洗；
- 尚未将 hard constraints 接入确定性过滤；
- 尚未根据候选不确定性动态选择澄清问题；
- category 词典只覆盖一部分高频类别；
- size 仍是开放文本，没有按鞋、服装、珠宝分别解释。

---

## 建议的第三步

第三步应并行推进两个模块：

1. **Dialogue Parser**：将每轮 `user_message` 转换成 `StatePatch`，先使用规则实现确定性 MVP，再决定是否加入 LLM fallback。
2. **Catalog Normalizer**：商品端导入同一个 `attribute_lexicons.py`，输出 category、audience、price、color、material、brand 等标准值和置信度。

二者完成后再接入 `agent.py`：

```text
user_message
→ StatePatch
→ ConversationState
→ BM25 Top 200
→ hard constraint 三态过滤
→ soft preference 重排序
→ Top 10
```

接入时必须做消融评估，确认结构化状态没有降低 Candidate Recall 和现有 Hit Rate@10。
