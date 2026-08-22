# MemSlides Financial：财务研报一键生成 PPT

将券商研报的 Markdown 或 PDF 转换为结构化、可审计的 PowerPoint。项目在
MemSlides 的生成与局部修订能力之上，增加了财务研报解析、证据约束、原始图表复用、
引用核验、上海交通大学视觉规范和逐页演讲者备注。

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/python-%3E%3D3.10-3776AB?logo=python&logoColor=white">
  <img alt="Node" src="https://img.shields.io/badge/node-20-339933?logo=node.js&logoColor=white">
  <img alt="Tests" src="https://img.shields.io/badge/tests-116%20passed-brightgreen">
  <img alt="License" src="https://img.shields.io/badge/license-Apache--2.0-green">
</p>

## 项目定位

这个分支的核心入口是：

```text
研报 Markdown / PDF
        ↓
文档解析与证据整理
        ↓
大纲、图表、数值与演讲稿生成
        ↓
引用核验与交大品牌处理
        ↓
HTML + PPTX + 运行回执
```

与普通的“把全文摘要成几页幻灯片”不同，财务工作流会检查章节证据、图表来源、
视觉预算、页面标题、引用编号、品牌元素和演讲者备注。缺少强制交付物时，流程会失败，
不会把不完整的 PPTX 当作成功结果返回。

## 核心能力

- **一条命令完成全流程**：研究、大纲、可视化、引用、HTML、PPTX 和备注统一编排。
- **研报证据约束**：页面只能使用所属章节的证据，自动修复跨章节引用。
- **原始图表优先**：按 PDF 页面顺序复用研报图表，并限制每页最多两个视觉位置。
- **标题去重**：章节名称与页面结论分离，避免连续页面重复同一个小标题。
- **交大视觉规范**：封面和结尾页使用内置 16:9 背景，内容页显示完整交大 Logo。
- **引用可追溯**：Markdown 模式核对正文引用与 PDF 附录，生成引用标记和附录页。
- **逐页演讲稿**：生成与幻灯片对齐的 speaker manuscript，并写入 PowerPoint 备注。
- **断点恢复**：用输入哈希和阶段状态安全执行 `--resume` 或 `--overwrite`。
- **可审计交付**：输出运行清单、生成回执、引用验证报告和最终合规回执。

## 快速开始

以下命令以 Windows PowerShell 为主。Linux 和 macOS 可使用等价的 Python、npm 和
Playwright 命令。

### 1. 安装环境

需要 Python 3.10 及以上版本和 Node.js 20。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[research]"
npm ci
.\.venv\Scripts\python.exe -m playwright install chromium
```

如果只是更新已有环境，可以重新执行 `pip install -e ".[research]"` 和 `npm ci`。

### 2. 配置服务凭据

```powershell
$env:DEEPSEEK_API_KEY = "你的 DeepSeek API Key"
$env:MINERU_API_TOKEN = "你的 MinerU Token"
```

不要把真实密钥写入 README、Git 提交或公共配置文件。也可以通过 `.env` 或私有 YAML
配置；详见 [DeepSeek 配置](docs/deepseek.md)。

### 3. 运行一份 human PDF 研报

```powershell
.\.venv\Scripts\memslides.exe financial-report `
  "data\reports\human\000333_2025-08-31_human.pdf" `
  --output-dir ".memslides\rerun-000333-20250831-human" `
  --max-attempts 3 `
  --generation-timeout 7200
```

这条命令适合直接测试 PDF 解析和 PPT 生成。当前直接 PDF 模式不会执行引用 sidecar；
如果需要正文引用核验和引用附录，请使用下一节的 Markdown + 同名 PDF 模式。

### 4. 生成带完整引用核验的 PPT

Markdown 与 PDF 必须是同一份研报，并放在同一目录、使用相同文件名主干：

```powershell
.\.venv\Scripts\memslides.exe financial-report `
  "data\reports\agent\600989_2025-10-11.md" `
  --output-dir ".memslides\rerun-600989-20251011" `
  --max-attempts 3 `
  --generation-timeout 7200
