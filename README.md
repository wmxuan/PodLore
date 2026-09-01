# PodLore —— 把播客变成你的书

> 把 1 小时播客音频，变成你能读、能改、能标注、能检索的「书」。
> 抓取 → 转写 → AI 加工 → 成书 → 标注 → 语义搜索 → 首页知识宇宙，全本地链路，数据不出本机。

---

## ✨ 这是什么

PodLore 是一个**把播客变成可阅读、可标注、可语义检索的「书」的本地工具**。

你贴一个小宇宙播客链接，PodLore 帮你：

1. **抓取**音频与元数据（小宇宙页面解析）
2. **本地转写**成带时间码的逐字稿（FunASR SenseVoiceSmall，中文优化）
3. **AI 加工**成分块摘要 / 大纲 / 金句 / 广告标记（DeepSeek LLM，JSON mode）
4. **成书**冻结成稳定的「书」快照（章/段独立三表，编辑可追溯）
5. 在**阅读器**里边听边读边**标注**（划线 / 笔记锚定段落，选中即搜）
6. 跨书**语义搜索**「你读到过什么」（本地 bge 向量 + FTS/LIKE 兜底）
7. 回到**首页知识宇宙**看词云 / 足迹 / 书架 / 数据成就——词云由你的标注驱动

核心理念：**用户行为 = 数据**。你划的线、记的笔记会反哺首页词云，让「读过的内容」沉淀成可检索的知识——数据飞轮。

---

## 🖼 功能截图

> 截图位于 `docs/screenshots/`，端到端验收时由用户实拍（见 [docs/M8_端到端验收指引.md](./docs/M8_端到端验收指引.md)）。

### 1 · 首页知识宇宙（M7）

![首页知识宇宙](./docs/screenshots/home.png)

四模块全部数据驱动：词云（jieba 切词 + 标注加权，笔记×2 / 划线×1 / 摘要×1）、30 天足迹热力图（4 阶莫兰迪色阶）、最近书架画廊、数据成就真实计数。点词云 chip 直达 `/search?q=词` 语义搜索。

### 2 · 阅读器：边听边读边标注（M5）

![阅读器](./docs/screenshots/reader.png)

播放器与逐字稿同步滚动，划线 / 笔记锚定 `book_para_id + offset`，选中段落可一键语义搜索。标注列表按书聚合。

### 3 · 语义搜索页（M6）

![语义搜索](./docs/screenshots/search.png)

跨书语义召回，结果按来源（书 / 章 / 段）+ 命中引擎染色（vector 绿 / fts 黄 / like 红）展示，附上下文 `context_before / context_after`，可溯源。

> 截图命令（macOS）：启动后用 `cmd + shift + 4` 框选，存为 `home.png` / `reader.png` / `search.png` 放进 `docs/screenshots/`。

---

## 🚀 快速开始

### 一键安装

```bash
git clone <repo> && cd podlore
./install.sh   # 创建 venv + 后端依赖(torch/funasr) + 前端依赖 + FunASR 模型下载 + 生成 .env
```

在 `.env` 填入 `DEEPSEEK_API_KEY`（AI 加工需要，其余链路本地完成）：

```bash
# .env
DEEPSEEK_API_KEY=sk-xxxxx
```

### 一键启动

```bash
./start.sh
# 前端 http://localhost:5173 ｜ 后端 API 文档 http://localhost:8000/docs
```

### 全流程冒烟（一键验证「真的能跑」）

```bash
backend/.venv/bin/python backend/scripts/smoke_pipeline.py
```

一键跑通 抓取(mock) → 转写(mock) → 加工(跳过LLM) → 成书 → 标注 → 搜索 → 首页 四模块，每步输出 ✓/✗ + 耗时，默认用临时库不污染真实数据。期望末行 `RESULT: ✅ ALL GREEN`。

复用真实 episode 跑全链路：

```bash
backend/.venv/bin/python backend/scripts/smoke_pipeline.py --use-existing-eid <真实eid> --skip-fetch
```

---

## 🏗 架构

### 技术栈（第一期）

