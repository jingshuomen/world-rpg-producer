# 导出与跨客户端使用

权威输入只有符合 `data-contract.md` 的 `game.json`。从制作器根目录运行：

```powershell
python scripts/export_game.py game.json --out exported --formats all --strict
```

`--formats ai` 输出 AI Skill 和普通聊天包；`tavern` 只输出酒馆包；`all` 输出全部。每一种输出均含自己的游戏数据，带工具的包复制 `common.py`、`gamekit.py` 运行时；不依赖制作器、本机路径或 CSP。缺少实际运行时即报错，不生成空脚本。严格发布门槛以 `common.validate_game(..., strict=True)` 为准。未加 `--strict` 仅适合检查草稿，不能据导出成功声称已通过发布门槛。

## 输出结构

- `ai-skill/<game-id>/SKILL.md`：中文模板说明，按 meta.language 主持；配 `references/rules.md`、`world.md`、`npcs.md`、`claims.md`、`sources.md`、`runtime.md` 和 `game.json`，以及可复制运行的状态工具。
- `chat/START_HERE.md`、`MASTER_PROMPT.md` 与 `RUNTIME.md`：普通聊天启动包、内嵌完整游戏数据的提示和可选工具接口说明。没有 Python/文件工具时，每回合输出拟议 STATE JSON，由玩家手动保存；不能声称自动写盘或永久记忆。
- `tavern/character-v2.json`、`character-v3.json`：CCv2 / CCv3 核心字段的角色卡。主持纪律在系统提示中，世界条目在内嵌世界书中。
- `tavern/world-info.json`：独立 SillyTavern 原生世界书，`entries` 为以 uid 为键的对象；与 CC 卡中 `character_book.entries` 数组不同。卡内世界书与独立书不要重复绑定。
- `tavern/character.png`：标准库生成的有效 64×96 纯色占位图，含 `chara` 和 `ccv3` 两个 tEXt chunk，UTF-8 JSON 经 base64 编码；不是 AI 生成美术。
- `tavern/character.charx`：实际 ZIP 文件，根目录有 `card.json`；同时带 `game.json` 和占位图。客户端是否完整支持此格式需要实机确认。
- `tavern/STATE.json`、`GM_DOSSIER.md`、`game.json`、`RUNTIME.md` 和 `scripts/`：手动状态、带时点/知识标签的主持资料和工具。不会自动安装酒馆、连接模型或执行脚本。
- `audit/`：输入/输出校验报告、来源断言记录、导出检查范围和文件散列清单。

## 信息边界

导出前验证权威输入。导出副本递归剔除显式 `available_from > cutoff_tick` 的记录，清理被剔除断言的列表引用，再次验证。输入本身的时间边界错误也可能被验证器直接拒绝。固定 cutoff 不因故事推进而扩大。工具不能从自由文本自动识别未来剧透，制作时须为条目和断言填写准确时点。

酒馆中公开条目按关键词触发，不保证每次全部进入上下文。当前世界规律及当前时点 GM 私密资料写在主持卡 system_prompt 中供 GM 决策，不进入开场或玩家可见摘要；世界书中的 GM/private、NPC 内心目标及尚未到时条目默认 `enabled=false` / `disable=true`，且 `constant=false`。推进时间后，主持者按剧情从 dossier 提取当前允许资料，或由管理员人工启用对应条目；不能一键启用全部未来内容。隐藏资料仍包含在随包文件中，玩家可直接读取；模型共享上下文也不是真正权限隔离。

canon、interpretation、inference、design 及 verification 标签随导出保留。游戏原创规则和改编应有 design 标签，不能混成原作设定。来源结构合法不等于断言已得到原文支持，仍需研究时人工或模型核对定位与内容。

## 持久化与扩展

本版酒馆导出是基础卡片与数据包，**不宣称自带 MVU 自动数值更新或可靠战斗结算**。文本状态栏、模型输出 STATE、聊天摘要都不等于数据已持久化。普通聊天需人工保存；有 Python/文件工具时使用随包 `gamekit.py --help` 中真实可用的状态、上下文和存档命令，并检查返回值。

每轮 context 默认直接输出，避免重复创建固定同名文件；snapshot 不传 --out 时按分支和 revision 命名，相同状态重复调用复用已有快照。运行时召回会过滤本局记忆和事件摘要的知情范围。1.0.1 保留 schema 1.0；旧存档须保留原 game.json，游戏数据升级后新开局或显式迁移，导出不会替用户迁移存档。

以后可做独立、可选的 MVU/Tavern Helper 适配层，但必须明确扩展依赖、变量 schema、更新校验与回滚，并单独实机测试。基础导出不放空占位脚本，也不远程加载第三方 JavaScript。

## 覆盖与验证

所有产物先在输出目录内暂存并校验。存在同名文件时拒绝覆盖；明确 `--force` 后才能替换本次计划生成的同名文件，其他文件保留。提交采用逐文件 `os.replace`，发生异常时回滚已经替换的文件；清单最后提交。整个目录的切换不是事务型原子操作，断电时不能保证回滚，因此关键存档应另外备份。输出目录有独占锁，避免两个导出进程同时提交。拒绝路径穿越、保留设备名、经符号链接写入和覆盖输入游戏文件。

已实施的检查包括：输入/导出游戏数据校验；CCv2、CCv3 本版导出字段和世界书类型；独立 WorldInfo uid/key 映射和私密默认关闭；JSON 往返读取；PNG 签名、图像数据、各 chunk CRC 与两份卡片 JSON 往返；CHARX ZIP CRC 和根 card.json 往返。**这些是离线格式检查，不是所有规范可选功能的完整认证，也不是酒馆实机导入或模型试玩证明。** 实际范围写入每次的 `audit/export-report.json`。

格式参考：[CCv2](https://github.com/malfoyslastname/character-card-spec-v2/blob/main/spec_v2.md)、[CCv3](https://github.com/kwaroran/character-card-spec-v3/blob/main/SPEC_V3.md)、[SillyTavern WorldInfo 实现](https://github.com/SillyTavern/SillyTavern/blob/release/public/scripts/world-info.js)、[官方 World Info 文档](https://docs.sillytavern.app/usage/core-concepts/worldinfo/)。
