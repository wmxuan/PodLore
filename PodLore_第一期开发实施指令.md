# PodLore · 第一期开发实施指令（MVP：导入→转写→成书→阅读→语义搜索）

> 用途：粘贴给 Trae 的实现指令（第一期 M0-M8）
> 配套文档：先读《PodLore_PRD_v1.0.md》（产品定义），再按本指令执行
> 总目标：**跑通「粘贴小宇宙链接 → 转写 → 精编成书 → 边听边读边标注 → 语义搜索」全流程**
> 总验收：5 本真实播客全流程跑通；语义搜索 30 条评测 Recall@K ≥ 0.7；界面符合设计规范（ins 奶油风）
> 约束：后端 Python 3.10+（FastAPI + FunASR + DeepSeek）；前端 Vite + React + TS；本地运行，数据不出本机；代码注释中文；每里程碑可独立验收

---

## 项目上下文（给 Trae 的背景，只需了解）

- **产品**：PodLore——把播客变成你的书。用户粘贴小宇宙分享链接，系统抓取元数据+音频直链（✅ 已实测：页面 `__NEXT_DATA__` 含全部字段），FunASR 转写中文，AI 加工摘要/金句/章节，用户精编后「加入书架」成冻结的书，可边听边读边标注，支持语义搜索
- **已实测数据源**：小宇宙单集页面 `https://www.xiaoyuzhoufm.com/episode/{eid}`，用普通 HTTP GET + UA 即可抓取，无需登录；`__NEXT_DATA__` JSON 内含：title / description / duration / audio_url（m4a 直链）/ cover / shownotes / playCount / clapCount 等
- **关键架构决策**：音频直链自建播放器（不内嵌小宇宙网页）；书为冻结快照（标注锚定段落永不错位）；用户标注/纠错行为 = 评测真值（数据飞轮）
- **设计规范**：ins 奶油风（详见 PRD §4.4）——奶油白底 #F7F4EE、灰蓝强调 #7C8FA6、细无衬线、大留白、大圆角、胶片感封面

---

## M0：项目工程化初始化

**目标**：从空目录到「clone 即可开发」的工程骨架。

### M0.1 仓库目录结构

```
podlore/
├── pyproject.toml            # 后端依赖（M0.2）
├── package.json              # 前端依赖（M0.2b）
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py           # FastAPI 入口（M5 起挂路由）
│   │   ├── infra/            # M1（本指令的文件都放这里）
│   │   ├── services/         # M2-M4（本期建空目录 + __init__.py）
│   │   ├── api/              # M5（本期建空目录 + __init__.py）
│   │   └── schemas/          # M5（本期建空目录 + __init__.py）
│   ├── tests/                # 测试（M1 起填充；含 test_smoke.py 包导入冒烟）
│   └── scripts/              # 脚本（抓取冒烟/转写冒烟/评测）
├── frontend/                 # Vite + React + TS（M0.2b 脚手架；M5 起开发）
├── eval/
│   └── dataset/              # 语义搜索评测集（M6 填充）
├── data/                     # 运行时数据（SQLite/音频/向量），gitignore
│   └── .gitkeep
├── docs/                     # 项目文档索引（链接 PRD）
├── .env.example              # 配置模板（M0.4）
├── .gitignore
├── README.md                 # 骨架（M0.5）
├── install.sh                # 一键安装（venv+前端依赖+FunASR 模型下载）
└── start.sh                  # 一键启动（后端+前端）
```

### M0.2 后端依赖（pyproject.toml）

```toml
[project]
name = "podlore"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = [
  "fastapi>=0.115",
  "uvicorn[standard]>=0.30",
  "pydantic>=2.7",
  "pydantic-settings>=2.3",
  "aiosqlite>=0.20",          # 异步 SQLite
  "httpx>=0.27",              # 抓取页面/音频下载
  "beautifulsoup4>=4.12",
  "loguru>=0.7",
  "openai>=1.40",             # DeepSeek 兼容客户端（OpenAI 协议）
  "funasr>=1.2",              # FunASR 转写框架（模型用 SenseVoiceSmall，见 M2）
  "modelscope>=1.20",         # FunASR 模型下载
  "torch>=2.2", "torchaudio>=2.2",  # FunASR 运行时必需（CPU 版）
  "imageio-ffmpeg>=0.5",      # m4a 解码（静态二进制，仓库自足，不依赖系统 ffmpeg）
  "sentence-transformers>=3.0",  # 本地 embedding（中文）
  "numpy>=1.26",
]
[project.optional-dependencies]
dev = ["pytest>=8.0", "pytest-asyncio>=0.23", "ruff>=0.5"]
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["backend/tests"]
[tool.ruff]
line-length = 100
```

