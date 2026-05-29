#!/usr/bin/env python3
"""
每日市场晨报 - 数据采集脚本
支持：A股、港股、美股、宏观经济日历
数据源：Tushare + AKShare
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta

def get_tushare_token():
    """从环境变量或 Tushare 全局配置中获取 Token"""
    token = os.environ.get("TUSHARE_TOKEN")
    if token:
        return token
    # 尝试从 tushare 全局配置读取
    try:
        import tushare as ts
        token = ts.get_token()
        if token:
            return token
    except Exception:
        pass
    return None


def fetch_cn_market():
    """采集 A 股市场数据"""
    import tushare as ts
    import akshare as ak

    token = get_tushare_token()
    if not token:
        return {"error": "Tushare token not found"}

    pro = ts.pro_api(token)
    data = {}

    try:
        # 获取最近交易日
        today = datetime.now().strftime("%Y%m%d")
        yesterday = (datetime.now() - timedelta(days=3)).strftime("%Y%m%d")

        # 主要指数行情
        indices = {
            "000001.SH": "上证指数",
            "399001.SZ": "深证成指",
            "399006.SZ": "创业板指",
            "000016.SH": "上证50",
            "000905.SH": "中证500",
            "000852.SH": "中证1000",
        }
        index_data = []
        for ts_code, name in indices.items():
            try:
                df = pro.index_daily(ts_code=ts_code, start_date=yesterday, end_date=today)
                if df is not None and len(df) > 0:
                    row = df.iloc[0]
                    index_data.append({
                        "code": ts_code,
                        "name": name,
                        "close": round(float(row["close"]), 2),
                        "pct_chg": round(float(row["pct_chg"]), 2),
                        "amount": round(float(row["amount"]), 2),  # 千元
                        "vol": round(float(row["vol"]), 2),  # 手
                    })
            except Exception as e:
                index_data.append({"code": ts_code, "name": name, "error": str(e)})
        data["indices"] = index_data

        # 涨跌停统计
        try:
            # AKShare 涨跌停统计
            zt_df = ak.stock_zt_pool_em(date=today)
            dt_df = ak.stock_zt_pool_dtgc_em(date=today)
            data["limit_stats"] = {
                "limit_up_count": len(zt_df) if zt_df is not None else 0,
                "limit_down_count": len(dt_df) if dt_df is not None else 0,
                "date": today,
            }
        except Exception as e:
            data["limit_stats"] = {"error": str(e)}

        # 北向资金（使用 AKShare 历史数据接口）
        try:
            north_df = ak.stock_hsgt_hist_em(symbol="北向资金")
            if north_df is not None and len(north_df) > 0:
                # 取最近有数据的行（排除周末/假期 NaN）
                valid_rows = north_df.dropna(subset=["当日成交净买额"])
                if len(valid_rows) > 0:
                    latest = valid_rows.iloc[-1]
                    data["north_flow"] = {
                        "date": str(latest.get("日期", "")),
                        "net_flow": round(float(latest.get("当日成交净买额", 0)), 2),
                        "unit": "亿元",
                        "lead_stock": str(latest.get("领涨股", "")),
                    }
        except Exception as e:
            data["north_flow"] = {"error": str(e)}

        # 板块涨跌（申万一级行业，Tushare SW 指数）
        try:
            classify_df = pro.index_classify(level="L1", src="SW2021")
            if classify_df is not None and len(classify_df) > 0:
                industry_data = []
                for _, cls_row in classify_df.iterrows():
                    try:
                        sw_df = pro.sw_daily(
                            ts_code=cls_row["index_code"],
                            start_date=yesterday, end_date=today,
                        )
                        if sw_df is not None and len(sw_df) > 0:
                            row = sw_df.iloc[0]
                            industry_data.append({
                                "name": cls_row["industry_name"],
                                "close": round(float(row["close"]), 2),
                                "pct_chg": round(float(row["pct_change"]), 2),
                            })
                    except Exception:
                        continue
                if industry_data:
                    industry_data.sort(key=lambda x: x["pct_chg"], reverse=True)
                    data["sectors"] = {
                        "top": industry_data[:5],
                        "bottom": industry_data[-3:],
                    }
        except Exception as e:
            data["sectors"] = {"error": str(e)}

    except Exception as e:
        data["error"] = str(e)

    return data


def fetch_hk_market():
    """采集港股市场数据"""
    import akshare as ak

    data = {}
    try:
        # 恒生指数（使用新浪接口，东方财富接口易被限流）
        hk_index_df = ak.stock_hk_index_daily_sina(symbol="HSI")
        if hk_index_df is not None and len(hk_index_df) > 0:
            latest = hk_index_df.iloc[-1]
            prev = hk_index_df.iloc[-2] if len(hk_index_df) > 1 else latest
            data["hsi"] = {
                "close": round(float(latest["close"]), 2),
                "pct_chg": round((float(latest["close"]) / float(prev["close"]) - 1) * 100, 2),
                "date": str(latest["date"]),
            }

        # 恒生科技指数
        try:
            hk_tech_df = ak.stock_hk_index_daily_sina(symbol="HSTECH")
            if hk_tech_df is not None and len(hk_tech_df) > 0:
                latest = hk_tech_df.iloc[-1]
                prev = hk_tech_df.iloc[-2] if len(hk_tech_df) > 1 else latest
                data["hstech"] = {
                    "close": round(float(latest["close"]), 2),
                    "pct_chg": round((float(latest["close"]) / float(prev["close"]) - 1) * 100, 2),
                    "date": str(latest["date"]),
                }
        except Exception:
            pass  # 恒生科技指数新浪可能不支持

        # 南向资金
        try:
            south_df = ak.stock_hsgt_hist_em(symbol="南向资金")
            if south_df is not None and len(south_df) > 0:
                valid_rows = south_df.dropna(subset=["当日成交净买额"])
                if len(valid_rows) > 0:
                    latest = valid_rows.iloc[-1]
                    data["south_flow"] = {
                        "date": str(latest.get("日期", "")),
                        "net_flow": round(float(latest.get("当日成交净买额", 0)), 2),
                        "unit": "亿元",
                    }
        except Exception as e:
            data["south_flow"] = {"error": str(e)}

    except Exception as e:
        data["error"] = str(e)

    return data


def fetch_us_market():
    """采集美股市场数据"""
    import akshare as ak

    data = {}
    try:
        # 美股主要指数
        us_indices = {
            ".DJI": "道琼斯",
            ".IXIC": "纳斯达克",
            ".INX": "S&P500",
        }
        for symbol, name in us_indices.items():
            try:
                df = ak.index_us_stock_sina(symbol=symbol)
                if df is not None and len(df) > 0:
                    latest = df.iloc[-1]
                    prev = df.iloc[-2] if len(df) > 1 else latest
                    close_val = float(latest["close"])
                    prev_close = float(prev["close"])
                    data[name] = {
                        "close": round(close_val, 2),
                        "pct_chg": round((close_val / prev_close - 1) * 100, 2),
                    }
            except Exception:
                pass

        # VIX 恐慌指数
        try:
            vix_df = ak.option_current_em()
            if vix_df is not None:
                vix_row = vix_df[vix_df["名称"].str.contains("VIX")]
                if len(vix_row) > 0:
                    data["VIX"] = {"close": round(float(vix_row.iloc[0]["最新价"]), 2)}
        except Exception:
            pass

        # 美债收益率（通过宏观数据）
        try:
            bond_df = ak.macro_usa_tmc_yield()
            if bond_df is not None and len(bond_df) > 0:
                latest = bond_df.iloc[-1]
                data["us_10y_yield"] = {
                    "value": round(float(latest.iloc[1]), 3) if len(latest) > 1 else None,
                    "unit": "%",
                }
        except Exception:
            pass

    except Exception as e:
        data["error"] = str(e)

    return data


def fetch_calendar():
    """采集经济日历和重要事件"""
    import akshare as ak

    data = {}
    try:
        # 财经日历（百度财经）
        cal_df = ak.news_economic_baidu()
        if cal_df is not None and len(cal_df) > 0:
            today_str = datetime.now().strftime("%Y-%m-%d")
            week_later = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
            cal_df["日期"] = cal_df["日期"].astype(str)
            upcoming = cal_df[
                (cal_df["日期"] >= today_str) & (cal_df["日期"] <= week_later)
            ]
            # 只取重要性>=3的事件
            high_importance = upcoming[upcoming["重要性"].astype(int) >= 3] if len(upcoming) > 0 else upcoming
            events = []
            for _, row in (high_importance if len(high_importance) > 0 else upcoming).head(20).iterrows():
                events.append({
                    "date": row.get("日期", ""),
                    "time": row.get("时间", ""),
                    "country": row.get("地区", ""),
                    "event": row.get("事件", ""),
                    "importance": row.get("重要性", ""),
                    "actual": row.get("公布", ""),
                    "forecast": row.get("预期", ""),
                    "previous": row.get("前值", ""),
                })
            data["economic_events"] = events
    except Exception as e:
        data["economic_events"] = {"error": str(e)}

    # A股限售解禁
    try:
        unlock_df = ak.stock_restricted_release_queue_sina()
        if unlock_df is not None and len(unlock_df) > 0:
            data["restricted_unlock"] = unlock_df.head(10).to_dict("records")
    except Exception:
        pass

    return data


def main():
    parser = argparse.ArgumentParser(description="每日市场晨报数据采集")
    parser.add_argument("--market", choices=["cn", "hk", "us"], help="市场类型")
    parser.add_argument("--calendar", action="store_true", help="获取经济日历")
    parser.add_argument("--all", action="store_true", help="采集所有市场数据")
    parser.add_argument("--output", default="-", help="输出文件路径（默认stdout）")

    args = parser.parse_args()

    result = {}

    if args.all or args.market == "cn":
        print(">>> 采集A股数据...", file=sys.stderr)
        result["cn"] = fetch_cn_market()

    if args.all or args.market == "hk":
        print(">>> 采集港股数据...", file=sys.stderr)
        result["hk"] = fetch_hk_market()

    if args.all or args.market == "us":
        print(">>> 采集美股数据...", file=sys.stderr)
        result["us"] = fetch_us_market()

    if args.all or args.calendar:
        print(">>> 采集经济日历...", file=sys.stderr)
        result["calendar"] = fetch_calendar()

    # 如果没有指定任何参数，采集全部
    if not any([args.market, args.calendar, args.all]):
        print(">>> 采集全部数据...", file=sys.stderr)
        result["cn"] = fetch_cn_market()
        result["hk"] = fetch_hk_market()
        result["us"] = fetch_us_market()
        result["calendar"] = fetch_calendar()

    result["timestamp"] = datetime.now().isoformat()

    output = json.dumps(result, ensure_ascii=False, indent=2, default=str)

    if args.output == "-":
        print(output)
    else:
        os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output)
        print(f">>> 数据已保存到 {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
