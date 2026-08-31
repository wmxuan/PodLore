# PodLore —— 把播客变成你的书

> 把 1 小时播客音频，变成你能读、能改、能标注、能检索的「书」。

## 快速开始

（占位，M8 完善）

```bash
./install.sh   # 一键安装（venv + 后端/前端依赖 + FunASR 模型下载）
./start.sh     # 一键启动（后端 :8000 + 前端 :5173）
```

## 文档

- PRD：[PodLore_PRD_v1.0.md](./PodLore_PRD_v1.0.md)
- 文档索引：[docs/index.md](./docs/index.md)

## 开发

- 后端：`python -m venv .venv && pip install -e ".[dev]" && pytest`
- 前端：`cd frontend && npm install && npm run dev`

## 目录结构

```
podlore/
├── pyproject.toml            # 后端依赖
├── backend/
│   ├── app/
│   │   ├── main.py           # FastAPI 入口
│   │   ├── infra/            # 基础设施（抓取/下载/DB/ASR/LLM）
│   │   ├── services/         # 业务服务（转写/加工/成书）
│   │   ├── api/              # 路由层
│   │   └── schemas/          # Pydantic 模型
│   ├── tests/                # 测试
│   └── scripts/              # 脚本（抓取冒烟/转写冒烟/评测）
├── frontend/                 # Vite + React + TS
├── eval/
│   └── dataset/              # 语义搜索评测集
├── data/                     # 运行时数据（SQLite/音频/向量），gitignore
├── docs/                     # 项目文档索引
├── .env.example              # 配置模板
├── install.sh                # 一键安装
└── start.sh                  # 一键启动
```

## License（MIT）
