<p align="right"><a href="README.en.md">English</a> | <b>简体中文</b></p>

# Paper Review System · 论文审稿系统

> 一套投稿前的**两档审稿体系**：免费、高频的**轻量级自检**，加上付费的**多模型评审委员会**——6 个不同厂商的大模型扮演 6 个真正独立的审稿人。

平时用轻量级自检兜底日常、抓格式 / 数值 / 排版错；投稿前关键节点再上多模型终审，做深度学术评审、交叉验证、打分分诊。

---

## 为什么是「两档」

不是所有检查都该上最贵的工具。**高频低成本的自检兜底日常，关键节点再上昂贵的多模型终审**——这是成本纪律，也是这个项目的核心设计。

| | 轻量级自检 | 多模型终审 |
|---|---|---|
| 执行 | 人 / Agent 直接读 | 6 个外部 LLM 并行 |
| 成本 | 免费 | ~5–7 万 tokens / 轮 |
| 频率 | 每改完都跑 | 投稿前终审 |
| 抓什么 | 格式 + 结构 + **数值一致** + 视觉排版 | 深度学术评审 + 交叉验证 + 打分分诊 |
| 适用 | 毕设 / EI 通常够 | CCF / SCI 投稿前 |

完整判断逻辑见 [`docs/methodology.md`](docs/methodology.md)。

---

## 档一 · 轻量级自检（免费）

双维度并行——两套眼睛抓不同的错：

- **维度 A · 文本内容**（读源文件）：结构完整性、摘要↔正文一致、**数值交叉验证（最高优先级）**、图表审计、符号公式一致、参考文献、语言、逻辑链、配图质量。
- **维度 B · 视觉排版**（PDF → 逐页 PNG，多模态"看"）：页眉、目录页码、空白页、图被裁切、表格跨页、公式渲染、留白……**源码对 ≠ 成品对**，排版错只有用眼睛看成品才发现。

```bash
python doc_to_pages.py path/to/paper.pdf --dpi 200
# 也支持 .docx；输出逐页 PNG，再用多模态模型 / 人逐页过视觉清单
```

灵魂原则：**诊断看全局，治疗要精确**——先全页截图建完整问题清单，再一次只改一处、改完立即重编译验证。紧耦合系统（如 LaTeX 浮动体）**绝不批量改**。

---

## 档二 · 多模型终审（付费）

**核心信念：交叉模型共识 ≫ 同模型重复。** 不同厂商 = 不同训练数据 = 真正独立的视角。当来自完全不同来源的模型都指出同一个问题，它大概率是真的；只有一个模型提的，可能是它的幻觉。

> 💡 **怎么一次性拿到这么多不同厂商的模型？** 审稿委员会要的恰恰是"多个不同厂商"，用**聚合 API** 最省事——一个 key 就能调 Claude / GPT / Gemini / Grok / DeepSeek 等所有主流模型，天然适配本工作流。本项目默认的 `vectorengine` 就是这样一个聚合站，模型覆盖广、更新快：注册入口 → <https://api.vectorengine.ai/register?aff=jyFY>

评审委员会（默认配置，可在 `config.json` 改）：

| 角色 | 默认模型家族 | 职责 |
|------|------|------|
| **EIC** 主编 | Anthropic | 整体质量、原创性、venue 匹配 |
| **Methodology** 方法学 | OpenAI | 实验设计、统计、可复现、数据泄漏 |
| **Domain** 领域 | Google | 文献、理论、增量贡献 |
| **Writing** 写作 | xAI | 清晰度、结构、图表 |
| **Devil** 唱反调 | DeepSeek | 反驳、逻辑漏洞、过度声明（故意压分） |
| **Meta** 仲裁 | OpenAI reasoning | 综合 5 份评审、裁决分歧、分诊 |

**聚合逻辑**：5 个审稿人各从 6 个维度（Originality / Soundness / Significance / Clarity / Reproducibility / Overall，1–10）打分 → 每维取**平均**（并报 Min/Max 分歧区间）；Meta **不打分**，只把 5 人的意见**并集去重 + 分诊**成 `MUST FIX / NICE TO HAVE / ALREADY ADDRESSED / REVIEWER ERROR`。

**三 API 渐进降级**：每个审稿人配 `fallback_chain`，主模型限流 / 不可用时自动降到备份 API 或次级模型，绝不直接跳到弱模型。

**Venue 校准**：`--venue top|mid|regional` 让审稿人按目标会议 / 期刊档次校准评分与分诊标准。

### 快速开始

```bash
# 1. 配置 API key（你的 key 永远留在本地，config.json 已被 .gitignore）
cp config.example.json config.json
#    编辑 config.json 填入真实 key

# 2. 跑审稿
python paper_reviewer.py path/to/main.tex --config config.json --venue regional

# 可选：多轮迭代 / 只跑部分审稿人
python paper_reviewer.py main.tex --config config.json --venue mid --rounds 3
python paper_reviewer.py main.tex --config config.json --reviewers EIC Writing Devil
```

产出 `*_review.md`（人读：分数汇总 + 共识 / 分歧 + 修订路线图）和 `*_review.json`（机读）。

---

## 怎么读分数（别只看总分）

1. **绝对校准**：8 强 / 7 扎实可录 / 6 刚过线 / ≤5 线下。
2. **按 venue 加权**：顶会重 Originality + Significance；四区 / 工程刊重 Soundness + Clarity + Reproducibility。同一份分，不同 venue 好坏可相反。
3. **看「形状」而非总分**：高执行 + 中原创 = 干净的应用型论文；Min–Max 区间 = 分歧大小。

> 口诀：**总分定生死线，形状定论文性格，venue 定形状好不好。**

---

## 闭环用法

```
写完初稿 → 轻量级自检循环 2–3 轮 → (EI 到此结束)
        → (CCF/SCI) 多模型终审 → 看形状 + triage → 按 must-fix 回炉 → 再验 → 投稿
```

## 安全

- **真实 API key 只存本地 `config.json`，已被 `.gitignore` 排除，绝不进仓库。**
- 仓库内只含脱敏的 `config.example.json`（占位符）。
- 脚本本身不内嵌任何 key，只从 `config.json` 读取。

## 依赖

- Python 3.9+；`requests`（调 API）。
- 视觉审查：`doc_to_pages.py` 需要 `pdftoppm`（poppler）或对 `.docx` 的转换支持。

## License

MIT © 2026 疏锦行

---

## 科研辅导 · 合作

需要**科研辅导、科研合作**，欢迎联系 **疏锦行**　微信：**shujinxing777**
