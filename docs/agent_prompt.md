# Agent 决策提示词 / 工作流配置说明

## 工作流（感知 - 思考 - 行动）

1. **感知**：`fetcher.fetch_boc()` 拉取中国银行外汇牌价 → 落库 SQLite
2. **分析**：`analyzer.analyze()` 用 pandas 计算各币种当日开盘基准、
   实时波动率、日内高低点，产出结构化分析结果
3. **思考**：分析结果连同阈值交给 Agent（`agent.decide()`）
4. **行动**：`alerter.dispatch()` 按决策下发桌面通知 / 邮件并存档

## 多级引擎设计

- **LLM 主引擎**：`config.json -> llm` 配置。当前为组委会提供的
  **Qwen3.6-35B-A3B**（联通云算力网关，OpenAI 兼容接口，鉴权头直接放 key）。
- **LLM 备用引擎**：`config.json -> llm.fallback`（可选）。主引擎故障、限流
  或网络异常时自动接管，当前为 DeepSeek。
- **规则引擎**（兜底）：全部 LLM 引擎失败或未配置时自动接管，
  决策逻辑与输出 JSON 结构与 LLM 引擎完全一致，保证演示永不掉链子。
  输出中的 `engine` 字段标记实际使用的引擎与模型（如 `llm:Qwen3.6-35B-A3B`
  / `llm:deepseek-chat` / `rule`），评委可查验。

## 系统提示词（SYSTEM_PROMPT，见 fx_agent/agent.py）

你是外汇监控系统的决策 Agent。你会收到各币种的实时分析数据（现价、日内波动率、阈值）。
请对每个币种独立决策，严格输出 JSON 数组，每个元素包含字段：
- agent_action: "trigger_alert"（达到阈值需要预警）或 "observe"（继续观察）
- severity: "紧急" | "警告" | "关注"（仅 trigger_alert 时有意义；超阈值1倍以上为紧急，其余为警告）
- currency_pair: 如 "USD/CNY"
- current_rate: 当前价（数字）
- fluctuation: 如 "0.35%"（带符号）
- threshold_set: 如 "0.10%"
- reason: 一到两句中文，说明波动判断依据与建议动作

规则：abs(fluctuation) >= threshold_set 时必须 trigger_alert。
只输出 JSON 数组，不要输出其他任何文字。

调用参数：temperature=0.2（决策任务要求稳定输出），max_tokens=3000
（Qwen3.6 为思考型模型，思维链计入 max_tokens，需留余量防止 JSON 截断）。
输入为用户消息：分析结果 JSON 数组（由 pandas 层产出）。

## 冷却机制（防预警风暴）

行动层在下发前做冷却判定（`config.json -> alert.cooldown_minutes`，默认 30 分钟）：
同一币种在冷却期内已发过预警的，本轮重复触发不再下发（系统通知、邮件、
预警记录均不重复写入）。作用：开盘行情跳变、持续偏离阈值时避免每分钟重复
轰炸用户；首次预警始终即时送达。该机制在代码层保证，不依赖模型自觉。

## 阈值规则

- 阈值按币种配置于 `config.json -> thresholds_pct`，未配置的币种用"默认"
- severity 分级：|波动| >= 1×阈值 → 警告；|波动| >= 2×阈值 → 紧急

## 输出示例（与赛题示例结构一致）

```json
{
  "agent_action": "trigger_alert",
  "severity": "紧急",
  "data_source": "中国银行",
  "currency_pair": "USD/CNY",
  "current_rate": 6.505,
  "fluctuation": "-3.20%",
  "threshold_set": "0.10%",
  "timestamp": "2026-10-15T14:25:03Z",
  "reason": "美元兑人民币日内贬值 -3.20%，已突破 0.1% 预警阈值，建议关注持仓或换汇时机。",
  "engine": "rule"
}
```
