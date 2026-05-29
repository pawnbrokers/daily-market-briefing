#!/usr/bin/env python3
"""
个股筛选脚本 - 从板块/行业中筛选优质个股
数据源：Tushare（财务/行情）+ 东方财富HTTP（实时行情/资金流向）
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta

import requests

# 复用主脚本的工具函数
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from fetch_market_data import (
    get_tushare_token, _safe_float, _em_get_json, EM_TOKEN, TIMEOUT,
)


def fetch_sector_stocks(industry_name=None, top_n=10):
    """从东方财富获取行业板块内个股行情+资金流向"""
    # 先获取行业板块列表，找到对应的板块代码
    sector_url = "https://push2.eastmoney.com/api/qt/clist/get"
    sector_params = {
        "pn": "1", "pz": "50", "po": "1", "np": "1",
        "ut": EM_TOKEN, "fltt": "2", "invt": "2",
        "fid": "f62",
        "fs": "m:90+t:2",
        "fields": "f12,f14,f2,f3,f62",
    }
    sector_resp = _em_get_json(sector_url, sector_params)
    if not sector_resp or not sector_resp.get("data") or not sector_resp["data"].get("diff"):
        return []

    # 找到目标行业
    target_code = None
    for item in sector_resp["data"]["diff"]:
        if industry_name and industry_name in item.get("f14", ""):
            target_code = item.get("f12")
            break

    if not target_code and industry_name:
        return []

    # 如果没指定行业，返回所有行业及其代码
    if not industry_name:
        return [{"code": item.get("f12"), "name": item.get("f14"),
                 "pct_chg": _safe_float(item.get("f3")),
                 "main_flow": round(_safe_float(item.get("f62", 0)) / 1e8, 2)}
                for item in sector_resp["data"]["diff"]]

    # 获取板块内个股
    stock_url = "https://push2.eastmoney.com/api/qt/clist/get"
    stock_params = {
        "pn": "1", "pz": str(top_n * 3), "po": "1", "np": "1",
        "ut": EM_TOKEN, "fltt": "2", "invt": "2",
        "fid": "f62",  # 按主力净流入排序
        "fs": f"b:{target_code}+f:!50",
        "fields": "f12,f14,f2,f3,f4,f5,f6,f7,f8,f9,f10,f15,f16,f17,f18,f20,f21,f62,f184",
    }
    stock_resp = _em_get_json(stock_url, stock_params)
    if not stock_resp or not stock_resp.get("data") or not stock_resp["data"].get("diff"):
        return []

    stocks = []
    for item in stock_resp["data"]["diff"]:
        pct = _safe_float(item.get("f3"))
        main_flow = _safe_float(item.get("f62"))
        mkt_cap = _safe_float(item.get("f20"))  # 总市值
        turnover_rate = _safe_float(item.get("f8"))
        pe = _safe_float(item.get("f9"))
        pb = _safe_float(item.get("f23") if "f23" in item else None)

        # 基本筛选：排除ST、退市、停牌
        name = item.get("f14", "")
        if any(k in name for k in ["ST", "退", "*ST"]):
            continue
        if _safe_float(item.get("f2")) is None:  # 无最新价=停牌
            continue

        stocks.append({
            "code": item.get("f12", ""),
            "name": name,
            "close": _safe_float(item.get("f2")),
            "pct_chg": pct,
            "change_amt": _safe_float(item.get("f4")),
            "amplitude": _safe_float(item.get("f7")),
            "turnover_rate": turnover_rate,
            "pe": pe,
            "mkt_cap": round(mkt_cap / 1e8, 2) if mkt_cap else None,  # 元→亿元
            "main_net_flow": round(main_flow / 1e8, 2) if main_flow else 0,
            "main_flow_ratio": _safe_float(item.get("f184")),
            "high": _safe_float(item.get("f15")),
            "low": _safe_float(item.get("f16")),
            "open": _safe_float(item.get("f17")),
            "prev_close": _safe_float(item.get("f18")),
            "source": "eastmoney",
        })

    return stocks[:top_n]


def fetch_stock_fundamentals(ts_code, pro):
    """从 Tushare 获取个股基本面数据"""
    data = {}
    try:
        # 最近财务指标
        df = pro.fina_indicator(ts_code=ts_code, start_date=(datetime.now() - timedelta(days=180)).strftime("%Y%m%d"))
        if df is not None and len(df) > 0:
            row = df.iloc[0]
            data["roe"] = _safe_float(row.get("roe"))
            data["grossprofit_margin"] = _safe_float(row.get("grossprofit_margin"))
            data["netprofit_margin"] = _safe_float(row.get("netprofit_margin"))
            data["revenue_yoy"] = _safe_float(row.get("or_yoy"))  # 营收同比
            data["profit_yoy"] = _safe_float(row.get("netprofit_yoy"))  # 净利润同比
            data["debt_to_assets"] = _safe_float(row.get("debt_to_assets"))
            data["current_ratio"] = _safe_float(row.get("current_ratio"))
            data["ann_date"] = str(row.get("ann_date", ""))
            data["source"] = "tushare"
    except Exception:
        pass
    return data


def screen_stocks(industry_name, top_n=10, min_mkt_cap=50, min_turnover=2.0):
    """筛选板块内优质个股"""
    stocks = fetch_sector_stocks(industry_name, top_n=top_n * 3)
    if not stocks:
        return {"industry": industry_name, "error": "no stocks found"}

    # 筛选条件
    filtered = []
    for s in stocks:
        # 市值筛选（亿元）
        if s.get("mkt_cap") and s["mkt_cap"] < min_mkt_cap:
            continue
        # 换手率筛选
        if s.get("turnover_rate") and s["turnover_rate"] < min_turnover:
            continue
        filtered.append(s)

    if not filtered:
        # 如果筛选后为空，放宽条件
        filtered = stocks[:top_n]

    # 补充 Tushare 基本面数据（仅 Top N）
    token = get_tushare_token()
    if token:
        import tushare as ts
        pro = ts.pro_api(token)
        for s in filtered[:top_n]:
            # 东方财富代码转 Tushare 代码
            em_code = s["code"]
            if em_code.startswith("0") or em_code.startswith("3"):
                ts_code = em_code + ".SZ"
            elif em_code.startswith("6"):
                ts_code = em_code + ".SH"
            elif em_code.startswith("8") or em_code.startswith("4"):
                ts_code = em_code + ".BJ"
            else:
                continue
            fundamentals = fetch_stock_fundamentals(ts_code, pro)
            if fundamentals:
                s["fundamentals"] = fundamentals

    # 排序：按主力净流入
    filtered.sort(key=lambda x: x.get("main_net_flow", 0), reverse=True)

    return {
        "industry": industry_name,
        "total_fetched": len(stocks),
        "total_after_filter": len(filtered),
        "top_stocks": filtered[:top_n],
    }


def fetch_top_sectors_with_stocks(top_sectors=3, stocks_per_sector=5):
    """获取当日 Top N 行业板块及其龙头个股"""
    # 获取行业板块涨跌
    sector_url = "https://push2.eastmoney.com/api/qt/clist/get"
    sector_params = {
        "pn": "1", "pz": "50", "po": "1", "np": "1",
        "ut": EM_TOKEN, "fltt": "2", "invt": "2",
        "fid": "f3",  # 按涨跌幅排序
        "fs": "m:90+t:2",
        "fields": "f12,f14,f2,f3,f62",
    }
    sector_resp = _em_get_json(sector_url, sector_params)
    if not sector_resp or not sector_resp.get("data") or not sector_resp["data"].get("diff"):
        return []

    sectors = []
    for item in sector_resp["data"]["diff"][:top_sectors]:
        sector_name = item.get("f14", "")
        sector_pct = _safe_float(item.get("f3"))
        sector_code = item.get("f12")

        # 获取板块内个股
        stock_url = "https://push2.eastmoney.com/api/qt/clist/get"
        stock_params = {
            "pn": "1", "pz": str(stocks_per_sector * 2), "po": "1", "np": "1",
            "ut": EM_TOKEN, "fltt": "2", "invt": "2",
            "fid": "f62",
            "fs": f"b:{sector_code}+f:!50",
            "fields": "f12,f14,f2,f3,f6,f7,f8,f9,f20,f62,f184",
        }
        stock_resp = _em_get_json(stock_url, stock_params)

        stocks = []
        if stock_resp and stock_resp.get("data") and stock_resp["data"].get("diff"):
            for s in stock_resp["data"]["diff"]:
                name = s.get("f14", "")
                if any(k in name for k in ["ST", "退", "*ST"]):
                    continue
                mkt_cap = _safe_float(s.get("f20"))
                stocks.append({
                    "code": s.get("f12", ""),
                    "name": name,
                    "close": _safe_float(s.get("f2")),
                    "pct_chg": _safe_float(s.get("f3")),
                    "turnover": round(_safe_float(s.get("f6", 0)) / 1e8, 2),  # 成交额→亿元
                    "turnover_rate": _safe_float(s.get("f8")),
                    "pe": _safe_float(s.get("f9")),
                    "mkt_cap": round(mkt_cap / 1e8, 2) if mkt_cap else None,
                    "main_net_flow": round(_safe_float(s.get("f62", 0)) / 1e8, 2),
                    "source": "eastmoney",
                })

        # 补充 Tushare 基本面
        token = get_tushare_token()
        if token:
            import tushare as ts
            pro = ts.pro_api(token)
            for s in stocks[:stocks_per_sector]:
                em_code = s["code"]
                if em_code.startswith("0") or em_code.startswith("3"):
                    ts_code = em_code + ".SZ"
                elif em_code.startswith("6"):
                    ts_code = em_code + ".SH"
                elif em_code.startswith("8") or em_code.startswith("4"):
                    ts_code = em_code + ".BJ"
                else:
                    continue
                fundamentals = fetch_stock_fundamentals(ts_code, pro)
                if fundamentals:
                    s["fundamentals"] = fundamentals

        sectors.append({
            "name": sector_name,
            "pct_chg": sector_pct,
            "main_flow": round(_safe_float(item.get("f62", 0)) / 1e8, 2),
            "stocks": stocks[:stocks_per_sector],
        })

    return sectors


def main():
    parser = argparse.ArgumentParser(description="个股筛选脚本")
    parser.add_argument("--industry", type=str, help="行业名称（如：通信、电子）")
    parser.add_argument("--top", type=int, default=5, help="每个行业筛选个股数")
    parser.add_argument("--top-sectors", type=int, default=3, help="获取 Top N 行业")
    parser.add_argument("--min-cap", type=float, default=50, help="最小市值（亿元）")
    parser.add_argument("--min-turnover", type=float, default=2.0, help="最小换手率(%)")
    parser.add_argument("--output", default="-", help="输出文件路径")

    args = parser.parse_args()
    result = {}

    if args.industry:
        print(f">>> 筛选行业 [{args.industry}] 个股...", file=sys.stderr)
        result = screen_stocks(args.industry, top_n=args.top,
                               min_mkt_cap=args.min_cap, min_turnover=args.min_turnover)
    else:
        print(f">>> 获取 Top {args.top_sectors} 行业及龙头个股...", file=sys.stderr)
        result = {
            "sectors": fetch_top_sectors_with_stocks(
                top_sectors=args.top_sectors, stocks_per_sector=args.top),
        }

    result["timestamp"] = datetime.now().isoformat()
    output = json.dumps(result, ensure_ascii=False, indent=2, default=str)

    if args.output == "-":
        print(output)
    else:
        os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output)
        print(f">>> 个股筛选结果已保存到 {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