```

程序会自动发现：

```text
data/reports/agent/600989_2025-10-11.md
data/reports/agent/600989_2025-10-11.pdf
```

如果存在 `600989_2025-10-11_parsed.json`，程序会优先复用；不存在时会自动生成。

## 输入模式

| 输入方式 | 适用场景 | 引用处理 |
| --- | --- | --- |
| Markdown + 同名 PDF | 推荐的完整生产流程 | 核对正文引用、PDF 来源目录并生成引用附录 |
| 直接 PDF | 测试 human PDF 或只有 PDF 的研报 | 当前跳过引用 sidecar，其余研究和生成阶段照常运行 |
| Markdown + `--pdf` | Markdown 与 PDF 文件名不同 | 使用显式指定的匹配 PDF |
| Markdown + `--parsed-json` | 已有独立的解析 JSON | 使用显式指定的块级解析结果 |

Markdown 和 PDF 如果不是同一份研报，引用编号与来源目录会不一致，流程可能失败或排除
无法验证的引用。

## 常用命令参数

| 参数 | 说明 |
| --- | --- |
| `--output-dir` | 必填；本次运行的独立输出目录 |
| `--max-attempts 3` | 大纲生成和修复的最大尝试次数 |
| `--generation-timeout 7200` | Deck 生成超时秒数；长研报建议使用 7200 |
| `--max-tokens N` | 初始输出 token 预算；确认截断后程序也会自动提高预算 |
| `--resume` | 输入未变化时，从现有 `run_manifest.json` 继续 |
| `--overwrite` | 清理该输出目录的阶段产物并重新运行 |
| `--instruction "..."` | 给本次 PPT 增加额外生成要求 |
| `--model` | 覆盖研究阶段使用的模型 |
| `--api-provider` | 指定 `deepseek`、`siliconflow` 或自动判断 |
| `--citation-model` | 覆盖引用匹配模型 |

`--resume` 与 `--overwrite` 不能同时使用。

## 输出目录

一次成功运行通常包含：

```text
<output-dir>/
├─ research/
│  ├─ slide_outline.json
│  ├─ numeric_audit.json
│  ├─ speaker_manuscript.json
│  └─ visualizations/
├─ citations/
│  ├─ citation_source_catalog.json
│  ├─ citation_units.json
│  └─ citation_validation_report.json
├─ deck/
│  ├─ outputs/                  # 最终 HTML 页面
│  ├─ generation_receipt.json
│  └─ *.pptx                    # 最终 PowerPoint
├─ run_manifest.json            # 各阶段状态与输入哈希
└─ final_receipt.json           # 最终合规检查
```

直接 PDF 模式会跳过引用阶段，因此 `citations/` 中不一定存在完整引用产物。最终 PPTX 的
实际路径也会打印在命令成功返回的 JSON 中。

## 质量与合规规则

### 大纲和证据

- 内容页必须绑定研报章节和证据块。
- 自动删除超出所属章节的 evidence refs 和 visual evidence。
- 每张内容页最多使用两个视觉位置。
- 图表页按原始 PDF 图表标题和页面顺序处理。
- 页面顺序会恢复为原始 DocumentBundle 的章节顺序。

### 页面设计

- 封面不提前展示“总营收”“净利润”等正文指标。
- 封面和结尾页只保留一套交大背景，自动移除冲突背景。
- 蓝色封面的可见文字统一为白色。
- 内容页右上角使用完整的上海交通大学 Logo。
- 页面标题与最终大纲同步，同一章节内重复标题会改为该页核心结论。
- HTML 使用高密度渲染倍率导出，减少截图进入 PPT 后的模糊问题。

### 引用和备注

- Markdown 模式只保留同时存在于正文解析结果和 PDF 来源目录中的引用 ID。
- 缺失 ID 会记录在验证报告和最终回执中，不会由模型虚构。
- 引用编号按照 PDF 附录顺序全局编号。
- 演讲稿必须与页面对齐并成功写入 PowerPoint speaker notes。

## 恢复或重新运行

上次运行因 API、网络或 Deck 生成中断时：

```powershell
.\.venv\Scripts\memslides.exe financial-report `
  "data\reports\human\000333_2025-08-31_human.pdf" `
  --output-dir ".memslides\rerun-000333-20250831-human" `
  --resume `
  --generation-timeout 7200
```

如果代码或输入文件已经变化，重新生成：

```powershell
.\.venv\Scripts\memslides.exe financial-report `
  "data\reports\human\000333_2025-08-31_human.pdf" `
  --output-dir ".memslides\rerun-000333-20250831-human" `
  --overwrite `
  --max-attempts 3 `
  --generation-timeout 7200
```

