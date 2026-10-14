# 跨媒介世界 RPG 制作器

world-rpg-producer v1.0.1 是一套 AI Skill：研究小说、动漫、电影、剧集、混合版本或原创世界，制作有来源依据、支持自由行动与存档的文字角色扮演游戏包。

## 安装

将本仓库完整下载到宿主支持的 Skill 目录，保持 SKILL.md、agents、assets、references 与 scripts 的相对结构不变。具体安装位置和注册方式以你的 AI 平台为准；只复制 SKILL.md 不足以安装完整功能。

可以把仓库链接交给支持联网与文件操作的云端 AI，并说明：

> 请将这个仓库作为 world-rpg-producer Skill 安装。先阅读 README.md、SKILL.md 和 manifest.json，确认你的平台支持的安装方式；保留完整目录结构，不覆盖已有同名 Skill。安装后运行 scripts/selftest.py 和 scripts/test_upgrade.py，并报告实际安装位置、版本和测试结果。不要把示例世界当作我的游戏或自动上传我的材料与存档。

## 使用

安装后对支持 Skill 调用的 AI 说：

> 使用 world-rpg-producer，根据我指定的作品版本和玩家身份收集核验资料，制作支持自由行动与存档的角色扮演游戏包。

建议提供作品名称、版本、开局时间线、剧透边界、玩家身份以及玩法偏好。无联网工具时可基于用户提供资料工作，但不能声称完成跨站核验。

## 功能

- 区分原作事实、解释、推断和原创设计，保留来源及版本边界。
- 将人物动机、关系、知识范围与世界规则整理为统一 game.json。
- 提供状态操作、检定、长期记忆、分支与可复用快照工具。
- 导出游戏主持 Skill、普通聊天启动文本、CCv2/CCv3 JSON、PNG 角色卡、CHARX 与 SillyTavern 世界书。

## 运行与验证

辅助脚本需要 Python 3.10+，不要求 API Key。研究与语义判断由宿主 AI 及其可用工具承担；本地 PDF 文本提取仅在已安装 pypdf 时可用。

在仓库根目录运行：

```bash
python scripts/selftest.py
python scripts/test_upgrade.py
```

v1.0.1 两组测试合计 38 项。测试覆盖不等于所有客户端导入和长期游玩质量均已验证。

## 兼容性与限制

- game/session schema 保持 1.0。旧存档需保留其原始 game.json；修改游戏数据需新局或显式迁移，不自动改写旧存档。
- 纯聊天模式需要手动保存，并使用外部骰子或明确标注的叙事裁定。
- 共享模型上下文与共享文件不提供真正的秘密隔离。
- 酒馆导出不会安装模型，也不等于自动 MVU 集成。
- 示例游戏及 legacy 测试样本仅供演示与回归测试，不是个人聊天或真实玩家存档。

详细接口和设计边界见 SKILL.md 与 references/。

## 许可

本仓库保留原始 MIT LICENSE。参考项目、来源和独立实现边界见 references/project-notes.md；引用链接不表示捆绑其代码或改变第三方材料的许可。用户另行提供的作品内容不因使用本工具而自动获得再发布许可。
