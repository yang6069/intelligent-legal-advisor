# 智法通 · 法律顾问智能体平台

> 企业级智能法律顾问平台 ｜ GraphRAG 三路混合检索 + Cross-Encoder 精排 + Self-RAG 自评估 ｜ 多 Agent 对抗式合同审查
>
> **本地化大模型**（Ollama + Qwen）｜ **全容器化部署**（Docker Compose 五服务一键拉起）

---

## 项目定位

**智法通** 是一套面向企业与律所的智能法律顾问平台，以本地大模型为推理底座，构建两大核心能力：

### 1. 智能法律问答（Legal RAG）

完整的检索增强生成链路，回答**引用真实法条**、标注**置信度**，支持 **SSE 逐字流式输出**：

```
查询预处理（指代消解/重写/子查询分解）
  → 三路混合召回（稠密向量 + BM25 + Neo4j 知识图谱，并发）
  → RRF 融合（k=60）
  → Cross-Encoder 精排（bge-reranker-v2-m3，Top-5）
  → LLM 流式生成（引用编号 + SSE）
  → Self-RAG 自评估（法条准确性/逻辑完整性/事实一致性三维置信度）
  → 引用校验（回答法条 ↔ 检索结果比对，幻觉防护 + 效力状态标注）
```

### 2. 多 Agent 对抗式合同审查（StateGraph 编排）

自研 LangGraph 兼容语义的状态图引擎，编排**对抗制衡**的多角色审查流水线：

```
条款拆分 → 法务/合规/业务三视角独立推理 → 主审风险识别 → 复核遗漏检查
        → 反方辩论（主审必须逐条回应） → 仲裁兜底 → 风险评分 → 修改建议
```

- **对抗制衡**：三个独立 Agent 从不同立场同步审查，避免单一视角偏见与遗漏
- **有结论**：未达成共识时由仲裁 Agent 终局裁定
- **真实性**：每个 Agent 节点记录 LLM 真实调用标记与耗时，LLM 失败时按规则数据兜底，三方意见永不为空
- **可交付**：输出结构化审查报告 + **带修改痕迹的 AI 修订版合同**（新增/修改/删除字符级 diff 高亮）

---

## 技术栈

| 层次 | 技术选型 |
| --- | --- |
| **前端** | Vue 3.4 + TypeScript + Vite 5 · Element Plus 2.7 + ECharts 5.5 · Pinia 2 · Nginx（SSE 透传） |
| **后端** | Python 3.10 + FastAPI（异步，双 worker）· 自研 StateGraph 多 Agent 引擎 |
| **数据层** | MySQL 8.0 · Redis 7 · ChromaDB（HNSW 向量库）· Neo4j 5（GraphRAG 图谱） |
| **AI 层** | Ollama + qwen2.5 · bge-large-zh-v1.5（1024 维嵌入）· bge-reranker-v2-m3（精排） |
| **部署** | Docker Compose 五服务编排（健康检查 + 数据卷持久化） |

---

## 快速开始

```bash
# 0. 前置：宿主机安装 Ollama 并拉取模型
ollama pull qwen2.5:1.5b
# 嵌入/精排模型（bge-large-zh-v1.5 / bge-reranker-v2-m3）部署方式见 docs/

# 1. 一键拉起五服务（MySQL / Redis / Neo4j / 后端 / 前端）
docker compose up -d --build

# 2. 访问
#    前端  http://localhost:8188
#    后端  http://localhost:28000/docs (FastAPI Swagger)
```

---

## 更多文档

- [智法通项目简洁介绍文档.md](./智法通项目简洁介绍文档.md) —— 系统架构、核心技术细节、功能模块、关键功能截图、权限与数据安全设计

---

> 本项目为本地可完整跑通的技术演示 Demo，未接入生产环境；演示数据均为虚构。