### M0.2b 前端脚手架（Vite + React + TS）

- 用 `npm create vite@latest frontend -- --template react-ts` 创建
- 安装：`react-router-dom`（路由）、`@tanstack/react-query`（数据请求）、`zod`（校验）
- 设计规范基础：全局 CSS 变量（颜色/圆角/间距，按 PRD §4.4 定义）；细无衬线字体栈
- 确认启动：`npm run dev` 可访问默认页

### M0.3 git + gitignore

- `git init`；主分支 main
- `.gitignore` 必须包含：`.env`、`data/`、`backend/.venv/`、`__pycache__/`、`*.pyc`、`frontend/node_modules/`、`frontend/dist/`、`*.log`、`.DS_Store`、`data/audio/`、`data/vectors/`

### M0.4 .env.example（全部配置项 + 中文注释）

```bash
# ===== DeepSeek（必填）=====
DEEPSEEK_API_KEY=             # 摘要/金句/广告标记用
DEEPSEEK_BASE_URL=https://api.deepseek.com

# ===== 转写引擎（本地，轻量方案）=====
# 主模型：SenseVoiceSmall（参数量 2.3 亿；fp32 磁盘约 940MB，int8 运行时内存约 240MB）
# 速度：非自回归，快；自带标点（use_itn=True）——不需要额外标点模型
# 时间戳：SenseVoice 不输出时间戳 → 用 fsmn-vad 分段提供起止时间（详见 M2）
ASR_MODEL=SenseVoiceSmall      # 主转写模型（自带标点）
ASR_VAD_MODEL=fsmn-vad         # VAD 语音活动检测（分段 + 起止时间，时间戳来源）
ASR_USE_ITN=true               # 开启标点/逆文本归一化（自带标点，勿再叠加标点模型）
ASR_DEVICE=cpu                 # cpu / cuda
ASR_BATCH_SIZE=1

# ===== 存储 =====
DATA_DIR=./data

# ===== 语义搜索 =====
EMBEDDING_MODEL=bge-small-zh-v1.5   # 本地中文 embedding
```

### M0.5 README.md 骨架

```
# PodLore —— 把播客变成你的书

## 快速开始
（占位，M8 完善）

## 文档
- PRD：链接到工作目录《PodLore_PRD_v1.0.md》

## 开发
- 后端：python -m venv .venv && pip install -e ".[dev]" && pytest
- 前端：cd frontend && npm install && npm run dev

## 目录结构
（M0.1 的树）

## License（MIT）
```

### M0.6 验证

- `git init` 成功，目录结构齐全
- 后端 `pytest` 跑通（含 test_smoke.py 包导入测试）
- 前端 `npm run dev` 可访问
- `install.sh` 可执行（venv + 依赖 + 前端 + **3 个模型下载**：SenseVoiceSmall + fsmn-vad + ct-punc，均有进度提示）

### M0 验收清单
- [ ] 目录结构齐全
- [ ] 后端 pytest 绿
- [ ] 前端可启动
- [ ] install.sh 一键安装成功

---

## M1：数据抓取层（小宇宙链接解析）

**目标**：粘贴小宇宙分享链接 → 抓取页面 → 解析出全部元数据 + 音频直链 → 入库。

### 项目上下文（本里程碑特有）

小宇宙单集页面是 Next.js 渲染，`__NEXT_DATA__` script 内含 `props.pageProps.episode`，字段（已实测）：
- `eid`（单集 id）、`pid`（节目 id）、`title`、`description`
- `duration`（秒）、`enclosure.url`（音频直链 m4a）、`media.size`
- `shownotes`（HTML 字符串）
- `podcast.title`、`podcast.author`、`podcast.brief`
- `playCount`、`clapCount`、`favoriteCount`、`commentCount`（热度数据，第二期用）
- `cover`（节目封面，在 podcast 字段或页面 og:image）
- `pubDate`