| 层 | 选型 | 说明 |
|---|---|---|
| 后端 | FastAPI + uvicorn + Pydantic v2 | ASGI，async 全链路 |
| 存储 | SQLite（aiosqlite） | 元数据 / 转写 / 书 / 标注全在本地一个库 |
| 抓取 | httpx + BeautifulSoup | 小宇宙页面解析 + 音频流式下载 + 幂等入库 |
| 转写 | FunASR SenseVoiceSmall + fsmn-vad + ct-punc | 阿里开源，中文优化，本地免费、数据不出本机 |
| 音频解码 | imageio-ffmpeg（静态 ffmpeg 二进制） | Intel Mac 无系统 ffmpeg 也可用 |
| AI 加工 | DeepSeek（OpenAI 协议）+ JSON mode | 大纲 / 金句溯源 / 广告保守 / 分块摘要 |
| 语义搜索 | sentence-transformers + BAAI/bge-small-zh-v1.5 + numpy 余弦 | 本地 embedding（512 维），向量索引 L2 归一化持久化 |
| 关键词兜底 | SQLite FTS(unicode61) + LIKE(CJK 多 token AND) | embedding 不可用时三通道混合 |
| 前端 | Vite + React 19 + react-router-dom 7 + @tanstack/react-query + zod | ins 风 UI，莫兰迪色阶 |
| 日志 | loguru | 成熟高性能 |

### 数据流（第一期 7 个里程碑）

```
[M1 抓取] 小宇宙链接 → 页面解析 → 音频下载 → episodes 幂等入库
    ↓
[M2 转写] 音频 → m4a→wav → FunASR(vad+punc) → transcript_paras(带时间码)
    ↓
[M3 加工] DeepSeek JSON mode → 大纲/金句/广告/分块摘要（金句溯源 para_id）
    ↓
[M4 成书] 冻结快照：books + book_chapters + book_paras（三表独立）+ edits 数组
    ↓
[M5 阅读器] 播放同步 + 划线/笔记(锚定 book_para_id+offset) + 选中搜索 + 标注列表
    ↓
[M6 搜索] bge embedding 惰性加载 + numpy 余弦向量 + FTS/LIKE 兜底 + admin rebuild
    ↓
[M7 首页] /api/home → jieba 词云(标注加权) + 足迹30天 + 书架 + 数据成就
    ↓
[M8 集成] 全流程冒烟 + 跨话题评测 + 文档完善 + 面试展示
```

### 目录结构

```
podlore/
├── backend/
│   ├── app/
│   │   ├── main.py           # FastAPI 入口
│   │   ├── infra/            # 抓取/下载/DB/ASR/embedding
│   │   ├── services/         # 转写/加工/成书业务服务
│   │   ├── api/              # 路由层（home/search/editor/process...）
│   │   └── schemas/          # Pydantic 模型
│   ├── tests/                # 100 个 pytest（85 主链路 + 15 ASR）
│   └── scripts/              # smoke_pipeline / eval_search / download_models ...
├── frontend/src/
│   ├── pages/                # Home / Search / Reader / Editor / Bookshelf
│   ├── lib/api.ts            # 后端 API 客户端
│   └── index.css             # ins 风设计规范（莫兰迪色阶/胶片感/毛玻璃）
├── eval/
│   ├── dataset/             # 语义搜索评测集（40 条 + 跨话题 15 条模板）
│   └── reports/              # 评测报告 JSON
├── data/                     # 运行时数据（SQLite/音频/向量），gitignore
├── docs/                     # 里程碑执行指令 + 验收评审记录 + 截图
├── install.sh / start.sh     # 一键安装 / 一键启动
└── .env.example              # 配置模板
```

---

## 📊 语义搜索评测报告

> 脚本：`backend/scripts/eval_search.py` ｜ 数据集：`eval/dataset/search_queries.jsonl`（40 条）
> 重跑：`backend/.venv/bin/python backend/scripts/eval_search.py --report-json eval/reports/m6_search_eval_report.json`

### 结果（M6 验收）

