#!/bin/bash
# 每日市场晨报 - 定时执行脚本
# 用法: bash scripts/run_daily_briefing.sh
# 定时配置见 README

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
HISTORY_DIR="$REPO_DIR/history"
LOG_DIR="$REPO_DIR/logs"
DATE=$(date +%Y-%m-%d)

mkdir -p "$HISTORY_DIR" "$LOG_DIR"

exec >> "$LOG_DIR/run_${DATE}.log" 2>&1

echo ""
echo "=========================================="
echo "[$(date)] 每日市场晨报开始"
echo "=========================================="

# Step 1: 采集市场数据
echo "[$(date)] Step 1: 采集市场数据..."
DATA_FILE="$HISTORY_DIR/data_${DATE}.json"
python3 "$SCRIPT_DIR/fetch_market_data.py" --all --output "$DATA_FILE"

if [ ! -f "$DATA_FILE" ]; then
    echo "[$(date)] ERROR: 数据采集失败，退出" >&2
    exit 1
fi

echo "[$(date)] 数据采集完成: $DATA_FILE"

# Step 2: 调用 AI 执行分析并生成报告
echo "[$(date)] Step 2: AI 分析与报告生成..."

PROMPT="请执行每日市场晨报工作流。

市场数据已采集到 $DATA_FILE，请先读取该文件，然后：

1. 用 WebSearch 搜索今日财经新闻热点（关键词：中国货币政策、美联储、地缘政治、A股热点板块、大宗商品原油黄金）
2. 基于数据和新闻进行研判分析
3. 生成报告，标题格式：每日市场晨报 | $DATE | {定调emoji} {定调文字}
4. 尝试用 lark-cli 创建飞书文档：lark-cli docs +create --title \"标题\" --markdown \"内容\" --api-version v2
5. 如果 lark-cli 不可用，将报告保存到 $HISTORY_DIR/briefing_${DATE}.md
6. 在终端输出报告摘要

重点关注：可能改变市场方向的信息、对未来有重大影响的事件。不要面面俱到，只输出真正重要的内容。"

# 尝试使用 AI CLI
if command -v coco &>/dev/null; then
    echo "[$(date)] 使用 coco CLI 执行分析..."
    echo "$PROMPT" | coco --non-interactive || true
elif command -v claude &>/dev/null; then
    echo "[$(date)] 使用 claude CLI 执行分析..."
    echo "$PROMPT" | claude --print || true
else
    echo "[$(date)] WARNING: 未找到 coco/claude CLI，仅保存原始数据"
    echo "请手动运行 AI CLI，然后输入 '晨报' 触发工作流"
fi

echo "[$(date)] === 每日市场晨报完成 ==="