### 文件 1：`backend/app/infra/fetch_xyz.py`（小宇宙抓取）

- 函数 `parse_episode_url(url: str) -> dict`：提取 eid（URL 中 `/episode/{eid}`），返回元数据字典
- 函数 `fetch_episode_page(eid: str) -> str`：GET 页面（UA 伪装成浏览器，超时 15s）
- 函数 `extract_next_data(html: str) -> dict`：正则提取 `__NEXT_DATA__` JSON 并解析
- 函数 `extract_episode_meta(next_data: dict) -> dict`：从 `pageProps.episode` 提取上表字段，**归一化字段名**（snake_case），返回结构化字典
- 封面提取：优先 `episode.podcast.cover`，兜底页面 `og:image` meta

### 文件 2：`backend/app/infra/downloader.py`（音频下载）

- 函数 `download_audio(url: str, dest: Path, progress_cb=None)`：流式下载（httpx stream），支持断点续传（Range），写 `data/audio/{eid}.m4a`
- 函数 `validate_audio(path: Path) -> bool`：文件存在且大小 > 1MB

### 文件 3：`backend/app/infra/db.py`（数据库 + 建表 + CRUD）

建表 DDL（初始化时执行）：

```sql
CREATE TABLE IF NOT EXISTS podcasts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  pid TEXT UNIQUE, title TEXT, author TEXT, brief TEXT, cover_url TEXT
);
CREATE TABLE IF NOT EXISTS episodes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  pid TEXT, eid TEXT UNIQUE, title TEXT, description TEXT,
  duration INTEGER, pub_date TEXT,
  audio_url TEXT, audio_path TEXT, cover_url TEXT,
  shownotes_html TEXT,
  play_count INTEGER, clap_count INTEGER, favorite_count INTEGER, comment_count INTEGER,
  series_name TEXT,              -- 从标题提取（M9 完善）
  transcript_status TEXT DEFAULT 'pending',  -- pending/processing/done/failed
  created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS transcript_paras (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  episode_id INTEGER, seq INTEGER, text TEXT, start_ts REAL, end_ts REAL
);
```

CRUD（全部 async，用 aiosqlite）：
- `upsert_episode(meta: dict) -> int`：按 eid 幂等插入/更新
- `get_episode(eid: str) -> dict | None`
- `list_episodes() -> list[dict]`
- `update_transcript_status(eid: str, status: str)`

### 测试要求（pytest）

- `test_parse_url.py`：URL 提取 eid（含 query 参数、hash）
- `test_extract_next_data.py`：用抓取的真实页面 HTML 样本（存入 `backend/tests/fixtures/`），断言解析出 title/audio_url/duration/shownotes
- `test_downloader.py`：mock 小文件下载成功、URL 无效抛错

### M1 验收清单

- [ ] 粘贴真实小宇宙链接，能解析出全部元数据（实测验证过 EP85）
- [ ] 音频直链能下载到 `data/audio/`
- [ ] episodes 表正确入库，幂等
- [ ] pytest 全绿

---

## M2：转写层（FunASR · SenseVoiceSmall + VAD 时间戳）

**目标**：音频 → 中文转写文本 → **带时间戳分段** → 入库 `transcript_paras`。
**⚠️ 本里程碑关键技术点（已按 Trae 实测修正，勿再引入标点模型）**：
1. **SenseVoiceSmall 自带标点**（`use_itn=True` 时）——**不需要 ct-punc**！实测叠加会出「。。」「，，」双重标点。方案只有 2 个模型：SenseVoiceSmall + fsmn-vad
2. **时间戳来自 VAD**：SenseVoice 不输出时间戳 → 用 fsmn-vad 的段起止时间作为每段时间戳（模型无关、精确）
3. **尺寸口径**：SenseVoiceSmall 参数量 2.3 亿，fp32 磁盘约 940MB，**int8 运行时内存约 240MB**——验收按「运行时内存」口径

### 转写流水线（核心，先理解再写码）

