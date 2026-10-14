---
name: world-rpg-producer
description: Research a novel, anime, film, TV series, mixed continuity, or original world and produce a source-grounded playable text RPG package, including an AI game Skill, chat starter, saves, and SillyTavern cards/worldbook. Use when asked to make or adapt a world into an AI roleplaying game; ordinary character chat and continuing an existing campaign belong to the generated game's runtime.
---

# 跨媒介世界 RPG 制作器

把作品与玩家偏好制作成可游玩、可续存的游戏包。此 Skill 的工作是研究、设计、编译和验证；完成后由生成的游戏主持 Skill 带玩家游玩。人物蒸馏内置，不依赖 CSP 或任何指定平台。

v1.0.1 保持 game/session schema 1.0；符合新增校验约束的旧存档可保留原游戏数据续玩。时点、保留 NPC ID 和自定义 memories 的兼容例外见 runtime.md。修改 game.json 会改变绑定 hash，需新局或显式迁移，保留旧数据与存档。

## 接收请求

识别作品、媒介、版本／改编、开局时间线、剧透上限、玩家身份、语言和玩法偏好。只有作品版本或资料边界的歧义会实质改变结果时，集中问一两项；已经明确的信息不要重复确认。

其他信息可合理默认并在制作记录中写明：单人文字 RPG、原创玩家角色、探索与交涉为主、适度风险、允许自由行动、允许偏离原作、支持文本存档。用户指定的恋爱、战斗、经营、推理等侧重点优先；未指定时不预设恋爱关系，不强套 D&D。

用户指定的研究深度、完整世界范围和输出规模优先；不要为了省时默改为轻量版或只做开局。可以分阶段完成，但局部交付不能声称完成全范围研究。

区分两个范围：研究资料的固定上限 `cutoff_tick`，以及玩家当前剧情时点 `state.time.tick`。按章节／集数／时间阶段建立同一整数轴，不能把聊天回合当剧情时间。原作剧情默认提供世界压力和可发生事件；除非用户要求复现，不把原作结局设成必须到达的终点。

进入研究前，在制作记录确认作品版本、资料边界、玩家体验与交付范围；未指定项写明默认值。剧情可以越过资料上限作为本局原创继续，来源资料仍按固定上限过滤。

## 按需阅读

- 开始制作时读 [data-contract.md](references/data-contract.md)，以 `game.json` 为唯一权威输入。
- 涉及作品资料时读 [research.md](references/research.md)，处理来源、版本、冲突及证据。
- 将资料改编成游戏时读 [game-design.md](references/game-design.md)。
- 导出前读 [exporting.md](references/exporting.md)，了解各平台格式和真实能力差异。
- 设计主持／存档与试玩时读 [runtime.md](references/runtime.md)。
- 需要了解项目借鉴及许可时读 [project-notes.md](references/project-notes.md)。
- [example-game.json](assets/example-game.json) 是完整原创演示，可用来理解数据结构；不要把其设定混入用户作品。

## 1. 获取与核验资料

优先使用宿主可用的联网搜索、网页读取和用户提供材料。先检索候选，再获取可访问全文／相关章节、保存来源定位并阅读；搜索摘要和 URL 清单不等于读过原文。脚本 `research.py` 是发现、抓取和导入材料的辅助，不替代语义判断。

按媒介选择证据：小说章节和叙述视角；影视剧对白、场景及集数／时间戳；动漫分集或漫画版本；混合改编分别研究。可用官方、原作、可靠参考与社区资料，但标明身份、版本、同源转载关系和可访问状态。

将重要世界规律、事件、人物动机和能力整理为细粒度 claims。每条记录分类、支持来源及定位、冲突和可用时点。`canon`、`interpretation`、`inference`、`design` 不混写；本局已发生事实另存 state／事件记录，不回写成原作。

只有两份独立来源实际支持同一断言、版本及语义一致时才标 `verified`；满足来源计数只是必要条件。直接原文事实可标 `single_source`，不要为了数量复制同源资料。冲突核心事实先解决或缩小范围；无法核实时保持草稿并说明缺口，不编造来源或“已核验”。

原创世界允许空 sources，所有原创 claims 标 `design`／`not_applicable`。无法联网时先用已有材料继续；范围不足则交付明确标注的材料受限草稿。不要声称完成跨站研究。