建议每份研报使用独立输出目录，避免不同输入共用运行清单。

## 测试研报

仓库中的样例分为两类：

```text
data/reports/agent/    # Markdown、PDF 和部分 parsed JSON 配套数据
data/reports/human/    # 人工版本 PDF，以及部分 Markdown 配套数据
```

当前样例覆盖 000333、001309、002444、002544、002821、600989 和 603993，可用于验证
PDF 解析、Markdown 引用、长研报、原始图表和不同页面结构。

## 常见问题

### `Output already contains a run`

输出目录已经存在。希望继续时使用 `--resume`；希望重新生成时使用 `--overwrite`。

### `Inputs changed since the saved run`

输入文件的哈希与运行清单不同，不能安全续跑。确认输出目录无误后使用 `--overwrite`。

### `Generated outline failed validation`

程序会优先自动修复跨章节证据、重复标题和视觉预算。仍然失败时，可提高
`--max-attempts`，检查模型输出是否被截断，并在长研报上设置更高的 `--max-tokens`。

### 模型或生成阶段超时

长研报建议使用：

```text
--max-attempts 3 --generation-timeout 7200
```

中断后优先尝试 `--resume`，避免重复已经完成的研究阶段。

### `fitz API is deprecated` 警告

这是 PyMuPDF 的兼容性警告，不是本次生成失败的直接原因。真正的失败原因通常出现在后续
`ERROR:` 行。

### 最终 PPT 在哪里

查看命令最后输出的 JSON，或者在 `<output-dir>/deck/` 中查找 `.pptx`。不要到
`.memslides` 根目录混合查找不同运行的结果。

## 运行测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

当前基线为：

```text
116 passed
```

测试覆盖一键工作流、引用标准化、网络重试、章节证据、图表配对、交大品牌、标题同步、
封面处理和 PowerPoint speaker notes。

## 高级与旧版用法

正常使用应优先选择 `memslides financial-report`。需要单独调试某个阶段时，请参考：

- [财务集成与阶段产物契约](docs/financial-integration.md)
- [安装说明](docs/INSTALL.md)
- [DeepSeek 模型与配置](docs/deepseek.md)

底层通用 MemSlides 命令仍然保留：

```powershell
.\.venv\Scripts\python.exe -m memslides generate `
  --instruction "Create a one-slide project summary" `
  --num-pages 1

.\.venv\Scripts\python.exe -m memslides revise `
  --workspace ".memslides\session" `
  --feedback "Tighten the title"

.\.venv\Scripts\python.exe -m memslides template induct `
  --template-file "template.pptx"
```

财务研报命令使用项目内置的交大视觉资源，不接受外部 `--template` 参数；通用生成路径仍可
使用模板相关功能。

## 配置、安全与隐私

- 真实 API Key 只放在环境变量、`.env` 或私有 YAML 中。
- 不要提交 `.env`、`.memslides/`、`.venv/`、生成工作区或私人配置。
- 公共默认配置位于 `src/memslides/memslides.yaml`。
- 可以通过 `MEMSLIDES_CONFIG_FILE` 或全局 `--config` 指定私人 YAML。
- 外部 URL、下载资源和生成的财务结论应在正式展示前人工复核。

## 项目基础与致谢

本项目基于 MemSlides：一个面向个性化演示文稿生成、多轮工作记忆、工具记忆和局部修订的
智能体框架。

- [原始论文](https://arxiv.org/abs/2606.17162)
- [原始项目主页](https://memslides.github.io/)
- [MemSlides 网站](https://memslides.com/)

如果使用了 MemSlides 的研究框架，请引用：

```bibtex
@misc{jin2026memslides,
  title={MemSlides: A Hierarchical Memory Driven Agent Framework for Personalized Slide Generation with Multi-turn Local Revision},
  author={Ye Jin and Yangyang Xu and Jun Zhu and Yibo Yang},
  year={2026},
  eprint={2606.17162},
  archivePrefix={arXiv},
  primaryClass={cs.CL},
  doi={10.48550/arXiv.2606.17162},
  url={https://arxiv.org/abs/2606.17162},
}
```

## License

见 [LICENSE](LICENSE) 和 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