```
原始音频（可能 4 小时）
  ↓ ① m4a 解码：imageio-ffmpeg（静态二进制）转成 wav/pcm（funasr 输入格式）
  ↓ ② VAD 分段：fsmn-vad 检测语音活动 → 切成若干语音段，每段带 [start, end] 起止时间
  ↓ ③ 逐段转写：每段用 SenseVoiceSmall（use_itn=True）识别 → 得到带标点文本
  ↓ ④ 组装：每段 = {text(带标点), start, end} → 时间戳来自 VAD 段的起止时间
  ↓ ⑤ 段落化：segment_to_paras 合并成阅读友好的 50-200 字段落（保留首段时间戳）
```

**为什么这样设计**：VAD 段的时间戳是「模型无关」的（来自音频能量检测，精确可靠）——用它作为每段文本的起止时间，就绕开了 SenseVoiceSmall 不输出时间戳的限制。阅读器同步播放时，用段落的 start 匹配播放器 currentTime。

### 文件 1：`backend/app/infra/asr.py`（FunASR 封装）

- 函数 `init_asr()`：加载 **2 个模型**（SenseVoiceSmall + fsmn-vad），device 从配置读，**惰性加载**（首次调用才加载，避免启动慢）
- 函数 `transcribe(audio_path: Path) -> list[dict]`：按上述流水线执行，返回分段列表 `[{text, start, end}]`（时间戳秒，来自 VAD）
  - **use_itn=True 必须开启**（自带标点）；**不要接 ct-punc**（双重标点实测问题）
- 函数 `segment_to_paras(segments, max_chars=200) -> list[dict]`：把 ASR 分段合并/切分为「阅读友好的段落」（每段 50-200 字，按句号/时间间隔切；合并时**段落 start 取首段 start，end 取末段 end**）
- **m4a 解码**：音频可能是 m4a（✅ 实测小宇宙直链是 m4a）——用 imageio-ffmpeg 转 wav 16k 单声道（funasr 标准输入），解码失败记 log 并置 failed
- **长音频处理**：VAD 一次处理整段可能内存不足 → 按 10 分钟切片，逐片 VAD+转写，再拼接（切片边界注意：VAD 跨切片处可能切句，可接受，后续段落化会合并）
- 错误处理：单段转写失败不中断整集（记 log 跳过），整集失败置 failed

### 文件 2：`backend/app/services/transcribe_service.py`（转写任务）

- 函数 `start_transcribe(eid: str)`：更新 status=processing → 下载音频（若缺）→ 转写 → 分段入库 → status=done；失败置 failed 并记录错误
- 函数 `get_transcript(eid: str) -> list[dict]`：读 transcript_paras
- **异步执行**：转写耗时（4h 音频，SenseVoiceSmall CPU 下估计 30-60 分钟，Intel Mac 更慢——**验收时用短音频片段**），用 FastAPI BackgroundTasks 或线程池，**不阻塞请求**；进度可查询（处理中置 status，前端轮询；可加「已处理 X 分钟音频」进度字段）

### 测试要求

- mock 一段 10 秒音频，转写返回分段且时间戳递增（若本地无模型，用 `@pytest.mark.skipif` 跳过，仅测 segment_to_paras 的纯逻辑——**重点测：合并后的段落 start/end 取首末段，保证时间戳单调递增**）
- **真实验证（关键）**：用 5-10 分钟真实播客片段跑通，人工核对：① 转写文字可读、带标点（无双重标点）② 时间戳与音频对齐（播放到某段高亮位置正确）③ m4a 解码正常
- **验证 Intel Mac 性能**：记录转写耗时（分钟音频/秒耗时），评估 4h 完整集可行性

### M2 验收清单

- [ ] 真实音频转写成功（SenseVoiceSmall + use_itn），分段合理（每段可读、**无双重标点**）
- [ ] **每段带时间戳，且时间戳与音频对齐**（播放同步测试通过）
- [ ] m4a 解码正常（imageio-ffmpeg 链路）
- [ ] 转写状态流转正确（pending→processing→done/failed）
- [ ] 长音频异步不阻塞（请求立即返回，状态可查）
- [ ] 运行时内存口径 < 600MB（SenseVoiceSmall int8 约 240MB + VAD 约 290MB + 余量）

