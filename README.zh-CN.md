# BlendHarness

**一个可自我进化的 Blender 多 Agent harness：通过编写 Blender Python 代码，把自然语言描述变成可编辑的 3D 场景。底层是从零实现的 Agent harness。**

[English](README.md)

![生成效果：村庄（测试题，使用学到的技能生成）、厨房一角、吧台凳。每个场景都有 3 个环绕视角和 1 个俯视角度](docs/examples.png)

本项目不直接生成网格，具体分工如下：
- **Coder** Agent 在沙箱化的 headless Blender 里编写并执行 `bpy` 脚本；
- **Critic**（视觉评审）根据渲染图挑出问题；
- **Verifier** 对照修改前后的渲染图，以及场景结构的差异，逐条核实问题是否修好。

通过评审的作品，还可以被提炼成**可自我进化的技能库**，里面是验证过、可复用的建模函数。最终产物是一份普通的 Python 脚本，所以每个物体都可以继续编辑。

项目思路最初来自 [LL3M 论文](https://arxiv.org/abs/2508.08228)。本仓库**不包含任何 LL3M 代码**：harness、沙箱、Agent、记忆和评测都是从零编写，并根据实测数据重新设计的。

## 亮点

- **自研的 Agent harness**：基于工具调用的 Agent 循环，由模型决策、harness 执行。工具出错时，错误作为观察结果交还给模型；设有三道预算闸（步数、同一错误的重复次数、成本）；处理输出截断；"是否完成"由 harness 验证，而不是只听模型自己说。
- **进程级隔离的 Blender 沙箱**：每个任务一个常驻的 headless Blender。每次执行前都恢复成空场景；遇到死循环或崩溃，看门狗会杀掉进程并重启。
- **全链路追踪**：每次 LLM 调用、工具调用和 Agent 运行都是调用树上的一个节点，记录 token、成本、耗时和错误。下文所有数字都来自这些记录。
- **上下文管理**：模型看到的是加工后的版本，完整历史另行保留。过期的脚本换成占位符，当前脚本常驻在上下文末尾；编辑支持批量、要么全部生效要么全部不生效。
- **两级视觉评审**：Critic 回答开放式问题（"有什么问题？"），Verifier 回答封闭式问题（"每个问题修好了吗？有没有改出新问题？"）。能用程序判断的，永远先用程序判断，然后才轮到视觉模型。
- **可自我进化的技能库**：Librarian 从通过评审的作品里提炼可复用的函数。每个候选都要通过四道准入关卡（静态检查、去重、沙箱运行、结构检查加视觉检查），没通过的还有一次修复机会。提示词里只列出技能目录，用到时再按需加载（渐进式披露）。
- **评测框架**：固定的题目集，加上支持断点续跑、单题崩溃隔离和并行的批量运行器。不同版本交错执行，保证对比公平；报告直接从追踪记录里拆出每个修改轮的成本。

## 架构

```mermaid
flowchart TB
    subgraph EVAL[评测]
        RUN[run_eval.py<br/>题目 x 实验条件 x 重复次数] --> REP[report.py]
    end
    subgraph PIPE[编排：pipeline/build.py]
        C0[Coder 建模] --> CR[Critic 评审渲染图]
        CR -->|有问题| C1[Coder 修改]
        C1 --> V[Verifier：修改前后对比 + 场景 diff]
        V -->|只反馈未修好的| C1
        CR -->|通过| L[Librarian 提炼技能]
        V -->|通过| L
    end
    subgraph AG[Agent]
        CODER[coder.py<br/>多步，使用 AgentLoop]
        SINGLE[critic.py / verifier.py / librarian.py<br/>单次调用]
    end
    subgraph TL[工具与记忆]
        BT[BlenderTools<br/>执行 / 编辑 / 查看 / 检查 / 渲染]
        ST[SkillTools<br/>目录 / load_skill / 预置代码]
        MEM[SkillLibrary + 准入关卡]
        SB[BlenderSession <-> blender_server<br/>headless Blender 沙箱]
    end
    subgraph H[Harness]
        LOOP[loop.py：Agent 循环]
        LLM[llm.py：流式客户端、重试、成本]
        CTX[context.py：上下文策略]
        TR[tracing.py：调用树]
    end
    RUN --> PIPE
    PIPE --> AG
    CODER --> BT & ST
    ST --> MEM
    L --> MEM
    BT --> SB
    AG --> H
```

| 层 | 目录 | 职责 |
|---|---|---|
| Harness | `harness/` | 所有 Agent 共用的机制：`AgentLoop`、`LLMClient`（流式、重试、多服务商、成本）、调用树 `Tracer`、构建上下文策略用的通用函数 |
| 工具 | `tools/` | 沙箱：`blender_server.py` 运行在 Blender **内部**，`blender_session.py` 负责驱动它；`BlenderTools`；`SkillTools` |
| 记忆 | `memory/` | `SkillLibrary`（每个技能一个文件夹：`SKILL.md` + `skill.py` + `stats.json`）、准入关卡、学习流程 |
| Agent | `agents/` | 各 Agent 的配置：Coder（提示词 + 工具 + 上下文策略 + 完成检查）、Critic、Verifier、Librarian |
| 编排 | `pipeline/` | `run_build(prompt, BuildConfig)`：建模 → 评审 → 修改 → 核实 → 提炼技能的完整流程 |
| 评测 | `eval/` | 题目集、批量运行器、报告 |

**设计原则：**
- **机制与策略分离**：harness 提供挂钩，Agent 决定策略。
- **框架与配置分离**：只有一个通用的循环引擎，每个 Agent 只是一份配置。
- **依赖倒置**：`BlenderTools` 只认识一个通用的 `preamble_provider` 挂钩，并不知道"技能"的存在。
- **由便宜到贵**：先做确定性的程序检查，再调用视觉模型。
- **故障安全**：评审和关卡拿不准时，一律判为不通过。

## 实验结果

所有数字都来自评测框架。Coder 使用 Claude Sonnet，`reasoning_effort` 设为 `low`；Critic、Verifier 和技能评审使用 Claude Haiku。**每个条件只跑了 2 次**，较小的差异应视为随机波动。

| 实验 | 设置 | 结论 |
|---|---|---|
| `m3_vs_baseline` | 6 道题 × 2 次：完整重写 + 完整历史 vs. 局部编辑 + 历史压缩（v1） | 通过率相同（75%），每题成本降低 13%。但**修改轮反而更贵**（中位数 $0.446 vs $0.274）：压缩把正在编辑的脚本也藏起来了，模型重新读了 17 次；逐处编辑又带来了大量往返。之前单次运行得到的"降低 73%"没有复现。 |
| `m3v2` | 当前脚本常驻上下文 + 批量、全有或全无的编辑；3 道最难的题 × 2 次 | **6/6 通过**（基线 4/6），**每题成本降低 14%**，重新读取脚本 0 次。修改轮的输出 token 里，85%–92% 是看不见的思考，所以剩下的降本空间在思考控制，而不在上下文。 |
| `skills_lc` | 学习曲线：先在 5 道训练题上积累技能，再在 5 道有共用部件、但从没见过的测试题上，对比有和没有技能库两种条件 | 技能库积累到 10 个验证过的技能（修复轮救回了 8 个被拒的候选）。测试通过率：有技能库 6/10，没有技能库 8/10，**但 4 次失败都和技能无关**。**只有 4/10 次运行真正用上了技能**；用上技能的村庄任务 2/2 在第一轮就通过，成本降低 34%。瓶颈在于技能粒度太粗，提炼出的是整件家具，换个题目就用不上。 |

`skills_lc` 训练出的技能库在 [`skills/`](skills/) 目录里：

![技能库：10 个技能，用默认参数渲染](docs/skill_library.png)

下面这些经验影响了设计，每一条都是通过追踪记录或评测发现的：
- **"能运行"不等于"是对的"**：首批提炼的 9 个技能里，有 3 个能正常执行，但其实是坏的（部件陷进地下或互相重叠）。免费的结构检查（落地 + 连通）能把它们拦下。
- **推理模型容易想太多**：默认设置下，模型在第一次调用工具前就花掉了 1.2 万到 3.2 万个思考 token；设为 `reasoning_effort: low` 后降到约 1,700 个。但同一个参数在 Haiku 上反而会**打开**思考，所以思考强度要按 Agent 分别配置。
- **中转站会静默截断流式输出**：有的回复只传回两个 token 就结束了，而且不报错。现在，没有 `finish_reason` 的流一律视为不完整，并自动重试。

## 快速开始

**环境要求：** macOS 或 Linux；[Blender 4.4.3](https://download.blender.org/release/Blender4.4/)；Python 3.12；兼容 OpenAI 协议的 API（实验中，Claude 通过中转站调用，另外也用到了 DeepSeek API）。

```bash
conda create -n blender-agent python=3.12 -y && conda activate blender-agent
pip install -e ".[dev]"        # 以可编辑模式安装 blendharness 包（含 pytest）
cp .env.example .env        # 填写 OPENAI_BASE_URL / OPENAI_API_KEY（如果用 DeepSeek，再填 DEEPSEEK_*）
# Blender 路径默认是 /Applications/Blender.app/Contents/MacOS/Blender，可以用 BLENDER_PATH 覆盖
```

模型按名字前缀路由：`deepseek` 开头的模型使用 `DEEPSEEK_*`，其余都使用 `OPENAI_*`。请在 `config/prices.json` 里填写价格（每百万 token 的美元价格），这样成本和预算才能计算。

```bash
python scripts/smoke_llm.py claude-haiku-4-5     # 测试 API、工具调用和错误记录
python scripts/smoke_blender.py                  # 测试沙箱：场景重置、报错、看门狗、渲染

# 生成一个场景（Coder → Critic → 修改 → Verifier）
python scripts/run_build.py "Create a wooden dining chair with four legs and a slatted backrest" \
    --model claude-sonnet-4-6 --critic-model claude-haiku-4-5 --rounds 2 --effort low

# 运行评测
python eval/run_eval.py --exp my_exp --variants baseline m3 --repeats 2 --workers 3
python eval/report.py --exp my_exp
```

每次运行会生成 `runs/<run_id>/` 目录，包括：
- `trace.jsonl`：调用记录摘要
- `payloads/`：发给模型的完整请求和模型的完整回复
- `renders/`：渲染图
- `final_script.py`：可以独立运行的最终脚本（包含用到的技能代码）
- `result.json`：运行结果
- `blender.log`：Blender 日志

## 目录结构

```
harness/   tracing.py  llm.py  loop.py  context.py
tools/     blender_server.py  blender_session.py  blender_tools.py  skill_tools.py
memory/    skill_library.py  skill_admission.py  learning.py
agents/    coder.py  critic.py  verifier.py  librarian.py
pipeline/  build.py
eval/      run_eval.py  report.py  tasks.json  tasks_skills.json
scripts/   run_build.py  relearn.py  recost.py  smoke_*.py  demo_loop.py  run_coder.py
skills/    学到的技能库（每个技能：SKILL.md + skill.py + stats.json）
config/    prices.json
docs/      roadmap.md  图片
```

## 局限与后续计划

- **样本量小**：每个条件只重复了 2 次，而相同条件下两次运行的结果差异也很大。关键实验应该重复 3–5 次。
- **技能采用率低**：技能粒度太粗，难以迁移到新题目。下一步：提炼可组合的零件（一组腿、一摞抽屉、把手、屋顶），并由 harness 针对每个请求检索、推荐最相关的几个技能。
- **成本**：修改轮的大部分成本来自看不见的思考。下一步：按阶段做模型路由（修改轮使用更便宜的模型或更低的思考强度），以及"先便宜、失败再升级"的级联策略，并与"全部用便宜模型"和"全部用强模型"两种方案对比。
- **健壮性**：阶段级断点续跑，让长任务可以从任意阶段恢复或分叉（已完成设计，尚未实现）。
- **沙箱范围**：目前是进程级隔离，能防住卡死和崩溃，但防不了恶意的文件访问。如果要作为公开服务，需要换成容器隔离。
- **渲染**：颜色略微偏淡；液面上的高光可能让视觉模型误认出不存在的物体。

更多设计说明见 [`docs/roadmap.md`](docs/roadmap.md)。
