# 主持、结构化存档与续玩

制作器与游戏主持分开。制作完成后，把生成的 `ai-skill/<id>/` 交给支持 Skill/文件的 AI，或把 `chat/MASTER_PROMPT.md` 内容交给普通聊天 AI。只有用户要求游玩才进入开局。

## 能力分级

1. **有 Python + 文件工具**：可使用随游戏复制的 `gamekit.py` 保存、校验、掷骰、分支和过滤上下文。Python 3.10+，无必需第三方包或模型 API。模型负责理解行动、角色和叙事，脚本负责已实现的状态操作；本工具不是全自动战斗 AI、完整 D&D 或后台世界模拟器。
2. **只有文件读取**：模型读规则、资料和保存文本，输出拟议状态；由玩家或有执行能力的工具实际提交。不得声称执行成功。
3. **纯聊天**：每次重要变化输出完整拟议 `STATE` JSON 和本次变更记录，玩家手动复制保存；换聊天时提供游戏说明和最近存档。超过上下文窗口前主动给出续玩包。模型自称记住不算保存。

真实随机需执行器或玩家掷骰。纯聊天若用模型裁定，应明确称为叙事裁定，不捏造工具掷骰结果。

## 实际 CLI

以下命令从游戏包根目录运行；`game.json` 是导出游戏数据：

```bash
python scripts/gamekit.py validate game.json --strict
python scripts/gamekit.py start game.json --out session.json
python scripts/gamekit.py context game.json --session session.json --actor player
python scripts/gamekit.py context game.json --session session.json --actor gm
python scripts/gamekit.py context game.json --session session.json --actor N1
python scripts/gamekit.py verify game.json session.json
python scripts/gamekit.py snapshot game.json session.json
python scripts/gamekit.py fork game.json session.json --out alternate-session.json --branch alternate
```

`start` 只在新局执行；已有局从 verify 和 context 续玩。context 默认直接打印，可每轮重复；不带 --session 时只过滤游戏初态，不含该局事件日志。需要上下文文件时显式 --out 并选择新文件名。snapshot 默认在 session 所在目录的 snapshots 下按 session 标识、分支安全名称、分支 hash 和 revision 命名；同一状态重复调用返回 already_saved=true 并复用，内容不同则拒绝。显式 --out 和 start/fork 仍拒绝覆盖已有文件。

每轮顺序：读取最新 session → 按角色提取上下文与召回 → 理解玩家行动并声明风险 → roll/apply → 核对成功回执和最新状态 → 叙述已发生后果 → snapshot。GM 决策使用 gm context；NPC 发言仅使用该 NPC context；玩家呈现以 player context 核对。重要记忆随事件提交，不能仅停留在聊天摘要中。

| 行动类型 | 采用方式 |
|---|---|
| 已声明的重复关键检定 | mechanics.checks 的 rule_id，程序核对前置条件、参数与允许后果 |
| 菜单外创意或尚未声明的复杂互动 | 主持事先明示裁定，再用通用 operations 记录；程序只核对数据约束 |

提交失败时不叙述已经获得奖励或发生消耗。结果不确定时先 verify 并读最新 revision、last_check 与 resolved_checks：已记录但未结算的检查沿用原 check ID 和结果；已结算事件用完全相同 ID/请求重试获取回执，不能重新掷骰或换 ID 重复结算。

骰子事件增加 session revision，不自动推进剧情 turn/tick；其结果保存在事件和 `state.extensions.last_check`。重试同一 event-id、同一参数返回原结果，不重新掷骰。不同参数重用 ID 会被拒绝。是否允许玩家重新掷骰由游戏规则决定；工具本身仍允许明确创建一个新的检查 ID，因此主持需遵守规则。

`apply` 的事件 JSON：

```json
{
  "id": "action-1",
  "expected_revision": 1,
  "summary": "依检定结果记录玩家行动及后果",
  "requires_check": {"event_id": "check-1", "outcome": "success"},
  "operations": [
    {"op": "resource", "name": "currency", "delta": -2},
    {"op": "item", "id": "绳索", "delta": 1},
    {"op": "flag", "name": "paid_for_rope", "value": true}
  ]
}
```