---

## M3：AI 加工层（摘要/大纲/金句/章节/广告标记）

**目标**：转写完成后，LLM 生成摘要/大纲/金句（带时间戳）/章节，并标记广告段落。

### 文件 1：`backend/app/infra/llm.py`（DeepSeek 封装）

- 函数 `chat(messages, json_schema=None, temperature=0.3) -> str | dict`：DeepSeek 调用，JSON mode 时用 Pydantic 校验（复用竞品 Agent 的 JSON mode 经验）
- 模型默认 `deepseek-chat`；temperature 按任务区分（摘要 0.3、金句 0.3、广告识别 0.1）

### 文件 2：`backend/app/services/process_service.py`（加工任务）

按顺序执行，全部用**分段输入**（长文本分块，避免超上下文）：
- `generate_summary(episode_id)`：输入转写全文（分块摘要→合并），输出 300 字内「人话摘要」
- `generate_outline(episode_id)`：输出结构化大纲（章节标题 + 起止时间戳），**按大纲切分章节存入**（新增表 `book_chapters` 雏形或 transcript 层章节标记）
- `generate_quotes(episode_id)`：提取 3-8 条金句，每条 `{text, start_ts, end_ts, reason}`——**金句必须能从转写原文中找到（时间戳对应）**
- `mark_ads(episode_id)`：识别广告段落（赞助口播/推广），返回 `[{para_id, is_ad, reason}]`——标注在段落级

### 数据模型补充（M3 新增）

```sql
CREATE TABLE IF NOT EXISTS episode_quotes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  episode_id INTEGER, text TEXT, start_ts REAL, end_ts REAL, reason TEXT
);
CREATE TABLE IF NOT EXISTS episode_outline (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  episode_id INTEGER, seq INTEGER, title TEXT, start_ts REAL, end_ts REAL
);
```

### 测试要求

- LLM mock（`openai` 客户端注入 mock）：摘要长度合规、金句时间戳对应原文、广告标记返回段落级结果
- 真实验证：用 1 集真实转写跑通（人工检查摘要/金句质量）

### M3 验收清单

- [ ] 摘要/大纲/金句/广告标记 4 项全部生成
- [ ] 金句可溯源（时间戳能找到原文）
- [ ] 长文本分段处理不超上下文

---

## M4：精编与成书（编辑器 + AI 建议 + 冻结快照）

**目标**：用户编辑转写稿（AI 广告建议 + 手动改写/纠错）→ 「加入书架」→ 生成冻结的书。

### 文件 1：`backend/app/services/book_service.py`（成书核心）

- `create_book(episode_id, edits=None) -> dict`：生成书的冻结快照——从 transcript_paras（应用用户编辑后）生成 `books` + `book_chapters` + `book_paras`（**全新表，书的正文与转写稿分离**）
- 书封面：复用 `episodes.cover_url`
- `get_book(book_id) -> dict`（含章节+段落全文）
- `list_books() -> list[dict]`（书架列表：封面/标题/章节数/创建时间）

### 数据模型（M4 新增，核心——书是冻结快照）

```sql
CREATE TABLE IF NOT EXISTS books (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  episode_id INTEGER, title TEXT, cover_url TEXT,
  created_at TEXT DEFAULT (datetime('now')), version INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS book_chapters (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  book_id INTEGER, seq INTEGER, title TEXT
);
CREATE TABLE IF NOT EXISTS book_paras (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  book_id INTEGER, chapter_id INTEGER, seq INTEGER, text TEXT
);
```

### 文件 2：`backend/app/api/editor_api.py`（编辑接口，M4 起有 API 层）

- `GET /api/episodes/{eid}/transcript`：转写稿 + 广告标记 + 金句（编辑页数据）
- `POST /api/episodes/{eid}/book`：创建书（body 可选 `edits`：段落替换/删除映射）→ 返回 book_id
- `GET /api/books`：书架列表

### 编辑模型（关键设计）

