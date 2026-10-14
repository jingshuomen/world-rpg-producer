# Game package 1.0 / 数据契约

`game.json` 是制作端唯一权威输入，UTF-8 JSON。所有工具保留未知字段。下列字段为 v1 核心；扩展放 `extensions`。文本由制作 Skill 研究、核验和编写，脚本不冒充大模型自动理解全文。

```json
{
  "schema_version": "1.0",
  "meta": {
    "id": "ascii-game-slug", "title": "游戏标题", "work": "作品或原创世界",
    "medium": "novel", "continuity": "明确版本/改编/时间线",
    "cutoff": "资料范围和剧透边界", "cutoff_tick": 10,
    "language": "zh-CN", "version": "1.0.0", "created_at": "2026-10-14",
    "research_completed_at": "2026-10-14", "status": "draft"
  },
  "sources": [
    {"id":"S1", "title":"来源", "uri":"https://example.org/source", "kind":"primary",
     "independence_group":"original-work", "retrieved_at":"2026-10-14", "status":"ok",
     "sha256":"可选内容散列", "continuity":"与 meta.continuity 相同"}
  ],
  "claims": [
    {"id":"C1", "text":"一个可核验的断言", "classification":"canon", "core":true,
     "verification":"single_source", "continuity":"与 meta.continuity 相同", "available_from":0,
     "source_refs":[{"source_id":"S1", "locator":"第 1 章 / 第 2 集 00:12:30 / 网页小节", "note":"支持关系"}],
     "conflicts":[]}
  ],
  "world": {
    "premise":"世界及玩家体验",
    "rules":[{"id":"R1","text":"世界规律","claim_ids":["C1"]}],
    "locations":[{"id":"L1","name":"地点","description":"说明","keys":["地点"],"claim_ids":["C1"],"available_from":0,"visibility":"public"}],
    "factions":[{"id":"F1","name":"势力","description":"目标和资源","keys":["势力"],"claim_ids":[],"available_from":0,"visibility":"gm"}],
    "lore":[{"id":"W1","name":"秘密","content":"内容","keys":["秘密"],"claim_ids":[],"available_from":0,"visibility":"gm","known_by":["N1"]}],
    "npcs":[{"id":"N1","name":"NPC","description":"画像","goals":["目标"],"behavior":["情境→反应"],"speech":"表达方式","knowledge":["C1"],"claim_ids":["C1"],"available_from":0,"status":"alive"}]
  },
  "gameplay": {
    "gm_rules":["玩家自己决定行动，主持世界及 NPC。"],
    "loops":["探索→决策→判定→后果"], "failure_policy":"失败推进及其代价",
    "mechanics":{"dice":"d20","resources":{"hp":{"min":0,"max":10},"currency":{"min":0,"max":1000000}}},
    "opening":{"scene":"开局的可感知情况","choices":["行动一","行动二","自由行动"]},
    "quests":[{"id":"Q1","title":"任务","objective":"可判定目标","status":"open"}],
    "endings":["有条件的结局或持续游玩的目标"]
  },
  "state": {
    "branch_id":"main", "turn":0, "time":{"tick":0,"label":"开局"}, "location_id":"L1",
    "player":{"name":"由玩家决定","identity":"独立新角色","stats":{"hp":10,"currency":0},"inventory":{},"flags":{},"relations":{}},
    "npc_status":{"N1":"alive"}, "quests":{"Q1":"open"}, "facts_known":[],
    "extensions":{}
  },
  "extensions":{}
}
```

`medium`: `novel|anime|film|tv|mixed|original`。`kind`: `official|primary|reference|community|user`。失败来源可记录 `failed|blocked|partial`，不能用于已核验断言。

`classification`: `canon|interpretation|inference|design`；`verification`: `verified|single_source|disputed|unsupported|not_applicable`。`verified` 必须有两个独立 `independence_group` 的可用来源；原文直接事实允许明确标为 `single_source`，不能冒充交叉核验。`design` 可无来源，使用 `not_applicable`。

`strict` 发布门槛：core 原作断言不得为 disputed/unsupported；非原创世界须存在可用来源及 canon 断言；所有断言引用必须存在、版本一致且不超过 cutoff_tick；single_source 可以发布但列入局限。混合版本必须通过 `extensions.continuities` 明确白名单并由主版本选择规则解释，不能仅写 mixed 就取消核对。

