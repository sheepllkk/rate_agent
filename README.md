# 实时汇率监测预警智能体

基于大模型 Agent 的桌面端外汇监控系统，感知-思考-行动三段式架构。

## 架构

```
感知层  中国银行外汇牌价页（实测无反爬，盘中秒级更新）
        ↓ 定时轮询（默认 60s，可配）
存储层  SQLite（data/rates.db）汇率历史 + 预警记录
        ↓
分析层  pandas 时间序列：当日开盘基准、波动率、日内高低点
        ↓
思考层  大模型 Agent 决策（主引擎 Qwen3.6-35B-A3B，备用 DeepSeek，末级规则兜底，OpenAI 兼容接口）
        未配置 API Key 时自动降级为规则引擎，输出结构完全一致
        ↓
行动层  Windows 桌面系统通知 / 邮件 / 预警记录存档（验收录屏依据）
```

## 快速开始

```bash
# 1. 安装依赖（建议 venv）
pip install -r requirements.txt

# 2. 图形界面（推荐，比赛演示用）
python gui.py

# 3. 单轮采集决策（验收调试用）
python run.py --once

# 4. 常驻监控（无界面后台运行）
python run.py --loop

# 5. 演示模式：注入模拟急跌行情，触发完整预警链路
python run.py --simulate

# 6. 数据服务（小程序/手机端接口，端口 8788）
python server.py
```

配置：`config.json` —— 监控币种、各币种预警阈值、轮询间隔、LLM 接口、邮件通道、防重复下发参数。

## 后台常驻与开机自启（Windows）

- `start_bg_monitor.bat`：启动后台监控（pythonw 无窗口）。**启动前会自动停掉旧实例**；
  进程内有单实例文件锁 + 心跳守护，重复双击也不会跑出两个实例。
- `stop_bg_monitor.bat`：停止后台监控。
- 后台监控启动时会**自动附带启动数据服务**（server.py 的 API，端口 8788），
  小程序接口随之可用；若 8788 已被单独运行的 server.py 占用则跳过，互不影响。
- 开机自启：将 `fx_monitor_autostart.vbs`（如未包含，见下方说明）放入
  `Win+R → shell:startup` 打开的启动文件夹，即可登录后自动拉起后台监控与数据服务。

自建自启脚本：新建文本文件 `fx_monitor_autostart.vbs`（内容如下，两处路径按实际修改），
放入 `Win+R → shell:startup` 打开的启动文件夹：

```vbs
CreateObject("Wscript.Shell").Run """C:\Python311\pythonw.exe"" ""C:\你的路径\rate_agent\bg_loop.py""", 0
```

## 微信小程序（移动端）

- 源码在 `miniprogram/`，用微信开发者工具导入该目录即可预览。
- 接口地址在 `miniprogram/utils/api.js`：
  - 开发者工具调试用 `http://127.0.0.1:8788`（工具跑在电脑上，任何网络通用）；
  - 手机真机预览改成 server.py 打印的本机局域网 IP，且手机与电脑须同一网络
    （校园网常隔离设备，演示建议用手机热点）。
- 需先有数据服务在跑（`python server.py`，或后台监控自带的内嵌服务）。

## 验收对照（赛题要求 -> 实现）

| 赛题要求 | 实现位置 |
|---|---|
| 数据源为人行或四大行公开接口/页面 | `fx_agent/fetcher.py`（中国银行 boc.cn/sourcedb/whpj） |
| 硬性监测 CNY/USD/HKD/EUR | 默认监控 7 个常用币种（美元/欧元/港币/英镑/日元/澳元/加元），界面动态增删（中行牌价页覆盖 12+ 币种） |
| 动态添加币种 | `gui.py` 界面"添加币种/删除币种"按钮 + 阈值双击编辑，写回 config.json |
| pandas 做数据清洗与时序分析 | `fx_agent/analyzer.py` |
| 预警逻辑由大模型 Agent 处理 | `fx_agent/agent.py` + `docs/agent_prompt.md` |
| 感知-思考-行动工作流 | `run.py` 的 `cycle()` |
| Windows 桌面端 + 弹窗预警 | `fx_agent/alerter.py`（winotify 系统通知） |
| 预警数据结构（赛题示例 JSON） | Agent 输出字段与示例一致，另加 severity/reason/engine |
| 预警触发记录（截图/录屏依据） | `data/rates.db` 的 alerts 表 + 控制台输出 |

## 演示模式说明（重要）

银行牌价日内真实波动通常远小于 3%，直接现场等触发不现实。
`--simulate` 向库中注入一段标记为 `simulated` 的合成急跌行情，
随后走与真实模式**完全相同**的分析-决策-预警链路，用于验收演示。
数据库中 `source` 字段可区分真实/模拟记录，保证数据诚实可查。

## 可靠性设计（实测记录）

- **防预警风暴**：时间冷却（30 分钟，风险显著恶化除外）+ 事件状态判定
  （波动与上次相比无实质变化不重发）+ 事件周期（超过 24 小时视为新事件）。
- **多引擎降级**：主引擎故障自动切备用，再降级为规则引擎，链路永不中断。
- **输出清洗**：模型 reason 含思考过程或数字声明与实际波动不符时，自动以
  分析层模板结论替换，保证展示层干净可审计。
- **单实例守护**：文件锁 + 心跳，僵尸持有者可被新实例安全接管。

## 当前状态

- [x] 桌面端 GUI（实时牌价表 + 时间轴走势图 + 阈值步进器 + 币种增删 + 日志）
- [x] 后台常驻 + 开机自启 + 数据服务随行
- [x] 邮件预警实测（QQ SMTP SSL 465，已验证收信）
- [x] 微信小程序（实时牌价 / 预警记录筛选 / 走势图 / 阈值设置 / 邮件订阅）
- [x] 双引擎接入（Qwen3.6-35B-A3B 主 + DeepSeek 备）
- [x] 连续运行数据：自 2026-10-15 起持续积累（`data/rates.db`）