```
前端编辑页把「段落列表」发给用户编辑：
- 删除段落：前端标记 deleted，提交时过滤
- 改写段落：前端改 text，提交时替换
- 广告段落：AI 已标记，一键「删除/保留」
后端 create_book 按编辑映射生成 book_paras（原样/替换/跳过）
```

### 测试要求

- 创建书：无编辑（原稿入架）与有编辑（删除 2 段 + 改写 1 段）两种，断言 book_paras 正确
- 幂等：同一集重复建书 → 新 book_id（版本+1），不覆盖旧书

### M4 验收清单

- [ ] 「加入书架」一键可用（默认原稿）
- [ ] 编辑后成书内容正确（删除/改写生效）
- [ ] 书是冻结快照（后续任何转写/加工变化不影响已建书）
- [ ] 封面复用成功

---

## M5：阅读器（播放器同步 + 划线/笔记 + 选中搜索）

**目标**：书的阅读体验——自建播放器边听边读、划线/笔记、选中搜索、标注列表。

### 文件 1：`backend/app/api/reader_api.py`

- `GET /api/books/{id}`：书全文（章节+段落，含每段 start_ts 供播放同步）
- `POST /api/books/{id}/annotations`：创建标注 `{book_para_id, offset_start, offset_end, color, note_text}`
- `GET /api/annotations`：我的全部标注（按书聚合）
- `DELETE /api/annotations/{id}`：删除标注
- `GET /api/search`（第一期版）：语义搜索（M6 实现，M5 先占位返回「建设中」）

### 数据模型（M5 新增）

```sql
CREATE TABLE IF NOT EXISTS annotations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  book_id INTEGER, book_para_id INTEGER,
  offset_start INTEGER, offset_end INTEGER,
  color TEXT DEFAULT 'blue', note_text TEXT,
  created_at TEXT DEFAULT (datetime('now'))
);
```

### 前端（M5 主体工作，frontend/）

- **路由**：`/`（首页，M7 做，先占位）`/books`（书架）`/books/:id`（阅读器）`/annotations`（标注列表）
- **阅读器组件**（`src/pages/Reader.tsx`）：
  - 章节导航（左侧或顶部，ins 细线风格）
  - 段落渲染（正文细无衬线、行距 1.7）
  - **播放器**：HTML5 Audio（audio_url 直链）+ 播放时高亮当前段落（用段落 start_ts 匹配 currentTime）——点击段落可跳转播放
  - **划线**：选中文本 → 浮动操作条「划线/笔记/搜索」→ 划线变色（灰蓝）、笔记弹输入框
  - **选中搜索**：点「搜索」→ 调 `/api/search?q=选中文本` → 结果面板（第一期内部知识库）→ 选中结果 → 「添加到笔记」（note_text 附带结果）
- **标注列表页**：按书分组展示，点标注回跳阅读器对应段落（锚点定位）
- **设计规范落地**：奶油白底、灰蓝强调、大圆角、细线（PRD §4.4 的 CSS 变量实现）

### 测试要求

- 标注 CRUD 全通（pytest）
- 前端：手工验证——播放高亮、划线笔记、选中搜索三个交互在真实书数据上可用

### M5 验收清单

- [ ] 播放器播放时段落高亮同步
- [ ] 划线 + 笔记 + 标注列表完整可用
- [ ] 选中搜索返回结果并可附加到笔记
- [ ] 阅读器视觉符合 ins 设计规范

---

## M6：语义搜索（embedding + 向量检索 + FTS 兜底 + 评测集）

**目标**：跨书语义搜索「我听到/读到过什么」——输入问题召回相关片段，带来源。

### 文件 1：`backend/app/infra/embedding.py`

- 函数 `init_embedder()`：加载本地中文 embedding（bge-small-zh-v1.5），惰性加载
- 函数 `embed(texts: list[str]) -> list[list[float]]`：批量向量化

### 文件 2：`backend/app/infra/vector_store.py`

- 数据量评估：几十本书 × 每本几百段 = 万级以内 → **不用向量数据库，本地 numpy + 余弦相似度**（复用竞品 Agent 的经验：万级 JSON/npy 够用）
- 函数 `build_index()`：把 book_paras 全部向量化，存 `data/vectors/`（npy + 元数据 json）
- 函数 `search(query, top_k=10) -> list[dict]`：query 向量化 → 余弦相似度 top_k → 返回 `{book_id, book_title, chapter, para_text, score}`
- **增量更新**：新书加入后 reindex（简单全量重建，几十本书秒级）

