# PodLore · 第二期开发实施指令（差异化：多 Agent + 系列关联 + 热度 + 标注反哺）

> 用途：粘贴给 Trae 的实现指令（第二期 M9-M13）——**第一期（M0-M8）验收通过后再执行本指令**
> 配套文档：先读《PodLore_PRD_v1.0.md》（产品定义）+《PodLore_第一期开发实施指令.md》（第一期已完成的工程）
> 总目标：在已跑通的第一期上，补齐差异化能力——多 Agent 协作（LangGraph）、系列关联分析、热度分析、标注数据反哺
> 总验收：3 个 Agent 任务跑通（系列研究/观点对比/知识卡片）；系列关联可视化可用；金句评测闭环（用户划线 vs AI 金句）
> 约束：后端 Python 3.10+；Agent 编排用 LangGraph（检索底层仍手写）；前端延续第一期 ins 设计语言；代码注释中文

---

## 项目上下文（给 Trae 的背景，只需了解）

- **第一期已交付**：导入→转写→加工→精编成书→阅读/标注→语义搜索；已有 `books`/`book_paras`/`annotations` 表、向量索引、FastAPI 路由、React 前端
- **本期新增能力**：
  1. **多 Agent**：系列研究/观点对比/知识卡片（LangGraph 5 角色：编排/检索/分析/写作/质检）
  2. **系列关联**：系列识别 → 实体提取 → 关系网络 → 关联路径（脉络导览/主题索引）
  3. **热度分析**：真实热度数据（playCount/clapCount 已在 episodes 表）
  4. **标注反哺**：用户划线=金句真值（评测 AI 金句）、ASR 纠错统计
- **关键技术决策**：Agent 编排用 LangGraph（多 Agent 状态流/checkpoint 需要框架），检索底层仍手写（第一期向量检索复用）；不对外提供 MCP（C 端产品）
- **数据说明**：`episodes` 表已有 `play_count/clap_count/favorite_count/comment_count/series_name`（series_name 第一期为 NULL，本期填充）

---

## M9：系列识别与关联分析

**目标**：把零散的单集组织成「系列」，提取实体，构建系列内节目关系网络。

### M9.1 系列识别（填充 series_name）

**文件**：`backend/app/services/series_service.py`

- 函数 `infer_series(title: str) -> str | None`：从标题提取系列名
  - 规则 1：`EP{n} {系列名}｜{副标题}` → 系列名 = `｜` 前部分去掉 EP 编号
  - 规则 2：`{系列名} Vol.{n}` / `{系列名} 第{n}期` 等常见模式
  - 规则 3：兜底——同一 podcast 下，多集标题共享的前缀（如「Mini MBA」）
- 函数 `backfill_series()`：扫全部 episodes，填充 series_name
- 函数 `list_series() -> list[dict]`：系列列表（含集数、覆盖时间）

### M9.2 实体提取（人物/公司/产品/概念）

**文件**：`backend/app/services/entity_service.py`

- 函数 `extract_entities(episode_id)`：LLM 从转写稿提取实体 → 存 `episode_entities` 表
  - 输出 JSON：`[{name, type(人物/公司/产品/概念), mentions_count, first_ts}]`
  - **JSON mode + Pydantic 校验**（复用第一期 LLM 封装）
  - 只对「已成书」的单集提取（转写质量已人工确认过）

### M9.3 关系网络构建

**文件**：`backend/app/services/relation_service.py`

- 函数 `build_relations()`：同系列内，两集共享实体（或共享主题词）→ 生成关联边
  - 输出：`[{episode_a, episode_b, shared_entities[], strength}]`
  - 强度 = 共享实体数 × 提及频次加权
- 函数 `get_series_graph(series_name) -> dict`：返回图结构 `{nodes: [{id, title, heat}], edges: [{source, target, label}]}`——供前端可视化

### 数据模型（M9 新增）

```sql
CREATE TABLE IF NOT EXISTS episode_entities (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  episode_id INTEGER, name TEXT, type TEXT, mentions_count INTEGER, first_ts REAL
);
CREATE TABLE IF NOT EXISTS series_relations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  series_name TEXT, episode_a_id INTEGER, episode_b_id INTEGER,
  shared_entities TEXT, strength REAL
);
```

### 测试要求

- 系列识别：用真实标题样本（EP85 Mini MBA｜…）断言提取正确
- 实体提取：LLM mock，断言 JSON 结构正确
- 关系构建：3 集 mock 数据，断言共享实体边生成

### M9 验收清单

- [ ] 现有单集 series_name 全部回填
- [ ] 已成书的单集实体提取完成
- [ ] 系列内关系网络可生成（数据层）

---

## M10：多 Agent 协作（LangGraph 5 角色）

**目标**：用户输入任务 → 多 Agent 完成——3 个任务：系列研究 / 观点对比 / 知识卡片。

### M10.1 LangGraph 编排框架

**文件**：`backend/app/agent/graph.py`

- 依赖新增：`langgraph>=0.2`
- **5 角色节点**（StateGraph）：
  ```
  orchestrator（编排）→ 解析任务类型，分发
  → researcher（检索）：调第一期向量搜索 + 实体库，收集相关片段
  → analyst（分析）：聚合/对比观点，识别主题异同
  → writer（写作）：生成报告/对比/知识卡片
  → critic（质检）：核对引用真实性（片段是否真实存在于知识库）、观点忠实度
  ```
- State 设计：`{task, task_type, collected_fragments[], analysis, draft, critique, final}`
- **条件分支**：task_type 决定路径（研究=全链路；对比=跳过 writer 前先 analyst 对比；卡片=精简链路）
- **checkpoint 启用**：任务可恢复（中断后可从 checkpoint 继续）

### M10.2 三个任务实现