进入设计的条件是：关键规律与人物已有可定位的证据、冲突处理结果和使用标签；未覆盖范围记为缺口。保存该记录，后续只因范围变化或新证据重查对应部分。

## 2. 制作游戏

将已核验资料映射为地点、势力、NPC、世界规则和分时点 lore；再以 design claims 补充游戏机制。保留出处定位与选择该版本的理由。混合版本必须有白名单和冲突选择规则，不能仅写 `mixed`。

按 [game-design.md](references/game-design.md) 建立短而完整的可玩循环：可感知开局、自由行动、明确风险、行动判定、持续后果、可实现目标和至少一种继续游玩／结束条件。玩法只实现用户需要的部分。

为 NPC 蒸馏目标、情境反应、说话习惯、关系与知识边界。秘密单独标 `gm` 并标记知情 NPC；未来事实保留 `available_from`。NPC 只知道其获知的内容，不能因为主持读过资料就全知。生成的主持规则必须保留玩家自己的对白、思想和选择权。

把实际采用的数值资源、背包、NPC 状态、任务状态和本局分支写入 state。复杂规则若无可靠执行器，只能声明为主持裁定／文本记录；不得把自然语言承诺称为已实现的模拟系统。

需要程序约束的关键检定用 mechanics.checks 明示前置条件与允许后果；本局承诺、误解和关键经历用 remember 记录知情范围。具体接口见 [runtime.md](references/runtime.md)。进入验证前，逐项确认目标可达、失败可继续、时间成本与初态一致。

用 `gamekit.py init` 创建草稿是可选步骤；可以直接编写完整 JSON。脚本路径以此 Skill 目录为基准，工作产物写入当前任务约定的输出目录，保留可复核的研究记录。

```text
python scripts/gamekit.py init --out game.json --title "游戏标题" --work "作品或世界" --medium novel --continuity "明确版本" --cutoff "章节或时间范围"
python scripts/gamekit.py validate game.json --strict
```

## 3. 验证并导出

先做语义审查：来源是否支持断言；是否串版本／提前剧透；NPC 是否越权知情；开局是否有实际可执行的行动；成功、失败及放弃是否改变状态；是否替玩家决定情感或行动。

再运行严格数据验证，修复引用、范围、状态和数值问题。`--strict` 通过表示结构及发布门槛通过，不证明人物符合原作，也不证明长期游玩稳定。

首次接入或修改工具后，可运行 `python scripts/selftest.py` 做离线回归；它在临时目录检查来源门槛、视角过滤、存档、幂等结算和完整导出，不访问模型或修改用户存档。

最终候选版本验证通过后导出；候选未改且已有验证回执时复用结果。导出器仍核对输入和成品，避免将制作验证重复扩展成全范围研究。

默认导出三种成品；用户限定平台时按其范围导出：

```text
python scripts/export_game.py game.json --out exported --formats all --strict
```

输出包含 AI Skill、普通聊天启动包、SillyTavern 角色卡／世界书及审计材料。遇已有文件先比较并使用新版本目录；仅在本次请求允许替换对应产物时使用 `--force`。不自动发布到外站，不改用户模型、API 密钥或酒馆配置。

按 [runtime.md](references/runtime.md) 和 [exporting.md](references/exporting.md) 核实能力：宿主能否读写文件、联网、执行脚本、保存状态及按时点过滤。普通聊天模式可使用文本存档，但不能声称自动持久化、真实掷骰或严格秘密隔离。世界书关键词触发也不等于游戏规则或知识隔离。

若支持委派，给独立评估者生成的包及一项真实试玩任务，要求实际尝试自由行动、风险失败、秘密追问、存档续玩，并记录观察；不要提供预期答案暗示。无法实际试玩时交付并明确“静态检查通过，行为试玩未执行”，不得把结构检查写成试玩成功。

## 交付

给出可直接使用的成品及简短启动方式，说明采用版本、资料范围、玩法与已完成验证。附局限：单来源核心事实、未解决冲突、宿主能力缺口、哪些规则依赖模型裁定。最终文字聚焦用户怎样开始玩；研究与实现细节留在审计文件。

只有用户要求开始游戏时，才加载生成的主持包进入开局；继续现有游戏时使用其 runtime 和存档，不重新研究改写已发生事实。
