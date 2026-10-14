# 一手参考与独立实现边界

研究日期：2026-10-14。以下项目提供设计参考；本 Skill 的工作流、指令、契约与辅助脚本独立编写，不捆绑或复制其实现。链接用于核查设计来源，使用本 Skill 不要求安装这些项目。项目状态及许可可能变化，实际复用前读当时的许可文件。

| 一手来源 | 借鉴的思想 | 采用范围及边界 |
|---|---|---|
| [WorldBuilder](https://github.com/PoweringManipulation2/WorldBuilder) 与 [研究时所查附件](https://github.com/PoweringManipulation2/WorldBuilder/blob/main/Universe-Builder/worldbuilder-4.55.32.zip) | 已有作品研究、逐断言证据、版本和时间边界、研究后再生成、格式审计 | 借鉴可追溯制作流水线；本 Skill 独立实现，不捆绑其脚本或要求 SearXNG/WARP。所查仓库和附件未见明确 LICENSE，未复制其代码。 |
| [RPG Roleplay Platform](https://github.com/felixchaos/rpg-roleplay-platform) 与 [导入流水线](https://github.com/felixchaos/rpg-roleplay-platform/blob/main/rpg/platform_app/import_pipeline/runner.py) | 小说切块、人物与世界提取、时间线、原文检索、分支存档 | 借鉴原作材料→事实/人物/世界→游戏状态的分层；独立编写导入和运行工具，不引入其完整数据库平台；原项目 AGPL-3.0-or-later。 |
| [Lorecard](https://github.com/bmen25124/lorecard) | 从明确来源 URL 采集文字，再生成角色和世界条目 | 独立实现有限 HTTP 文本抓取、来源快照和宿主工具导入；不把 URL 抓取称为全网研究；原项目 GPL-3.0。 |
| [LoreBook Creator](https://github.com/virgilianshailer/lorebook-creator) 与 [ST-CARD-WRITER](https://github.com/STRockefeller/ST-CARD-WRITER) | 分类条目、关键词触发、字段检查、生成与审核分开、可导出卡片 | 借鉴制卡审查与格式往返验证，未复制 UI、提示词或实现；两项目研究时标注 MIT。 |
| [MagVarUpdate](https://github.com/MagicalAstrogy/MagVarUpdate)、[酒馆助手](https://github.com/N0VI028/JS-Slash-Runner)、[MemoryBooks](https://github.com/aikohanasaki/SillyTavern-MemoryBooks) | 结构化状态独立于叙事、逐消息/事件记录、长期剧情记忆 | 本版用独立 Python 状态工具及文本降级，不安装或捆绑扩展，不宣称提供 MVU 自动更新。MVU MIT；酒馆助手非商业许可；MemoryBooks AGPL-3.0。 |
| [SillyTavern 原仓](https://github.com/SillyTavern/SillyTavern) 与 [World Info 官方文档](https://docs.sillytavern.app/usage/core-concepts/worldinfo/) | 角色卡承载扮演信息；世界书按主题与关键词提供相关设定 | 导出独立 JSON 内容供用户导入；不复制酒馆源码。世界书触发不等于判定引擎、状态机或严格秘密隔离。酒馆是运行前端，不负责替制作端自动核验跨站资料。 |
| [Character Card V2 规范](https://github.com/malfoyslastname/character-card-spec-v2) 与 [Character Card V3 规范](https://github.com/kwaroran/character-card-spec-v3) | 可交换的角色卡与嵌入世界书字段 | 依据格式说明生成卡片；不假定所有酒馆版本支持所有扩展。实际格式与兼容性见 exporting.md。 |
| [open-tabletop-gm](https://github.com/Bobby-Gray/open-tabletop-gm)、[SKILL.md](https://github.com/Bobby-Gray/open-tabletop-gm/blob/main/SKILL.md)、[系统移植说明](https://github.com/Bobby-Gray/open-tabletop-gm/blob/main/SYSTEM-PORTING.md) | 通用主持能力与规则模块分离；文件存档；数值计算交给工具；尊重玩家行动权 | 借鉴结构，未复制指令或 Python 代码。该仓 [LICENSE](https://github.com/Bobby-Gray/open-tabletop-gm/blob/main/LICENSE) 为 AGPL-3.0-or-later，部分规则材料另有许可及 NOTICE。 |
| [My GM is AI](https://github.com/seewhydee/my-gm-is-ai) 与 [scenario-generation.md](https://github.com/seewhydee/my-gm-is-ai/blob/main/schema/scenario-generation.md) | 制作端先把情境编译成可玩的模组；运行端分理解行动、引擎判定与叙述 | 借鉴制作与运行分离，不复制模组或实现；原项目 GPL-3.0，README 明确实验性质。 |
| [Project Lunar](https://github.com/horizonfps/project-lunar) 与 [记忆源码](https://github.com/horizonfps/project-lunar/blob/master/backend/app/engines/memory_engine.py) | NPC 私有目标、事件记录、长期记忆和见证者知识归属 | 借鉴逐条知识归属，不照搬其四层摘要。其 MEMORY 层公开及见证者合并并不构成严格信息隔离。README 标注 MIT，研究时根目录未见独立 LICENSE；实际复制前须确认授权。 |
| [AI-RPG-Engine](https://github.com/l497996153/AI-RPG-Engine) | 模块描述、世界内容和提示词分离；schema 驱动不同玩法 | 借鉴多玩法数据包，未复制实现；原仓标注 MIT，是早期项目。 |
| [Ink](https://github.com/inkle/ink) | 变量、条件分支、路径汇合与可保存的叙事状态 | 借鉴显式状态与分支思想，不引入其编译器；原项目 MIT。 |
| [AI Dungeon 历史原仓](https://github.com/latitudegames/AIDungeon) | 自由文字行动与持续叙事的历史参考 | 不作为当前运行依赖；仓库已于 2023-10-05 归档，MIT，不能当作当前商业服务的源码。 |

另见 [datashaman/ai-ttrpg](https://github.com/datashaman/ai-ttrpg) 的事件溯源与确定性规则思路。研究时作者明确尚无许可证，因此未复用其代码或材料，也不将其列为可直接复制的开源底座。

## 本 Skill 的取舍

制作者负责研究作品与版本、核验来源、内置人物行为蒸馏和玩法改编；`game.json` 负责稳定表达这些结果；导出器适配 AI Skill、普通聊天和酒馆。运行时工具只落实已经支持的状态操作、过滤与判定，不把模型生成的文字当已执行计算。

跨媒介需要各自证据：小说章节、影视集数／场景／时间戳、动漫及改编版本分别记录。原作、解释、推断、原创改编和本局事实保留边界。CSP 可以作为已有材料来源，但不是依赖，也不能把动漫专用检索入口强套到所有作品。

来源条目的独立计数由工具辅助检查，语义支持由制作者读原文确认；独立行为试玩补充静态检查，不能由制作端自称全部成功。
