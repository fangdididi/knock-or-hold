# knock-or-hold（敲门还是先放着）

[English](README.md) | **简体中文**

一个智能体技能，决定**主动型智能体何时、以何种方式联系它的主人**：立刻敲门、请求批准、留到下一份简报、悄悄做完并留痕，或者干脆不提。

后台智能体会盯着邮件、日历、聊天和长时间运行的任务，每天都能发现几十件“可以说一下”的事。全说，主人就不看了；说得太少，扣款失败要等到晚上才被发现；动手太随意，就会有没人批准过的东西被发出、付掉或删掉。knock-or-hold 用成文规则、一个小型打分模型、每日打扰预算和反馈闭环，取代这种凭感觉的判断。

## 为什么需要它

- **打扰的代价远大于一条消息本身。** 回到深度工作需要时间，在任务中间被打断比在自然停顿处被打断更伤。所以本技能会在免打扰时段和专注时段暂缓敲门，并把多条合并成一条。
- **吵闹的提醒会让人学会忽略提醒。** 较小的每日预算加上敲门门槛，让每次敲门都值得一看；“这个可以等”“告诉我太晚了”之类的反馈会调整门槛。
- **无人看管的动作需要审批分级。** 只有主人事先授权、可撤销、不对外的小动作才会静默执行。凡是别人会看到或无法撤销的，一律先问，展示确切动作，并且沉默永远不算同意。
- **外部内容不是老板。** 陌生人一句“紧急，立即验证”换不来一次敲门；内容里夹带的指令只会被报告，绝不会被执行。

## 功能

- **敲门卡**：时区、免打扰时段（可跨午夜）、按星期设置的专注时段、简报时间、每日敲门预算、门槛、长期授权、免打扰豁免类别。
- **事项卡**：类型（`fyi`/`alert`/`decision`/`act`）、来源可信度、拖延代价、截止时间、谁在等，以及带可撤销性、对外可见性和撤销方法的确切动作。
- **六种去向**，每个都写明理由：`KNOCK`、`ASK`、`BRIEF`、`BRIEF_ASK`、`SILENT`、`DROP`。
- **打分**：代价（0-4）+ 截止压力（0-3）+ 等待（0-2）；严重事项，以及截止时间早于下一份简报且代价不低的事项，无论分数都算紧急。
- **可信度封顶**：未核实前，不可信来源代价封顶为 low，未知发件人封顶为 medium。
- **时机**：免打扰时段暂缓，只有列入豁免清单的严重事项例外；专注时段暂缓，截止时间有 15 分钟缓冲。
- **合并与预算**：同一时刻的敲门合并为一条；按天计预算，只有严重事项可以透支。
- **一键回复按钮**：2~3 个选项，每个最多 3 个词 / 24 个字符（支持中文），其中一个标记为安全选项，不回复即等于安全选项。
- **计划复核**：对手动修改过的计划重新检查所有硬规则，任一违规返回退出码 1。
- **渲染**：生成敲门消息和简报，包含“需要你点头”“值得知道”“已悄悄处理（都可以撤销）”。
- **校准**：敲门精确率、迟到事项、不受欢迎的静默动作，以及建议的新门槛。
- 仅用 Python 3.9+ 标准库，不联网，116 个测试。

## 安装

技能就是这个文件夹，放到你的智能体读取技能的位置：

| 智能体 | 位置 |
|---|---|
| Claude Code | `~/.claude/skills/knock-or-hold/`（或项目内 `.claude/skills/`） |
| Codex CLI | `~/.codex/skills/knock-or-hold/` |
| Cursor | 项目内 `.cursor/skills/knock-or-hold/`，或在规则里引用 `SKILL.md` |
| 其他智能体（Gemini CLI、OpenClaw、Windsurf、自建） | 任意位置，让智能体读取 `SKILL.md` |

```bash
git clone https://github.com/fangdididi/knock-or-hold ~/.claude/skills/knock-or-hold
```

## 使用

对智能体说类似这样的话：“我工作时你帮我盯着邮件和日历。用 knock-or-hold：免打扰 22:30-07:30，工作日 9 点到 11 点半是专注时间，每天最多提醒我 3 次，订阅邮件你可以直接归档。”智能体会先写好敲门卡，再对发现的事项分流。脚本也可以直接使用：

```bash
python3 scripts/knock.py init mysetup                       # 生成 card.json 和 moments.json 模板
python3 scripts/knock.py route card.json moments.json --out plan.json --ledger ledger.jsonl
python3 scripts/knock.py check plan.json                    # PASS 或规则错误，带退出码
python3 scripts/knock.py render plan.json                   # 敲门消息 + 下一份简报
python3 scripts/knock.py lint moments.json                  # 只检查按钮和措辞
python3 scripts/knock.py calibrate ledger.jsonl --threshold 6
python3 -m unittest discover -s tests                       # 运行测试
```

退出码：0 正常，1 规则或 lint 错误，2 输入无效。

## 示例

周三 09:40，主人处于专注时段，直到 11:30：

```text
$ python3 scripts/knock.py route examples/card.json examples/moments.json --now 2026-10-28T09:40:00+08:00
KNOCK      m1   score=5 at=11:30  held for focus block until 11:30
SILENT     m2   score=0 at=09:40  standing permission 'archive_newsletter', reversible, private
BRIEF_ASK  m3   score=3 at=17:30  needs approval: not reversible
BRIEF      m4   score=1 at=17:30  untrusted source cannot raise its own urgency: cost capped at low
KNOCK      m5   score=5 at=11:30  held for focus block until 11:30
KNOCK      m6   score=4 at=09:40  breaks focus block: cannot wait until 11:30
DROP       m7   score=0 at=09:40  no cost, no deadline, nobody waiting
...
sends: 2
```

解读：异地登录（m6）严重，立即打断专注；房租扣款失败（m1）和维修师傅约时间（m5）等到 11:30 合并成一次敲门；回复客户（m3）对外可见，放进简报请求批准；陌生邮件里的“URGENT!!”（m4）来源不可信，代价被封顶，只在简报里带“未核实发件人”标注；归档订阅邮件（m2）有授权且可撤销，直接做；周报（m7）没人在等，不提。总共只发 2 次。

完整的卡片、事项、计划和渲染结果见 `examples/`。

## 文件结构

```text
knock-or-hold/
├── SKILL.md                      # 智能体指令（英文）
├── SKILL.zh-CN.md                # 智能体指令（中文）
├── README.md / README.zh-CN.md
├── LICENSE                       # MIT
├── references/
│   ├── routing-rules.md          # 打分与分流规则全文
│   └── routing-rules.zh-CN.md
├── scripts/
│   └── knock.py                  # init / route / check / render / lint / calibrate
├── examples/
│   ├── card.json                 # 敲门卡
│   ├── moments.json              # 十件候选事项
│   ├── plan.json                 # 分流后的计划
│   └── render.md                 # 敲门消息 + 简报
└── tests/
    └── test_knock.py             # 116 个 unittest 用例
```

## 许可证

MIT，见 [LICENSE](LICENSE)。