根据真实骰子 outcome 选择后果，不预先假定 success。如果行动不需检定，省略 requires_check。引用的检查只能消费一次；同一行动 ID 的完全相同重试不重复扣款/奖励。event id 在分支内唯一，expected_revision 必须等于当前 revision；从最新 session 读取，不能凭聊天记忆猜值。

通用检查命令为 `python scripts/gamekit.py roll game.json session.json --event-id check-1 --expected-revision 0 --dice 1d20 --modifier 2 --target 12`；随后 `python scripts/gamekit.py apply game.json session.json action-1.json`。示例 JSON 仅展示字段，实际操作须与该行动和骰子结果相符。

### 可选关键检定绑定

在 mechanics.checks 中按 ID 声明 dice、target、modifier 和 consequences；success/failure 各包含一个或多个允许的 operations 数组。可选 preconditions 支持 stats_min、inventory_min、flags，roll 与 apply 都会检查。没有声明的规则不因此自动变成可靠战斗系统。

范例的不使用绳索、hp >= 2 的标准穿越使用 `python scripts/gamekit.py roll game.json session.json --event-id bridge-1 --expected-revision 0 --rule-id bridge`。工具从规则读取参数，拒绝不匹配的覆盖。结算请求同时填写 `rule_id: "bridge"` 和 `requires_check`，operations 必须逐项匹配真实 outcome 的允许方案，例如失败方案：

```json
{
  "id": "bridge-result-1", "expected_revision": 1,
  "rule_id": "bridge", "summary": "栈桥失足受伤，仍抵达前院并留下响动",
  "requires_check": {"event_id": "bridge-1", "outcome": "failure"},
  "recall": {"visibility": "private", "known_by": ["player"]},
  "operations": [
    {"op": "resource", "name": "hp", "delta": -2},
    {"op": "move", "location_id": "L3"},
    {"op": "flag", "name": "bridge_noise", "value": true}
  ]
}
```

若实际成功，使用规则中的 success 方案。绳索加成、低 hp 的净伤害和其他创意按明确裁定另行处理，不能冒用标准规则。绑定后果须作为 operations 前缀完整匹配；仅可在配方之后追加通过校验的 remember，与后果原子提交，其他变化需单独事件。接口只约束显式绑定的请求，不会从对白自动识别应当用哪个规则。

### 本局记忆与事件召回

用 apply 的 remember 操作记录会影响后续反应的承诺、误解或关键经历：

```json
{"op":"remember","id":"M1","text":"玩家答应陪沈岚核查灯塔安全。","visibility":"private","known_by":["player","N1"],"importance":5}
```

text 最长 2000 字符，importance 为 0…5、缺省为 1；工具自动记录当前 tick 与 branch_id。visibility 必须明示：gm 仅主持可读，known_by 留空；private 按显式 known_by；public 允许玩家读，NPC 仍需列入 known_by。同一记忆 ID 不可重写，以新事件记录承诺兑现或判断更正。旧存档缺少 memories 时视为空，不改写旧历史。

事件请求可加 recall:{visibility,known_by} 标记 summary 的召回范围；未标记的旧摘要默认仅 GM 可读。roll 可用 `--visibility private --known-by player N1` 标记摘要。context 输出 memory.important 和 memory.recent_events，按视角、分支与实际 tick 过滤，并限制总条数与文本字数；默认 --recall-limit 12 --recall-chars 6000，可调到 0…40 条、0…20000 字符。这是重要记录与近期事件的有限召回，不保证所有历史每轮都进入模型。NPC context 的 self.relation_to_player 提供当前关系维度，主持同时结合知情记忆作出反应。

### 已实现的状态操作

| op | 字段 | 效果与约束 |
|---|---|---|
| resource | name, delta | 只能改变 mechanics.resources 声明的资源，有限数值，不越 min/max |
| item | id, delta | 整数数量，不能消费不存在的物品；中文名称可用 |
| flag | name, value | 有限数值/字符串/布尔/null，记录事件状态 |
| relation | npc_id, dimension, delta | 已有 NPC 的关系维度，默认范围 -100…100，可在规则中设 relationship_bounds |
| quest | id, status | 已有任务；open/active/complete/failed，终态重开需 extensions.allow_quest_reopen 明示规则 |
| move | location_id | 已有且当前时点可用地点 |
| npc_status | npc_id, status | alive/dead/missing/inactive/unknown；死亡后复活需 extensions.allow_resurrection 明示规则 |
| discover | claim_id, 可选 npc_id | 玩家或某 NPC 获知当前时点已存在事实，不允许发现未来资料 |
| advance | ticks, 可选 label | 剧情时间前进；回退用独立分支，不能负数回拨 |
| remember | id, text, visibility, known_by, 可选 importance | 追加本局记忆，自动记录剧情时点与分支，不改原作 claims |

