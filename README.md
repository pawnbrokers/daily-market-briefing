# 📊 Daily Market Briefing

> **AI 驱动的每日股市晨报** — 多源数据采集 + AI 智能研判 → 一键输出飞书/本地报告

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.11+-green.svg)
![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux-brightgreen.svg)

---

## ✨ 功能一览

| 能力 | 主数据源 | 兜底数据源 | 覆盖范围 |
|------|----------|------------|----------|
| A股指数 | Tushare | 东方财富K线 | 上证/深证/创业板/上证50/中证500/中证1000 |
| 涨跌停统计 | 东方财富HTTP | — | 涨停/跌停数量 |
| 北向资金 | Tushare moneyflow_hsgt | — | 沪股通/深股通/北向合计 |
| 行业板块涨跌 | 东方财富HTTP | Tushare申万(补充) | 行业涨跌Top5/Bottom3 + 主力资金流向 |
| 港股指数 | 新浪实时 | 东方财富K线 / efinance / yfinance | 恒生指数 / 恒生科技指数 |
| 南向资金 | Tushare moneyflow_hsgt | — | 港股通当日净流入/流出 |
| 美股三大指数 | 新浪实时 | 东方财富K线 / yfinance | 道琼斯 / 纳斯达克 / S&P500 |
| VIX恐慌指数 | yfinance | — | 实时波动率指标 |
| 美债收益率 | yfinance | — | 10Y国债收益率 |
| 美元指数 | yfinance | — | DXY |
| 经济日历 | Tushare cn_schedule + 东方财富 | — | 未来7天重要经济事件 |
| 新闻热点搜索 | WebSearch | — | 央行政策/美联储/地缘政治/大宗商品 |

**数据源架构**：

```
主数据源（优先）              兜底数据源（仅异常时触发）
──────────────              ──────────────────
Tushare (A股指数/资金)       efinance (A股/港股/美股实时)
新浪HTTP (港股/美股实时)      yfinance (美股/港股/VIX/美债)
东方财富HTTP (涨跌停/板块/日历)
```

**异常检测与自动兜底**：
- 数据为空或返回 error → 自动 fallback 到兜底数据源
- 涨跌幅 > 15%（大概率数据错误）→ 标记 warning 并 fallback
- 数据日期超过3天 → 标记 warning 并 fallback

**输出**：结构化 Markdown 报告（关键信号 + 宏观环境 + 热点板块 + 风险预警 + 操作建议），支持自动推送到**飞书文档**或保存为本地文件。

---

## 🚀 快速开始

### 前置条件

