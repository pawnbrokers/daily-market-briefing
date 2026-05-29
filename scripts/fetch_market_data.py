#!/usr/bin/env python3
"""
每日市场晨报 - 数据采集脚本
支持：A股、港股、美股、宏观经济日历

数据源优先级：
  主数据源：Tushare(A股) + 新浪HTTP(港股/美股实时) + 东方财富HTTP(涨跌停/板块/资金)
  兜底数据源：efinance(大陆+港股+美股实时) + yfinance(美股/港股补充)
  触发条件：主数据源返回空值、涨跌幅>15%、日期非最近交易日等异常时自动 fallback
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta

import requests

# ─── 通用工具 ──────────────────────────────────────────────

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
})
SESSION.verify = True

EM_TOKEN = "bd1d9ddb04089700cf9c27f6f7426281"

TIMEOUT = 15  # 秒


def get_tushare_token():
    """从环境变量或 .env 文件或 Tushare 全局配置中获取 Token"""
    token = os.environ.get("TUSHARE_TOKEN")
    if token:
        return token
    # 尝试从 .env 文件读取
    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    if os.path.exists(env_path):
        for line in open(env_path):
            line = line.strip()
            if line.startswith("TUSHARE_TOKEN="):
                token = line.split("=", 1)[1].strip()
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


def _safe_float(val, default=None):
    """安全转换为 float"""
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def is_data_stale(date_str, max_days=3):
    """判断数据日期是否过旧"""
    if not date_str:
        return True
    try:
        # 兼容多种日期格式
        for fmt in ("%Y%m%d", "%Y-%m-%d", "%Y/%m/%d"):
            try:
                d = datetime.strptime(str(date_str).split(" ")[0], fmt)
                break
            except ValueError:
                continue
        else:
            return True
        return (datetime.now() - d).days > max_days
    except Exception:
        return True


def is_pct_abnormal(pct_chg, threshold=15.0):
    """判断涨跌幅是否异常（单日涨跌超过阈值大概率是数据错误）"""
    v = _safe_float(pct_chg)
    return v is not None and abs(v) > threshold


# ─── 兜底数据源：efinance ──────────────────────────────────

def _efinance_fallback_index(codes_map):
    """使用 efinance 获取指数实时行情作为兜底"""
    try:
        import efinance as ef
    except ImportError:
        return {}
    result = {}
    try:
        # efinance 获取全部指数行情
        df = ef.stock.get_realtime_quotes()
        if df is None or len(df) == 0:
            return result
        for code, name in codes_map.items():
            row = df[df["股票代码"] == code]
            if len(row) > 0:
                r = row.iloc[0]
                result[name] = {
                    "close": _safe_float(r.get("最新价")),
                    "pct_chg": _safe_float(r.get("涨跌幅")),
                    "source": "efinance",
                }
    except Exception:
        pass
    return result


def _efinance_fallback_hk_us(market_type="hk"):
    """使用 efinance 获取港股/美股指数作为兜底"""
    try:
        import efinance as ef
    except ImportError:
        return {}
    result = {}
    try:
        if market_type == "hk":
            df = ef.stock.get_realtime_quotes(market="港股")
            if df is not None and len(df) > 0:
                for _, r in df.iterrows():
                    name = str(r.get("股票名称", ""))
                    if "恒生" in name:
                        result[name] = {
                            "close": _safe_float(r.get("最新价")),
                            "pct_chg": _safe_float(r.get("涨跌幅")),
                            "source": "efinance",
                        }
        elif market_type == "us":
            df = ef.stock.get_realtime_quotes(market="美股")
            if df is not None and len(df) > 0:
                for _, r in df.iterrows():
                    name = str(r.get("股票名称", ""))
                    if any(k in name for k in ["道琼斯", "纳斯达克", "标普", "S&P"]):
                        result[name] = {
                            "close": _safe_float(r.get("最新价")),
                            "pct_chg": _safe_float(r.get("涨跌幅")),
                            "source": "efinance",
                        }
    except Exception:
        pass
    return result


# ─── 兜底数据源：yfinance ──────────────────────────────────

def _yfinance_fallback(tickers_map):
    """使用 yfinance 获取数据作为兜底"""
    try:
        import yfinance as yf
    except ImportError:
        return {}
    result = {}
    for ticker, name in tickers_map.items():
        try:
            t = yf.Ticker(ticker)
            hist = t.history(period="5d")
            if hist is not None and len(hist) >= 2:
                latest = hist.iloc[-1]
                prev = hist.iloc[-2]
                close_val = _safe_float(latest["Close"])
                prev_close = _safe_float(prev["Close"])
                pct = round((close_val / prev_close - 1) * 100, 2) if close_val and prev_close else None
                result[name] = {
                    "close": close_val,
                    "pct_chg": pct,
                    "source": "yfinance",
                }
        except Exception:
            continue
    return result


# ─── 主数据源：新浪财经 HTTP ────────────────────────────────

def _sina_fetch(codes):
    """从新浪财经获取实时行情（A股/港股/美股通用）"""
    url = f"https://hq.sinajs.cn/list={','.join(codes)}"
    headers = {
        "Referer": "https://finance.sina.com.cn",
        "User-Agent": SESSION.headers["User-Agent"],
    }
    try:
        resp = requests.get(url, headers=headers, timeout=TIMEOUT)
        resp.encoding = "gbk"
        lines = resp.text.strip().split("\n")
        data = {}
        for line in lines:
            if '="' not in line:
                continue
            var_part, value_part = line.split('="', 1)
            code = var_part.split("hq_str_")[-1]
            value = value_part.rstrip('";')
            if not value:
                continue
            fields = value.split(",")
            data[code] = fields
        return data
    except Exception:
        return {}


# ─── 主数据源：东方财富 HTTP ────────────────────────────────

def _em_get_json(url, params=None):
    """东方财富 HTTP GET 返回 JSON"""
    try:
        resp = SESSION.get(url, params=params or {}, timeout=TIMEOUT)
        return resp.json()
    except Exception:
        return None


def _em_kline(secid, klt=101, limit=5):
    """东方财富 K 线数据"""
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    params = {
        "secid": secid,
        "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": klt,
        "fqt": "1",
        "end": "20500101",
        "lmt": limit,
        "ut": EM_TOKEN,
    }
    data = _em_get_json(url, params)
    if data and data.get("data") and data["data"].get("klines"):
        rows = []
        for line in data["data"]["klines"]:
            parts = line.split(",")
            rows.append({
                "date": parts[0],
                "open": _safe_float(parts[1]),
                "close": _safe_float(parts[2]),
                "high": _safe_float(parts[3]),
                "low": _safe_float(parts[4]),
                "vol": _safe_float(parts[5]),
                "turnover": _safe_float(parts[6]),
                "amplitude": _safe_float(parts[7]),
                "pct_chg": _safe_float(parts[8]),
                "change_amt": _safe_float(parts[9]),
                "turnover_rate": _safe_float(parts[10]),
            })
        return rows
    return []


# ─── A 股市场 ──────────────────────────────────────────────

def fetch_cn_market():
    """采集 A 股市场数据（Tushare 主 + 东方财富 HTTP 兜底）"""
    import tushare as ts

    token = get_tushare_token()
    if not token:
        return {"error": "Tushare token not found"}

    pro = ts.pro_api(token)
    data = {}

    try:
        today = datetime.now().strftime("%Y%m%d")
        yesterday = (datetime.now() - timedelta(days=3)).strftime("%Y%m%d")

        # ── 1. 主要指数行情 ──
        indices = {
            "000001.SH": "上证指数",
            "399001.SZ": "深证成指",
            "399006.SZ": "创业板指",
            "000016.SH": "上证50",
            "000905.SH": "中证500",
            "000852.SH": "中证1000",
        }
        index_data = []
        need_fallback = []

        for ts_code, name in indices.items():
            try:
                df = pro.index_daily(ts_code=ts_code, start_date=yesterday, end_date=today)
                if df is not None and len(df) > 0:
                    row = df.iloc[0]
                    close = _safe_float(row["close"])
                    pct = _safe_float(row["pct_chg"])
                    trade_date = str(row.get("trade_date", ""))
                    item = {
                        "code": ts_code, "name": name,
                        "close": close, "pct_chg": pct,
                        "amount": round(_safe_float(row["amount"], 0), 2),
                        "vol": round(_safe_float(row["vol"], 0), 2),
                        "trade_date": trade_date,
                        "source": "tushare",
                    }
                    # 数据异常检测
                    if is_data_stale(trade_date) or is_pct_abnormal(pct):
                        item["warning"] = "data_may_be_stale_or_abnormal"
                        need_fallback.append((ts_code, name))
                    index_data.append(item)
                else:
                    index_data.append({"code": ts_code, "name": name, "error": "no_data"})
                    need_fallback.append((ts_code, name))
            except Exception as e:
                index_data.append({"code": ts_code, "name": name, "error": str(e)})
                need_fallback.append((ts_code, name))

        # 兜底：东方财富 K 线
        if need_fallback:
            em_secid_map = {
                "000001.SH": "1.000001", "399001.SZ": "0.399001",
                "399006.SZ": "0.399006", "000016.SH": "1.000016",
                "000905.SH": "1.000905", "000852.SH": "1.000852",
            }
            for ts_code, name in need_fallback:
                secid = em_secid_map.get(ts_code)
                if not secid:
                    continue
                rows = _em_kline(secid, limit=3)
                if rows:
                    latest = rows[-1]
                    # 更新已有条目
                    for item in index_data:
                        if item.get("code") == ts_code and ("error" in item or "warning" in item):
                            item["close"] = latest["close"]
                            item["pct_chg"] = latest["pct_chg"]
                            item["trade_date"] = latest["date"]
                            item["source"] = "eastmoney_fallback"
                            item.pop("error", None)
                            item.pop("warning", None)
                            break

        data["indices"] = index_data

        # ── 2. 涨跌停统计（东方财富 HTTP） ──
        try:
            zt_url = "https://push2ex.eastmoney.com/getTopicZTPool"
            zt_params = {
                "ut": EM_TOKEN, "dpt": "wz.ztzt",
                "Pageindex": "0", "pagesize": "500",
                "sort": "fbt:asc", "date": today,
            }
            zt_resp = _em_get_json(zt_url, zt_params)
            zt_count = 0
            if zt_resp and isinstance(zt_resp, dict):
                zt_count = len((zt_resp.get("data") or {}).get("pool", []))

            dt_url = "https://push2ex.eastmoney.com/getTopicDTPool"
            dt_params = {
                "ut": EM_TOKEN, "dpt": "wz.ztzt",
                "Pageindex": "0", "pagesize": "500",
                "sort": "fund:asc", "date": today,
            }
            dt_resp = _em_get_json(dt_url, dt_params)
            dt_count = 0
            if dt_resp and isinstance(dt_resp, dict):
                dt_count = len((dt_resp.get("data") or {}).get("pool", []))

            data["limit_stats"] = {
                "limit_up_count": zt_count,
                "limit_down_count": dt_count,
                "date": today,
                "source": "eastmoney",
            }
        except Exception as e:
            data["limit_stats"] = {"error": str(e)}

        # ── 3. 北向资金（Tushare moneyflow_hsgt） ──
        try:
            hsgt_df = pro.moneyflow_hsgt(start_date=yesterday, end_date=today)
            if hsgt_df is not None and len(hsgt_df) > 0:
                latest = hsgt_df.iloc[0]
                north_val = _safe_float(latest.get("north_money"))
                data["north_flow"] = {
                    "date": str(latest.get("trade_date", "")),
                    "net_flow": round(north_val / 100, 2) if north_val else None,  # 百万元 -> 亿元
                    "hgt": round(_safe_float(latest.get("hgt"), 0) / 100, 2),
                    "sgt": round(_safe_float(latest.get("sgt"), 0) / 100, 2),
                    "unit": "亿元",
                    "source": "tushare",
                }
                # 异常检测
                if north_val and abs(north_val / 100) > 200:
                    data["north_flow"]["note"] = "单日北向资金超200亿，请交叉验证"
            else:
                data["north_flow"] = {"error": "no_data"}
        except Exception as e:
            data["north_flow"] = {"error": str(e)}

        # ── 4. 板块涨跌（Tushare 申万一级行业为主，东方财富补充） ──
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
                                "close": round(_safe_float(row["close"], 0), 2),
                                "pct_chg": round(_safe_float(row["pct_change"], 0), 2),
                                "source": "tushare_sw",
                            })
                    except Exception:
                        continue
                if industry_data:
                    industry_data.sort(key=lambda x: x.get("pct_chg", 0) or 0, reverse=True)
                    data["sectors"] = {
                        "top": industry_data[:5],
                        "bottom": industry_data[-3:],
                    }
        except Exception as e:
            data["sectors"] = {"error": str(e)}

        # 东方财富行业板块资金流向（补充资金数据，可能被限流）
        try:
            sector_url = "https://push2.eastmoney.com/api/qt/clist/get"
            sector_params = {
                "pn": "1", "pz": "50", "po": "1", "np": "1",
                "ut": EM_TOKEN, "fltt": "2", "invt": "2",
                "fid": "f62",
                "fs": "m:90+t:2",
                "fields": "f12,f14,f2,f3,f62,f184,f66,f69,f72,f75,f78,f81,f84,f87",
            }
            sector_resp = _em_get_json(sector_url, sector_params)
            em_industry = []
            if sector_resp and isinstance(sector_resp, dict) and sector_resp.get("data") and sector_resp["data"].get("diff"):
                for item in sector_resp["data"]["diff"]:
                    pct = _safe_float(item.get("f3"))
                    main_flow = _safe_float(item.get("f62"))
                    em_industry.append({
                        "name": item.get("f14", ""),
                        "close": _safe_float(item.get("f2")),
                        "pct_chg": pct,
                        "main_net_flow": round(main_flow / 1e8, 2) if main_flow else 0,
                        "source": "eastmoney",
                    })
            if em_industry:
                data["em_sectors"] = em_industry  # 作为资金流向补充
        except Exception:
            pass  # 东方财富接口被限流时静默跳过

    except Exception as e:
        data["error"] = str(e)

    return data


# ─── 港股市场 ──────────────────────────────────────────────

def fetch_hk_market():
    """采集港股市场数据（新浪实时 + 东方财富K线 + Tushare南向资金）"""
    data = {}

    try:
        # ── 1. 恒生指数 & 恒生科技（新浪实时） ──
        sina_codes = ["rt_hkHSI", "rt_hkHSTECH"]
        sina_data = _sina_fetch(sina_codes)

        # 恒生指数
        hsi_fields = sina_data.get("rt_hkHSI", [])
        if len(hsi_fields) >= 8:
            cur = _safe_float(hsi_fields[4])
            prev = _safe_float(hsi_fields[3])
            pct = round((cur / prev - 1) * 100, 2) if cur and prev and prev > 0 else None
            data["hsi"] = {
                "close": cur,
                "pct_chg": pct,
                "date": hsi_fields[21] if len(hsi_fields) > 21 else "",
                "source": "sina",
            }
            if pct is not None and is_pct_abnormal(pct):
                data["hsi"]["warning"] = "pct_abnormal"
        else:
            # 兜底：东方财富 K 线
            rows = _em_kline("100.HSI", limit=3)
            if rows:
                data["hsi"] = {
                    "close": rows[-1]["close"],
                    "pct_chg": rows[-1]["pct_chg"],
                    "date": rows[-1]["date"],
                    "source": "eastmoney_fallback",
                }

        # 恒生科技
        hstech_fields = sina_data.get("rt_hkHSTECH", [])
        if len(hstech_fields) >= 8:
            cur = _safe_float(hstech_fields[4])
            prev = _safe_float(hstech_fields[3])
            pct = round((cur / prev - 1) * 100, 2) if cur and prev and prev > 0 else None
            data["hstech"] = {
                "close": cur,
                "pct_chg": pct,
                "date": hstech_fields[21] if len(hstech_fields) > 21 else "",
                "source": "sina",
            }
        else:
            rows = _em_kline("100.HSTECH", limit=3)
            if rows:
                data["hstech"] = {
                    "close": rows[-1]["close"],
                    "pct_chg": rows[-1]["pct_chg"],
                    "date": rows[-1]["date"],
                    "source": "eastmoney_fallback",
                }

        # efinance 兜底
        if not data.get("hsi") or "warning" in data.get("hsi", {}):
            fb = _efinance_fallback_hk_us("hk")
            if fb and not data.get("hsi"):
                data["hsi"] = fb.popitem()[1] if fb else None
            if fb and not data.get("hstech"):
                data["hstech"] = fb.popitem()[1] if fb else None

        # yfinance 兜底
        if not data.get("hsi") or not data.get("hstech"):
            yf_map = {}
            if not data.get("hsi"):
                yf_map["^HSI"] = "恒生指数"
            if not data.get("hstech"):
                yf_map["^HSTECH"] = "恒生科技"
            if yf_map:
                yf_result = _yfinance_fallback(yf_map)
                for name, val in yf_result.items():
                    if name == "恒生指数" and not data.get("hsi"):
                        data["hsi"] = val
                    elif name == "恒生科技" and not data.get("hstech"):
                        data["hstech"] = val

        # ── 2. 南向资金（Tushare moneyflow_hsgt） ──
        try:
            import tushare as ts
            token = get_tushare_token()
            if token:
                pro = ts.pro_api(token)
                yesterday = (datetime.now() - timedelta(days=3)).strftime("%Y%m%d")
                today = datetime.now().strftime("%Y%m%d")
                hsgt_df = pro.moneyflow_hsgt(start_date=yesterday, end_date=today)
                if hsgt_df is not None and len(hsgt_df) > 0:
                    latest = hsgt_df.iloc[0]
                    south_val = _safe_float(latest.get("south_money"))
                    data["south_flow"] = {
                        "date": str(latest.get("trade_date", "")),
                        "net_flow": round(south_val / 100, 2) if south_val else None,  # 百万元 -> 亿元
                        "ggt_ss": round(_safe_float(latest.get("ggt_ss"), 0) / 100, 2),
                        "ggt_sz": round(_safe_float(latest.get("ggt_sz"), 0) / 100, 2),
                        "unit": "亿元",
                        "source": "tushare",
                    }
        except Exception as e:
            data["south_flow"] = {"error": str(e)}

    except Exception as e:
        data["error"] = str(e)

    return data


# ─── 美股市场 ──────────────────────────────────────────────

def fetch_us_market():
    """采集美股市场数据（新浪实时 + 东方财富K线 + yfinance兜底）"""
    data = {}

    try:
        # ── 1. 美股主要指数（新浪实时） ──
        sina_codes = ["gb_$dji", "gb_ixic", "gb_$inx"]
        sina_data = _sina_fetch(sina_codes)

        us_index_map = {
            "gb_$dji": "道琼斯",
            "gb_ixic": "纳斯达克",
            "gb_$inx": "S&P500",
        }
        need_yf = {}

        for code, name in us_index_map.items():
            fields = sina_data.get(code, [])
            if len(fields) >= 8:
                cur = _safe_float(fields[1])
                pct = _safe_float(fields[2])
                data[name] = {
                    "close": cur,
                    "pct_chg": pct,
                    "date": fields[3] if len(fields) > 3 else "",
                    "source": "sina",
                }
                if is_pct_abnormal(pct):
                    data[name]["warning"] = "pct_abnormal"
                    need_yf[code] = name
            else:
                need_yf[code] = name

        # 东方财富 K 线兜底
        em_secid_map = {
            "gb_$dji": "105.DJIA",
            "gb_ixic": "105.NDX",
            "gb_$inx": "105.SPX",
        }
        for code, name in list(need_yf.items()):
            secid = em_secid_map.get(code)
            if secid:
                rows = _em_kline(secid, limit=3)
                if rows:
                    data[name] = {
                        "close": rows[-1]["close"],
                        "pct_chg": rows[-1]["pct_chg"],
                        "date": rows[-1]["date"],
                        "source": "eastmoney_fallback",
                    }
                    need_yf.pop(code, None)

        # yfinance 兜底
        yf_ticker_map = {
            "gb_$dji": "^DJI",
            "gb_ixic": "^IXIC",
            "gb_$inx": "^GSPC",
        }
        if need_yf:
            yf_map = {yf_ticker_map[k]: v for k, v in need_yf.items() if k in yf_ticker_map}
            if yf_map:
                yf_result = _yfinance_fallback(yf_map)
                for name, val in yf_result.items():
                    if name not in data or "warning" in data.get(name, {}):
                        data[name] = val

        # ── 2. VIX 恐慌指数（yfinance，最可靠） ──
        try:
            yf_result = _yfinance_fallback({"^VIX": "VIX"})
            if "VIX" in yf_result:
                data["VIX"] = yf_result["VIX"]
        except Exception:
            pass

        # ── 3. 美债收益率（yfinance） ──
        try:
            yf_result = _yfinance_fallback({"^TNX": "US_10Y"})
            if "US_10Y" in yf_result:
                close = yf_result["US_10Y"]["close"]
                data["us_10y_yield"] = {
                    "value": round(close, 3) if close else None,
                    "unit": "%",
                    "source": "yfinance",
                }
        except Exception:
            pass

        # ── 4. 美元指数（yfinance） ──
        try:
            yf_result = _yfinance_fallback({"DX-Y.NYB": "DXY"})
            if "DXY" in yf_result:
                data["dxy"] = yf_result["DXY"]
        except Exception:
            pass

    except Exception as e:
        data["error"] = str(e)

    return data


# ─── 经济日历 ──────────────────────────────────────────────

def fetch_calendar():
    """采集经济日历和重要事件"""
    data = {}

    # ── 1. Tushare 中国经济数据发布日程 ──
    try:
        import tushare as ts
        token = get_tushare_token()
        if token:
            pro = ts.pro_api(token)
            now = datetime.now()
            for m_offset in range(2):
                m = (now.replace(day=1) + timedelta(days=32 * m_offset)).strftime("%Y%m")
                try:
                    sched_df = pro.cn_schedule(m=m)
                    if sched_df is not None and len(sched_df) > 0:
                        events = []
                        for _, row in sched_df.iterrows():
                            events.append({
                                "date": str(row.get("publish_date", "")),
                                "event": str(row.get("title", "")),
                                "issuer": str(row.get("issuing_org", "")),
                                "api": str(row.get("data_api", "")),
                            })
                        if events:
                            data.setdefault("cn_economic_schedule", []).extend(events)
                except Exception:
                    continue
    except Exception as e:
        data["cn_economic_schedule"] = {"error": str(e)}

    # ── 2. 东方财富财经日历 ──
    try:
        cal_url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
        today_str = datetime.now().strftime("%Y-%m-%d")
        week_later = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
        cal_params = {
            "reportName": "RPT_ECO_DATA_CALENDAR",
            "columns": "ALL",
            "filter": f'(PUBLISH_DATE>="{today_str}")(PUBLISH_DATE<="{week_later}")',
            "pageNumber": "1",
            "pageSize": "50",
            "sortColumns": "PUBLISH_DATE",
            "sortTypes": "1",
            "source": "WEB",
            "client": "WEB",
        }
        cal_resp = _em_get_json(cal_url, cal_params)
        if cal_resp and cal_resp.get("result") and cal_resp["result"].get("data"):
            events = []
            for item in cal_resp["result"]["data"]:
                events.append({
                    "date": item.get("PUBLISH_DATE", ""),
                    "country": item.get("COUNTRY", ""),
                    "event": item.get("INDICATOR_NAME", ""),
                    "importance": item.get("IMPORTANCE_LEVEL", ""),
                    "actual": item.get("ACTUAL_VALUE", ""),
                    "forecast": item.get("FORECAST_VALUE", ""),
                    "previous": item.get("PREVIOUS_VALUE", ""),
                })
            data["economic_events"] = events
    except Exception as e:
        data["economic_events"] = {"error": str(e)}

    # ── 3. 交易日历（Tushare） ──
    try:
        import tushare as ts
        token = get_tushare_token()
        if token:
            pro = ts.pro_api(token)
            start = datetime.now().strftime("%Y%m%d")
            end = (datetime.now() + timedelta(days=14)).strftime("%Y%m%d")
            cal_df = pro.trade_cal(exchange="SSE", start_date=start, end_date=end, is_open="1")
            if cal_df is not None and len(cal_df) > 0:
                data["trade_days"] = [str(r["cal_date"]) for _, r in cal_df.iterrows()]
    except Exception:
        pass

    return data


# ─── 主入口 ────────────────────────────────────────────────

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

    # 统计数据源使用情况
    source_stats = {"tushare": 0, "sina": 0, "eastmoney": 0, "efinance": 0, "yfinance": 0, "error": 0}
    for market_key in ["cn", "hk", "us"]:
        market_data = result.get(market_key, {})
        if isinstance(market_data, dict):
            for k, v in market_data.items():
                if isinstance(v, dict):
                    src = v.get("source", "unknown")
                    if "error" in v:
                        source_stats["error"] += 1
                    elif "tushare" in src:
                        source_stats["tushare"] += 1
                    elif "sina" in src:
                        source_stats["sina"] += 1
                    elif "eastmoney" in src:
                        source_stats["eastmoney"] += 1
                    elif "efinance" in src:
                        source_stats["efinance"] += 1
                    elif "yfinance" in src:
                        source_stats["yfinance"] += 1
                elif isinstance(v, list):
                    for item in v:
                        if isinstance(item, dict):
                            src = item.get("source", "unknown")
                            if "error" in item:
                                source_stats["error"] += 1
                            elif "tushare" in src:
                                source_stats["tushare"] += 1
                            elif "sina" in src:
                                source_stats["sina"] += 1
                            elif "eastmoney" in src:
                                source_stats["eastmoney"] += 1
                            elif "efinance" in src:
                                source_stats["efinance"] += 1
                            elif "yfinance" in src:
                                source_stats["yfinance"] += 1
    result["source_stats"] = source_stats

    output = json.dumps(result, ensure_ascii=False, indent=2, default=str)

    if args.output == "-":
        print(output)
    else:
        os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output)
        print(f">>> 数据已保存到 {args.output}", file=sys.stderr)
        print(f">>> 数据源统计: {source_stats}", file=sys.stderr)


if __name__ == "__main__":
    main()