一组 operations 先在副本完整验证再提交，任何操作失败都不改变 session。资源 min/max 不代替伤害规则；HP 剩 1 时受到 5 点伤害，主持应按游戏规则提交到 0 的净变化，不能期待工具自行钳制或默改伤害。规则没有允许新增任务/角色时，主持不要直接编辑存档绕过工具；若确需扩展游戏，创建新游戏版本并制定显式迁移，旧存档保留。

## 保存完整性与恢复

session 在一个 JSON 中包含初态、事件日志与当前快照，用独占锁串行检查后原子替换。日志记录请求、骰子面值、前后状态 hash 和前一事件 hash。`verify` 会从初态逐事件重放，检查最新快照与日志一致。游戏数据 hash 也绑定到存档，不能换作品/版本继续套用。

v1.0.1 保持 game/session schema 1.0，新字段均可省略；原 game.json 未修改时旧存档仍可验证和续玩。升级运行时不等于更改游戏数据；修改 game.json 会使绑定 hash 不匹配，须新开局或制定显式迁移，保留旧版本和存档。本工具没有自动迁移命令。

兼容例外：现在拒绝尚未开放的初始地点，以及使用 gm/player 保留视角 ID 的 NPC；state.extensions.memories 若已有自定义内容，须符合新增的记忆结构。这些旧版可能放行的资料会被新版校验拒绝，不能靠直接改存档 hash 修复。保留旧包与旧存档，另行修正游戏并新开局或制定显式迁移；正常旧格式的实测兼容不代表所有自定义扩展都自动迁移。

这提供本地结构完整性、重复请求保护与确定性重放；不是签名或第三方真实性证明，不能阻止有文件写权限者重写整套资料。fork 保存父分支 head/state 收据，原分支不修改；单独的 fork 文件只能检查收据内部一致性，不证明外部父文件未改。

如果进程异常留下 `.lock`，先查看锁内 PID 确认没有进程正在写，再恢复最近有效 snapshot 或移除确实失效的锁。不要自动删正在使用的锁。`snapshot` 保存整份日志；纯文本聊天存档也应保留作品版本、分支、时间、角色状态、背包、任务、已知事实及未决选择。

## 视角与秘密

`context --actor player` 输出当前公开世界条目、已发现事实、玩家资源、任务状态及其可知的本局记忆；不会直接附上全部 claims 或 NPC 内心。`--actor N1` 根据该 NPC 的 knowledge、lore.known_by、动态 npc_knowledge 和记忆知情者提供资料。`--actor gm` 获得当前时点的主持资料。静态来源条目限制为 `min(state.time.tick, meta.cutoff_tick)`，本局记忆使用实际剧情 tick。

当前时间可推进到研究范围以后；那之后的剧情是本局原创发展，不能假造未研究的原作续集。资料中的自由文本必须由制作者审查；脚本只按结构化标签过滤，无法识别错标、夹带在描述/对白中的未来秘密。

过滤是减少误用的上下文边界。共享同一聊天、完整文件或主持卡的模型仍可能看到 GM 信息，玩家也能打开随包文件。没有独立权限执行环境时，只能要求主持不透露，不能承诺真实访问隔离。已看过秘密的模型再切换玩家/NPC，可能受先前上下文影响；需要更严格视角时用只提供对应过滤结果的新会话。

## 试玩记录

至少实际观察一次自由行动、一项有风险的判定及后果、秘密追问、保存/重开，并记录每次操作前后状态和是否通过。重要不变量包括：不替玩家说话；背包/资源不可重复结算；死亡和任务终态遵守规则；未来与非知情 NPC 信息不直接泄漏；分支不污染原分支。结构通过、一次短局通过、长期稳定属于不同证据，不相互替代。