**文件**：`backend/app/agent/tasks/`

- `task_research.py`：系列研究——「研究『Mini MBA』系列讲了哪些营销理论」
  - researcher 检索系列内各集 → analyst 聚合主题 → writer 生成研究报告（结构化）→ critic 核对引用
- `task_compare.py`：观点对比——「各集关于『定价』分别怎么说的」
  - researcher 跨集检索 → analyst 按集组织观点 + 对比异同 → writer 输出对比表 → critic 核对
- `task_card.py`：知识卡片——「把 EP85 关于『护城河』的观点做成卡片」
  - researcher 定位片段 → writer 生成卡片（观点/出处/时间戳）→ critic 核对 → 存 `knowledge_cards` 表

### 数据模型（M10 新增）

```sql
CREATE TABLE IF NOT EXISTS knowledge_cards (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT, content TEXT, source_book_id INTEGER,
  source_para_ids TEXT,  -- JSON 数组
  created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS agent_tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_type TEXT, input_text TEXT, status TEXT,  -- running/done/failed
  result_path TEXT, created_at TEXT
);
```

### M10.3 API 与前端

- `POST /api/agent/tasks`：提交任务（异步执行，BackgroundTasks）
- `GET /api/agent/tasks/{id}`：查状态/结果
- `GET /api/agent/tasks`：历史任务列表
- 前端「研究台」页（`src/pages/Research.tsx`）：任务输入 → 状态展示（节点进度）→ 结果渲染（报告/对比表/卡片）
- 设计：延续 ins 风格，任务状态用细线进度指示

### 测试要求

- 图结构：mock 工具，断言 orchestrator→researcher→…→critic 流程走通
- 质检：构造一个「引用不存在」的 case，断言 critic 拦截
- 任务异步：提交立即返回，状态可查

### M10 验收清单

- [ ] 3 个任务全部跑通（真实数据）
- [ ] critic 质检生效（假引用被拦截）
- [ ] 研究台前端可用

---

## M11：热度分析

**目标**：用真实热度数据（已在 episodes 表）做「系列哪集最火」「节目热度分布」。

### 文件：`backend/app/api/heat_api.py` + 前端组件

- `GET /api/heat/series/{series_name}`：系列内各集热度（playCount/clapCount/favoriteCount）→ 排序
- `GET /api/heat/overview`：全部已导入节目的热度对比
- 前端：系列页展示热度（条形/排行，ins 风格）；「最火的一集」高亮
- 数据已在第一期 episodes 表（✅ 已实测抓到），本期只做读取+展示

### M11 验收清单

- [ ] 系列内热度排行正确
- [ ] 前端展示美观

---

## M12：标注数据反哺（数据飞轮闭环）

**目标**：用户行为变成评测数据——划线=金句真值、ASR 纠错统计。

### M12.1 金句评测闭环

**文件**：`backend/scripts/eval_quotes.py`

- 逻辑：对比「AI 金句」（episode_quotes 表）vs「用户划线」（annotations 表，按书对回原文）
- 指标：**金句命中率** = 用户划线的段落中，AI 金句覆盖的比例（阈值可调）
- 输出：评测报告（哪些集 AI 金句好、哪些差——差的可反哺提示词）
- **这是第一期「用户行为=真值」设想的落地**：不需要人工标注，用户行为就是评测集

### M12.2 ASR 纠错统计

**文件**：`backend/scripts/eval_asr_errors.py`

- 第一期精编时用户改错字 → 需要记录改动（**第一期 M4 补充**：编辑时记录 `original_text` vs `edited_text` 差异）
- 统计：高频错误词、错误率趋势 → 输出报告（可反哺 FunASR 术语表）

### M12.3 第一期补充（回补）

- M4 编辑接口补充：编辑记录表
  ```sql
  CREATE TABLE IF NOT EXISTS edit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_id INTEGER, para_id INTEGER,
    original_text TEXT, edited_text TEXT,
    created_at TEXT DEFAULT (datetime('now'))
  );
  ```
- 若第一期已完成，本期回补此表 + 前端编辑时写入

### M12 验收清单

- [ ] 金句评测闭环跑通（AI 金句 vs 用户划线对比报告）
- [ ] ASR 纠错统计输出（有真实纠错数据）

---

## M13：集成与验收

### 13.1 全流程集成

- 第二期功能与第一期无缝衔接：系列页可从书架/节目进入；研究台可从首页入口进入
- 语义搜索升级：可搜知识卡片（knowledge_cards 进向量索引）

### 13.2 端到端验收（你本人操作）

- 3 个 Agent 任务用真实数据跑通，结果质量可接受
- 系列关联可视化可用（至少一个真实系列有图）
- 热度分析正确
- 金句评测闭环出报告

### 13.3 文档完善

- README 更新：第二期功能 + 截图（研究台/系列图）
- 评测报告归档（金句命中率、搜索 Recall@K）

### 13.4 面试展示准备

- 准备 3 张关键截图：首页知识宇宙 / 阅读器（边听边读标注）/ 研究台（Agent 任务结果）
- 准备 1 个演示脚本：从粘贴链接到搜索观点，全流程 3 分钟内讲完

### M13 验收清单

- [ ] 两期功能完整集成
- [ ] 3 个 Agent 任务 + 系列关联 + 热度 + 反哺全部验收
- [ ] README + 截图 + 演示脚本就绪

---

## 第二期交付方式

1. 每个 M 完成后：Trae 输出「差异清单 + 测试结果 + 验收清单勾选」
2. 你验收通过后进入下一 M
3. 卡住时：Trae 输出「问题 + 可选方案 + 建议」，你决策后继续

## 第二期执行决策记录

（此表由 Trae 在每次差异确认时填写：日期 / 决策 / 原因）