- Python 3.11+
- [Tushare Token](https://tushare.pro/)（免费注册，[申请地址](https://tushare.pro/register?reg=7)）
- Claude Code / Cursor / 其他支持 Skill 的 AI CLI（用于 AI 分析）

> 不需要飞书账号，飞书是可选功能。

### 1. 安装

```bash
# 克隆仓库
git clone https://github.com/pawnbrokers/daily-market-briefing.git
cd daily-market-briefing

# 安装依赖（推荐用 venv）
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 配置 Token
cp .env.example .env
# 编辑 .env，填入你的 TUSHARE_TOKEN
```

### 2. 安装为 Claude Code Skill

```bash
# 方式A：全局安装（所有项目可用）
mkdir -p ~/.claude/skills/daily-market-briefing/scripts
cp SKILL.md ~/.claude/skills/daily-market-briefing/
cp scripts/fetch_market_data.py ~/.claude/skills/daily-market-briefing/scripts/
cp scripts/run_daily_briefing.sh ~/.claude/skills/daily-market-briefing/scripts/
chmod +x ~/.claude/skills/daily-market-briefing/scripts/*.sh

# 方式B：项目级安装（仅当前项目可用）
# 将 SKILL.md 放到项目的 .claude/skills/ 目录下即可
```

安装完成后，在 Claude Code 中输入 `晨报` 或 `daily briefing` 即可触发。

### 3. 手动运行一次

```bash
# 采集数据
python3 scripts/fetch_market_data.py --all

# 触发 AI 分析（在 Claude Code 对话中输入以下内容，或直接说"晨报"）
# "请执行每日市场晨报工作流。数据已保存在 history/data_$(date +%Y-%m-%d).json"
```

---

## ⏰ 定时任务

### macOS (launchd)

```bash
# 加载定时任务（周一至周五 9:00 自动执行）
launchctl load ~/Library/LaunchAgents/com.daily-market-briefing.plist

# 卸载定时任务
launchctl unload ~/Library/LaunchAgents/com.daily-market-briefing.plist
```

需要先创建 plist 文件：

```bash
cat > ~/Library/LaunchAgents/com.daily-market-briefing.plist << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.daily-market-briefing</string>
    <key>ProgramArguments</key>
    <array><string>/bin/bash</string><string>{SKILL_PATH}/scripts/run_daily_briefing.sh</string></array>
    <key>StartCalendarInterval</key>
    <array>
        <dict><key>Hour</key><integer>9</integer><key>Minute</key><integer>0</integer><key>Weekday</key><integer>1</integer></dict>
        <dict><key>Hour</key><integer>9</integer><key>Minute</key><integer>0</integer><key>Weekday</key><integer>2</integer></dict>
        <dict><key>Hour</key><integer>9</integer><key>Minute</key><integer>0</integer><key>Weekday</key><integer>3</integer></dict>
        <dict><key>Hour</key><integer>9</integer><key>Minute</key><integer>0</integer><key>Weekday</key><integer>4</integer></dict>
        <dict><key>Hour</key><integer>9</integer><key>Minute</key><integer>0</integer><key>Weekday</key><integer>5</integer></dict>
    </array>
    <key>StandardOutPath</key><string>{SKILL_PATH}/logs/launchd_stdout.log</string>
    <key>StandardErrorPath</key><string>{SKILL_PATH}/logs/launchd_stderr.log</string>
</dict>
</plist>
EOF
```

将 `{SKILL_PATH}` 替换为你的实际路径（如 `~/.claude/skills/daily-market-briefing`）。

### Linux (crontab)

```bash
# 编辑 crontab
crontab -e

# 添加一行：
0 9 * * 1-5 /bin/bash {SKILL_PATH}/scripts/run_daily_briefing.sh >> {SKILL_PATH}/logs/cron.log 2>&1
```

---

## 🎯 输出报告示例

```
# 每日市场晨报 | 2026-05-29 | 🟡 谨慎进攻

---

## ⚡ 今日关键信号

- **创业板指 +1.96% 领涨两市** — 成长风格占优，但上证50下跌(-0.70%)，大小分化明显
- **北向资金净流出 67.75 亿** — 外资连续流出需警惕
- **恒生指数 -1.27%** — 港股承压，南向资金逆势流入(+76亿)

---

## 🌍 宏观环境

| 指标       | 数值      | 变动   | 信号 |
| ---------- | --------- | ------ | ---- |
| 上证指数   | 4098.64   | +0.12% | 平   |
| 深证成指   | 15861.89  | +0.80% | 多   |
| 创业板指   | 4125.07   | +1.96% | 强多 |
| 恒生指数   | 25006.16  | -1.27% | 空   |
| S&P 500    | 7563.63   | +0.58% | 多   |
| 北向资金   | -67.75亿  | —     | 空   |

---

## 🔥 热点板块

### 1. 通信 (+4.59%) — AI算力需求持续催化
- 判断：短期主线

### 2. 建筑材料 (+3.78%) — 政策预期驱动
- 判断：一日游概率大

---

## ⚠️ 风险预警

### 1. 大小盘分化加剧
- **事件**：上证50 vs 创业板走势背离
- **触发条件**：北向资金持续流出且无回流迹象
- **影响路径**：权重股拖累大盘，中小盘赚钱效应难持续

---

*本报告由 AI 生成，仅供参考，不构成投资建议。*
*数据来源：Tushare / 新浪财经 / 东方财富 / yfinance / efinance（多源交叉验证）*
```

---

## 🔧 自定义配置

### 调整采集的市场

编辑 `scripts/fetch_market_data.py` 中的指数列表即可增删监控标的。

### 调整报告模板

编辑 `SKILL.md` 中的「文档模板」部分，可自由定制输出格式。

### 接入其他数据源

脚本已实现多源交叉验证架构，新增数据源只需：
1. 在对应 fetch 函数中添加主数据源逻辑
2. 在 fallback 函数中添加兜底逻辑
3. 在异常检测中添加验证规则

---

## 📁 项目结构

```
daily-market-briefing/
├── SKILL.md                          # Claude Code Skill 定义
├── scripts/
│   ├── fetch_market_data.py          # 数据采集脚本（多源交叉验证）
│   └── run_daily_briefing.sh         # 定时执行入口
├── .env.example                      # 环境变量模板
├── .gitignore                        # Git 忽略规则
├── requirements.txt                  # Python 依赖
└── README.md                         # 本文件
```

---

## 🤝 与外部 Skill/MCP 的关系

本项目可以与以下工具组合使用，构建更强大的工作流：

| 工具 | 用途 | 安装方式 |
|------|------|----------|
| [anthropics/financial-services](https://github.com/anthropics/financial-services) | Anthropic 官方金融 Skill，11个专业数据源 MCP | Claude Cowork 插件 |
| [Horizon](https://github.com/Thysrael/Horizon) | AI 新闻雷达，多源聚合+去重+摘要 | GitHub clone |
| [Exa MCP](https://github.com/exa-labs/exa-mcp-server) | AI 搜索引擎，深度研究 | `npx exa-mcp-server` |
| [Feishu MCP](https://github.com/larksuite/lark-openapi-mcp) | 飞书官方 MCP，文档读写推送 | GitHub clone |
| [Alpaca MCP](https://github.com/alpacahq/alpaca-mcp-server) | 美股交易执行（纸盘模拟） | GitHub clone |
| [tradermonty/claude-trading-skills](https://github.com/tradermonty/claude-trading-skills) | 56个专业交易 Skill（技术分析/VCP筛选等） | GitHub clone |
| [himself65/finance-skills](https://github.com/himself65/finance-skills) | 金融分析+社交情绪+TradingView | GitHub clone |

---

## 📋 License

MIT License — 自由使用、修改、分发。

---

## ⚠️ 免责声明

本工具生成的所有分析和建议**仅供参考，不构成任何形式的投资建议**。投资有风险，入市需谨慎。