`available_from` 和 `time.tick` 使用制作时定义的同一时间轴整数，不等于聊天回合。章节/集数映射写进研究记录。`cutoff_tick` 是固定资料/剧透上限；`context` 还按当前时间和知识所有者过滤。GM 可读当前时点的私人事实，玩家只读 public 或已发现；NPC 只读其 knowledge / known_by 中的事实。这属于上下文过滤，无法在同一个聊天或共享文件环境建立真正秘密隔离。

剧情 tick 可以超过 cutoff_tick，之后的活动属于本局原创；静态资料仍以 min(tick, cutoff_tick) 过滤。本局记忆按实际 tick、分支和知情范围过滤，不受来源 cutoff 截断。meta.language 指定主持语言；模板说明仍为中文，不自动翻译用户资料或专名。

`world.rules` 的 claim_ids 是世界规律证据；游戏规则与原创补充使用 design 断言。所有 entity id 全局唯一；gm/player 专用于上下文角色，NPC 使用其他 ID。schema 数字需有限，数量不能为负。保存文本不复制整部原作，仅保留必要摘要、定位及用户授权材料。

每条 entity 的 available_from 不得早于其支持 claims 的 available_from。NPC 的自身 description/goals/behavior/speech 是可交给该 NPC 的主观行为档案，claim_ids 必须包含在其 baseline knowledge 内；NPC 不知道的身世、幕后操纵等放入独立 gm lore，不夹带到自身档案。起始已知事实也不能指向未来。脚本检查已标注的引用关系，自由文本漏标仍需语义审查。

## v1.0.1 可选运行时字段

- mechanics.checks：按规则 ID 定义 dice、target、可选 modifier、preconditions 和 consequences；后者为 success/failure 各自允许的 operations 数组方案。前置条件支持 stats_min、inventory_min、flags。roll 与 apply 都须绑定同一 rule_id，结算以前缀逐项匹配实际 outcome 的完整方案，仅在其后允许追加通过校验的 remember，与后果原子提交；其他额外操作须另行裁定。通用 apply 不识别自然语言意图。
- state.extensions.memories：由 remember 操作追加 id、text、visibility、known_by、importance，以及自动生成的 tick/branch_id。visibility 为 gm/private/public；public 允许玩家读，NPC 仍需列入 known_by；gm 只供主持。context 输出有长度上限的 memory.important 与 memory.recent_events。
- 事件 request.recall：可选的 visibility/known_by，只标记 summary 的召回范围；旧日志缺省为 GM-only。它不改变数值结算或授权查看完整存档。

这些字段均可省略，game/session schema 保持 1.0。meta.version 是每个游戏的内容版本，与制作器 manifest.version 独立；新游戏可从 1.0.0 开始。1.0.1 运行时兼容原 game.json 对应的旧存档；修改游戏数据后 hash 不同，需新开局或显式迁移，不能直接改 hash 跳过校验。完整命令与限制见 [runtime.md](runtime.md)。

## CLI 共用约定

- `common.py`: `load_json(path)`, `dump_json(path, data)`, `validate_game(game, strict=False)` 返回 `{ok,errors,warnings,metrics}`；导出前检查 ok。
- `gamekit.py init --out game.json --title ... --work ... --medium novel --continuity ... --cutoff ...` 创建待研究草稿。
- `gamekit.py validate game.json [--strict]`；非通过返回非零退出码。
- `export_game.py game.json --out exported [--formats all|ai|tavern] [--strict]`；输出 `ai-skill/<id>/`、`chat/`、`tavern/` 和 `audit/`。只写指定输出目录，遇已有文件拒绝覆盖或要求显式 --force。
- `research.py discover QUERY --out sources.json` 输出候选，候选不是核验通过；`fetch URL --out-dir corpus` 或 `ingest PATH --out-dir corpus` 保存来源快照；具体参数以各工具 --help 为准。

## 来源与借鉴

工作流借鉴公开项目的设计思想，代码和指令为本次独立实现；不复制 WorldBuilder、酒馆助手或其他项目实现。格式参考 CCv2 / CCv3 官方规范。具体链接及功能映射见 `project-notes.md`。