| 指标 | 值 | 验收线 |
|---|---|---|
| **Recall@10**（full-set-cover query 数 / 总数） | **1.0（40/40）** | ≥ 0.7 ✅ |
| Hit@10（任意 expected 出现比例） | 1.0 | — |
| 平均 expected_book_ids 覆盖率 | 1.0 | — |
| MRR | 1.0 | — |
| 搜索引擎 | hybrid_vector_fts | 三通道混合 |
| embedding | ready=True（bge-small-zh-v1.5，512 维） | — |
| 失败 case | 0 | — |

### ⚠️ Recall 诚实性说明（重要，面试必读）

**Recall@10=1.0 是「虚高」的**——40 条评测针对的 3 本书是**同一 episode 的 v1/v2/v3 三个版本**，文本主体相同，任何主题 query 都会同时召回三本。

这个数字的正确解读：

- ✅ **能证明**：语义搜索链路可用、能跨书召回、评测体系搭好了（功能验收）
- ❌ **不能证明**：语义搜索效果好 / 有跨话题区分度（同内容书，LIKE 关键词也能接近全命中）

**面试讲法**：
> 「语义搜索链路跑通了，40 条评测 Recall@10=1.0——但我如实说明：评测集是三本同话题书（同一集三个版本），这个数字主要验证链路可用，区分度需要导入不同话题的书后补跨话题评测。我不能说语义搜索效果 100%，只能说链路和评测体系搭好了。」

### 跨话题评测（M6 遗留项 / M8 补齐）

已准备跨话题评测模板 `eval/dataset/search_queries_cross_topic.jsonl`（15 条，覆盖商业 / 营销 / 美妆 / 护发 / AI / 出海 / 管理等话题）。

**使用方式**：端到端验收时导入不同话题的书（如纵横四海 + 半拿铁），查到真实 `book_id` 后替换模板里的 `<商业类 book_id>` 占位符，重跑：

```bash
backend/.venv/bin/python backend/scripts/eval_search.py \
  --dataset eval/dataset/search_queries_cross_topic.jsonl \
  --report-json eval/reports/cross_topic_eval_report.json
```

预期：跨话题 query 应只命中对应话题的书（如「雷军 小米 创业 经历」只命中商业书，不命中护发书），以此验证语义搜索的真实区分度。

---

## 🧪 测试

```bash
cd backend && .venv/bin/pytest            # 主链路 85 个
.venv/bin/pytest tests/test_asr_*.py      # ASR/转写 15 个（共 100 passed）
```

覆盖：DB 幂等、抓取解析、转写分段、加工 JSON mode、成书快照、阅读器标注锚定、搜索三通道兜底、首页空态不造假。

---

## ⚠️ 风险与合规

- **仅限本地使用**：转写稿 / 书 / 标注仅限个人本地使用；不公开、不分发、不提供音频下载分发。
- **版权**：尊重播客创作者版权，本工具不存储 / 不分发原始音频之外的公开内容，所有产出限本地阅读。
- **隐私**：数据全本地（ASR / embedding / 检索均本地完成）；仅 AI 加工环节调用 DeepSeek，且只发送文本片段，不出音频、不出库。
- **诚实边界**：AI 生成内容标注来源；不声称官方内容；Recall 指标如实说明局限性（见上）。
- **依赖风险**：小宇宙页面结构变更可能导致抓取失效（抓取层已抽象隔离，失败有提示）；Intel Mac 上 torch 上限 2.2.2（已锁定 transformers 4.47.1 + sentence-transformers 3.3.1 兼容）。

详见 [PodLore_PRD_v1.0.md §9 风险与对策 / §10 合规与伦理](./PodLore_PRD_v1.0.md)。

---

## 📚 文档

- PRD：[PodLore_PRD_v1.0.md](./PodLore_PRD_v1.0.md)
- 文档索引：[docs/index.md](./docs/index.md)
- 里程碑评审记录：[docs/M0~M7_验收评审记录.md](./docs/)
- 端到端验收指引：[docs/M8_端到端验收指引.md](./docs/M8_端到端验收指引.md)
- 面试演示脚本：[docs/面试演示脚本.md](./docs/面试演示脚本.md)

## License（MIT）
