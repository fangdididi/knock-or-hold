# 分流规则（参考）

按顺序执行。辅助脚本严格实现以下规则。

## 1. 有效代价

| cost_of_delay | 分值 |
|---|---|
| none | 0 |
| low | 1 |
| medium | 2 |
| high | 3 |
| severe | 4 |

- `source: untrusted` 且未 `verified`：封顶 1（low）。
- `source: unknown` 且未 `verified`：封顶 2（medium）。
- `owner` 与 `known`：按填写值。

## 2. 截止压力

| 距 `deadline` 剩余时间 | 分值 |
|---|---|
| 已过期或 ≤ 30 分钟 | 3 |
| ≤ 4 小时 | 2 |
| ≤ 24 小时 | 1 |
| 更久或无截止时间 | 0 |

## 3. 等待

`nobody` 0，`person` 1，`many` 2。

**分数 = 代价 + 压力 + 等待**（0-9）。

**紧急**：满足任一条件：分数 ≥ `knock_threshold`（默认 6）；代价为 severe；截止时间早于下一份简报且代价 ≥ medium。

## 4. 初步去向

- `act`：仅当全部满足才 SILENT：类别在 `standing_permissions` 中、`reversible: true`、`external: false`、写了 `undo`、来源不是 untrusted。否则紧急则 ASK，不紧急则 BRIEF_ASK。
- `fyi` 且代价 0、压力 0、没人在等：DROP。
- 其他：紧急则 KNOCK，否则 BRIEF。

## 5. KNOCK 与 ASK 的时机

- 免打扰时段内：等到时段结束；除非事项严重、类别在 `quiet_bypass` 中，且没有截止时间或截止时间早于免打扰结束。
- 专注时段内（且不在免打扰时段）：等到专注结束；除非事项严重，或截止时间不晚于专注结束后 15 分钟。

## 6. 预算

- 发送时间相同的事项合并为一组 = 一次敲门。
- 每个本地日期有 `knock_budget_per_day` 次敲门；今天从 `knocks_sent_today` 开始计。
- 若某日预算已用完，该组只保留严重事项（透支）；其余转为该时间之后下一份简报中的 BRIEF（动作则为 BRIEF_ASK）。

## 7. 措辞

- KNOCK 与 ASK 必须有 `why_now`。
- ASK、BRIEF_ASK 和 decision 类 KNOCK 需要 2~3 个按钮；每个 1~3 个词（中日韩文字约 4 个字算一个词），≤ 24 个字符，不得重复。
- 审批必须有一个 `safe: true` 的按钮，且 `default_if_silent` 指向安全按钮。
- 警告：喊叫式用词（URGENT、ASAP、!!、紧急）、只有问句的摘要、摘要超过 160 个字符。

## 8. 反馈

| feedback | 含义 | 在 calibrate 中的作用 |
|---|---|---|
| needed_me | 这次敲门是对的 | 计入精确率 |
| could_wait | 本可以放进简报 | 降低精确率 |
| too_late | 没敲门的事其实应该敲门 | 建议门槛 -1 |
| wrong_silent | 不该悄悄执行 | 建议撤销该类授权 |