### 文件 3：`backend/app/api/search_api.py`

- `GET /api/search?q=...&top_k=10`：向量检索为主，**SQLite FTS5 兜底**（向量召回不足时用关键词，防漏）；结果按分数排序，返回带上下文（前后段落）

### 文件 4：评测集与评测脚本

- `eval/dataset/search_queries.jsonl`：30-50 条 `{query, expected_book_ids, expected_keywords}`——**自己标注**（基于真实转写的书，你听过的内容）
- `backend/scripts/eval_search.py`：跑评测 → Recall@K / MRR → 输出报告
- 验收线：**Recall@K ≥ 0.7**

### 测试要求

- search 返回结果含来源字段（book/title/chapter/para）
- 评测脚本可运行，输出指标

### M6 验收清单

- [ ] 语义搜索跨书召回（搜「护城河」能找到多本书相关片段）
- [ ] FTS 兜底生效（向量漏时关键词能补）
- [ ] 评测集 30+ 条，Recall@K ≥ 0.7

---

## M7：首页「知识宇宙」（词云 + 足迹 + 书架画廊 + 数据成就）

**目标**：首页四模块，ins 网格布局，数据驱动。

### 后端：`backend/app/api/home_api.py`

- `GET /api/home`：聚合首页数据
  - `word_cloud`：从标注 + 笔记 + 摘要提取高频词（简单实现：jieba 分词 + 词频，标注/笔记加权）→ `[{word, weight}]`
  - `footprint`：按天聚合沉淀量（听了几集/标了几条/写了几条笔记）→ `[{date, count}]`（近 30 天）
  - `books_recent`：最近 5 本书
  - `stats`：沉淀总数（书/标注/笔记/主题数）

### 前端：`src/pages/Home.tsx`

- **词云**：点击词 → 跳 `/search?q=词`（直达语义搜索）；低饱和色块 + 大圆角（不硬朗矩形）
- **足迹热力图**：30 天网格，莫兰迪色阶（浅灰→雾蓝→灰蓝→陶土橘）
- **书架画廊**：最近的书封面墙（胶片感滤镜）
- **数据成就**：数字展示（大号 Light 字体）
- ins 网格 3 列布局，大留白

### M7 验收清单

- [ ] 四模块全部数据驱动（非假数据）
- [ ] 词云点击直达语义搜索
- [ ] 视觉符合 ins 设计规范

---

## M8：集成与验收

### 8.1 全流程冒烟脚本 `backend/scripts/smoke_pipeline.py`

一键跑通：真实链接 → 抓取 → 转写（可跳过，用已有转写数据）→ 加工 → 成书 → 标注 → 搜索，输出每步状态。

### 8.2 端到端验收（你本人操作）

- 用 5 本真实播客（如纵横四海/半拿铁）跑全流程
- 阅读器：边听边读边标注，划线/笔记/选中搜索可用
- 语义搜索：你听过的内容能搜到（Recall@K ≥ 0.7）
- 首页四模块展示你的真实数据

### 8.3 设计验收

- [ ] 无 SaaS 模板感
- [ ] 全站统一 ins 设计语言
- [ ] 截图展示美观（首页/阅读器/搜索页三张图）

### 8.4 文档完善

- README：快速开始（install.sh + start.sh）、功能截图、架构说明、评测报告
- 风险声明：本地使用/版权声明（参考 PRD §9/§10）

### M8 验收清单

- [ ] 5 本书全流程跑通
- [ ] 语义搜索 Recall@K ≥ 0.7
- [ ] README 完整（含截图 + 评测报告）
- [ ] 设计验收通过

---

## 第一期交付方式

1. 每个 M 完成后：Trae 输出「差异清单 + 测试结果 + 验收清单勾选」
2. 你验收通过后进入下一 M
3. 卡住时：Trae 输出「问题 + 可选方案 + 建议」，你决策后继续

## 第一期执行决策记录

（此表由 Trae 在每次差异确认时填写：日期 / 决策 / 原因）
