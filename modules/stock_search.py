"""
stock_search.py — 股票搜索模块
基于东方财富/腾讯等公开API实现实时搜索
增强：跨市场别名匹配、拼音首字母搜索
"""

import requests
import pandas as pd
from typing import List, Dict, Optional
from dataclasses import dataclass, field
import json
import time
import re


# ─── 跨市场别名字典 ──────────────────────────────────────────────────────────
# 格式: full_code -> {name, aliases: [中文名, 拼音首字母, 常见简称...]}
CROSS_MARKET_ALIASES: Dict[str, Dict] = {
    # ── 美股科技 ──
    "AAPL":      {"name": "苹果", "market": "美股", "aliases": ["苹果", "pg", "apple", "苹果公司"]},
    "MSFT":      {"name": "微软", "market": "美股", "aliases": ["微软", "wr", "microsoft"]},
    "GOOGL":     {"name": "谷歌A", "market": "美股", "aliases": ["谷歌", "gg", "google", "alphabet"]},
    "GOOG":      {"name": "谷歌C", "market": "美股", "aliases": ["谷歌c", "google c"]},
    "AMZN":      {"name": "亚马逊", "market": "美股", "aliases": ["亚马逊", "ymx", "amazon"]},
    "META":      {"name": "Meta", "market": "美股", "aliases": ["脸书", "facebook", "fb", "meta", "ls"]},
    "TSLA":      {"name": "特斯拉", "market": "美股", "aliases": ["特斯拉", "tsl", "tesla"]},
    "NVDA":      {"name": "英伟达", "market": "美股", "aliases": ["英伟达", "ywd", "nvidia", "老黄"]},
    "AMD":       {"name": "AMD", "market": "美股", "aliases": ["amd", "超微半导体", "cwbdt"]},
    "INTC":      {"name": "英特尔", "market": "美股", "aliases": ["英特尔", "yte", "intel"]},
    "NFLX":      {"name": "奈飞", "market": "美股", "aliases": ["奈飞", "nf", "netflix"]},
    "CRM":       {"name": "赛富时", "market": "美股", "aliases": ["赛富时", "sfs", "salesforce"]},
    "ORCL":      {"name": "甲骨文", "market": "美股", "aliases": ["甲骨文", "jgw", "oracle"]},
    "ADBE":      {"name": "Adobe", "market": "美股", "aliases": ["adobe", "奥多比"]},
    "AVGO":      {"name": "博通", "market": "美股", "aliases": ["博通", "bt", "broadcom"]},
    "QCOM":      {"name": "高通", "market": "美股", "aliases": ["高通", "gt", "qualcomm"]},
    "MU":        {"name": "美光", "market": "美股", "aliases": ["美光", "mg", "micron"]},
    "TSM":       {"name": "台积电", "market": "美股", "aliases": ["台积电", "tjd", "tsmc"]},
    "ASML":      {"name": "阿斯麦", "market": "美股", "aliases": ["阿斯麦", "asm", "asml", "光刻机"]},
    # ── 美股金融/消费/其他 ──
    "BRK-B":     {"name": "伯克希尔B", "market": "美股", "aliases": ["伯克希尔", "巴菲特", "bft", "berkshire"]},
    "JPM":       {"name": "摩根大通", "market": "美股", "aliases": ["摩根大通", "mgdt", "jpmorgan"]},
    "V":         {"name": "Visa", "market": "美股", "aliases": ["visa", "维萨"]},
    "MA":        {"name": "万事达", "market": "美股", "aliases": ["万事达", "wsd", "mastercard"]},
    "WMT":       {"name": "沃尔玛", "market": "美股", "aliases": ["沃尔玛", "wem", "walmart"]},
    "KO":        {"name": "可口可乐", "market": "美股", "aliases": ["可口可乐", "kkkl", "cocacola", "coca"]},
    "PEP":       {"name": "百事", "market": "美股", "aliases": ["百事", "bs", "pepsi"]},
    "DIS":       {"name": "迪士尼", "market": "美股", "aliases": ["迪士尼", "dsn", "disney"]},
    "NKE":       {"name": "耐克", "market": "美股", "aliases": ["耐克", "nk", "nike"]},
    "BA":        {"name": "波音", "market": "美股", "aliases": ["波音", "by", "boeing"]},
    "XOM":       {"name": "埃克森美孚", "market": "美股", "aliases": ["埃克森美孚", "eksm", "exxon"]},
    "UNH":       {"name": "联合健康", "market": "美股", "aliases": ["联合健康", "lhjk", "unitedhealth"]},
    "JNJ":       {"name": "强生", "market": "美股", "aliases": ["强生", "qs", "johnson"]},
    "PFE":       {"name": "辉瑞", "market": "美股", "aliases": ["辉瑞", "hr", "pfizer"]},
    "LLY":       {"name": "礼来", "market": "美股", "aliases": ["礼来", "ll", "lilly", "减肥药"]},
    "COIN":      {"name": "Coinbase", "market": "美股", "aliases": ["coinbase", "cb"]},
    "PLTR":      {"name": "Palantir", "market": "美股", "aliases": ["palantir", "大数据"]},
    "SNOW":      {"name": "Snowflake", "market": "美股", "aliases": ["snowflake", "雪花"]},
    # ── 中概股 ──
    "BABA":      {"name": "阿里巴巴", "market": "美股", "aliases": ["阿里巴巴", "阿里", "al", "alibaba"]},
    "JD":        {"name": "京东", "market": "美股", "aliases": ["京东", "jd"]},
    "PDD":       {"name": "拼多多", "market": "美股", "aliases": ["拼多多", "pdd", "temu"]},
    "BIDU":      {"name": "百度", "market": "美股", "aliases": ["百度", "bd", "baidu"]},
    "NIO":       {"name": "蔚来", "market": "美股", "aliases": ["蔚来", "wl", "nio"]},
    "XPEV":      {"name": "小鹏汽车", "market": "美股", "aliases": ["小鹏", "xp", "xpeng"]},
    "LI":        {"name": "理想汽车", "market": "美股", "aliases": ["理想", "lx", "理想汽车"]},
    "BILI":      {"name": "哔哩哔哩", "market": "美股", "aliases": ["b站", "哔哩哔哩", "bilibili", "blbl"]},
    "TME":       {"name": "腾讯音乐", "market": "美股", "aliases": ["腾讯音乐", "txyy"]},
    "FUTU":      {"name": "富途控股", "market": "美股", "aliases": ["富途", "ft", "futu"]},
    # ── 港股 ──
    "0700.HK":   {"name": "腾讯控股", "market": "港股", "aliases": ["腾讯", "tx", "tencent", "腾讯控股"]},
    "3690.HK":   {"name": "美团-W", "market": "港股", "aliases": ["美团", "mt", "meituan"]},
    "9999.HK":   {"name": "网易-S", "market": "港股", "aliases": ["网易", "wy", "netease"]},
    "9988.HK":   {"name": "阿里巴巴-W", "market": "港股", "aliases": ["阿里港股", "alibaba hk"]},
    "9618.HK":   {"name": "京东集团-SW", "market": "港股", "aliases": ["京东港股", "jd hk"]},
    "1810.HK":   {"name": "小米集团-W", "market": "港股", "aliases": ["小米", "xm", "xiaomi"]},
    "2318.HK":   {"name": "中国平安", "market": "港股", "aliases": ["平安港股", "中国平安港股"]},
    "0005.HK":   {"name": "汇丰控股", "market": "港股", "aliases": ["汇丰", "hf", "hsbc"]},
    "1211.HK":   {"name": "比亚迪股份", "market": "港股", "aliases": ["比亚迪港股", "byd hk"]},
    "2020.HK":   {"name": "安踏体育", "market": "港股", "aliases": ["安踏", "at", "anta"]},
    "9626.HK":   {"name": "哔哩哔哩-W", "market": "港股", "aliases": ["b站港股", "bilibili hk"]},
    "0388.HK":   {"name": "香港交易所", "market": "港股", "aliases": ["港交所", "gjs", "hkex"]},
    "2269.HK":   {"name": "药明生物", "market": "港股", "aliases": ["药明生物", "ymsw"]},
    "0981.HK":   {"name": "中芯国际", "market": "港股", "aliases": ["中芯港股", "smic hk"]},
    # ── A股热门 ──
    "600519.SS": {"name": "贵州茅台", "market": "A股-上海", "aliases": ["茅台", "mt", "maotai", "贵州茅台"]},
    "000858.SZ": {"name": "五粮液", "market": "A股-深圳", "aliases": ["五粮液", "wly"]},
    "000333.SZ": {"name": "美的集团", "market": "A股-深圳", "aliases": ["美的", "md", "midea"]},
    "300750.SZ": {"name": "宁德时代", "market": "A股-深圳", "aliases": ["宁德", "nd", "catl", "宁德时代"]},
    "601318.SS": {"name": "中国平安", "market": "A股-上海", "aliases": ["平安", "pa", "pingan", "中国平安"]},
    "600036.SS": {"name": "招商银行", "market": "A股-上海", "aliases": ["招行", "zh", "cmb", "招商银行"]},
    "002594.SZ": {"name": "比亚迪", "market": "A股-深圳", "aliases": ["比亚迪", "byd"]},
    "601012.SS": {"name": "隆基绿能", "market": "A股-上海", "aliases": ["隆基", "lj", "longi"]},
    "002415.SZ": {"name": "海康威视", "market": "A股-深圳", "aliases": ["海康", "hk", "hikvision"]},
    "300059.SZ": {"name": "东方财富", "market": "A股-深圳", "aliases": ["东财", "dc", "eastmoney"]},
    "600030.SS": {"name": "中信证券", "market": "A股-上海", "aliases": ["中信", "zx", "citic"]},
    "601398.SS": {"name": "工商银行", "market": "A股-上海", "aliases": ["工行", "gh", "icbc"]},
    "601288.SS": {"name": "农业银行", "market": "A股-上海", "aliases": ["农行", "nh", "abc"]},
    "600276.SS": {"name": "恒瑞医药", "market": "A股-上海", "aliases": ["恒瑞", "hr", "hengrui"]},
    "688981.SS": {"name": "中芯国际", "market": "A股-科创", "aliases": ["中芯", "zx", "smic"]},
    "600809.SS": {"name": "山西汾酒", "market": "A股-上海", "aliases": ["汾酒", "fj"]},
    "600887.SS": {"name": "伊利股份", "market": "A股-上海", "aliases": ["伊利", "yl", "yili"]},
    "601888.SS": {"name": "中国中免", "market": "A股-上海", "aliases": ["中免", "zm", "免税"]},
    "601899.SS": {"name": "紫金矿业", "market": "A股-上海", "aliases": ["紫金", "zj", "zijin"]},
    "002230.SZ": {"name": "科大讯飞", "market": "A股-深圳", "aliases": ["讯飞", "xf", "iflytek"]},
    "300274.SZ": {"name": "阳光电源", "market": "A股-深圳", "aliases": ["阳光电源", "ygdy"]},
    "600900.SS": {"name": "长江电力", "market": "A股-上海", "aliases": ["长电", "cd", "长江电力"]},
    "603288.SS": {"name": "海天味业", "market": "A股-上海", "aliases": ["海天", "ht", "haitian"]},
    "300760.SZ": {"name": "迈瑞医疗", "market": "A股-深圳", "aliases": ["迈瑞", "mr", "mindray"]},
    "600436.SS": {"name": "片仔癀", "market": "A股-上海", "aliases": ["片仔癀", "pzh"]},
    "000651.SZ": {"name": "格力电器", "market": "A股-深圳", "aliases": ["格力", "gl", "gree"]},
    "600690.SS": {"name": "海尔智家", "market": "A股-上海", "aliases": ["海尔", "he", "haier"]},
    # ── 指数/ETF ──
    "510300.SS": {"name": "沪深300ETF", "market": "ETF-上海", "aliases": ["沪深300", "hs300", "300etf"]},
    "510050.SS": {"name": "上证50ETF", "market": "ETF-上海", "aliases": ["上证50", "sz50", "50etf"]},
    "159915.SZ": {"name": "创业板ETF", "market": "ETF-深圳", "aliases": ["创业板", "cyb", "创业板etf"]},
    "512000.SS": {"name": "券商ETF", "market": "ETF-上海", "aliases": ["券商etf", "qs"]},
    "518880.SS": {"name": "黄金ETF", "market": "ETF-上海", "aliases": ["黄金etf", "hj"]},
    "SPY":       {"name": "标普500ETF", "market": "美股ETF", "aliases": ["spy", "标普500"]},
    "QQQ":       {"name": "纳指100ETF", "market": "美股ETF", "aliases": ["qqq", "纳指100", "纳指etf"]},
}


def _build_alias_index() -> Dict[str, List[str]]:
    """构建反向别名索引: alias_lower -> [full_code, ...]"""
    index = {}
    for full_code, info in CROSS_MARKET_ALIASES.items():
        # 索引股票名称
        name_lower = info["name"].lower()
        index.setdefault(name_lower, []).append(full_code)
        # 索引所有别名
        for alias in info.get("aliases", []):
            alias_lower = alias.lower()
            index.setdefault(alias_lower, []).append(full_code)
        # 索引代码本身（不含后缀）
        code_only = full_code.split(".")[0].lower()
        index.setdefault(code_only, []).append(full_code)
    return index

_ALIAS_INDEX = _build_alias_index()


def _fuzzy_match_aliases(keyword: str) -> List[tuple]:
    """
    模糊匹配别名字典，返回 [(full_code, score), ...]
    score: 100=精确匹配, 80=别名精确, 60=前缀匹配, 40=包含匹配
    """
    kw = keyword.lower().strip()
    if not kw:
        return []

    matches = {}  # full_code -> best_score

    # 1. 精确匹配别名索引
    if kw in _ALIAS_INDEX:
        for fc in _ALIAS_INDEX[kw]:
            matches[fc] = max(matches.get(fc, 0), 100)

    # 2. 前缀匹配 & 包含匹配
    for alias_key, full_codes in _ALIAS_INDEX.items():
        if alias_key == kw:
            continue  # already handled
        if alias_key.startswith(kw):
            for fc in full_codes:
                matches[fc] = max(matches.get(fc, 0), 60)
        elif kw in alias_key:
            for fc in full_codes:
                matches[fc] = max(matches.get(fc, 0), 40)
        elif len(kw) >= 2 and alias_key.startswith(kw[:2]):
            # 短拼音前缀匹配
            for fc in full_codes:
                matches[fc] = max(matches.get(fc, 0), 20)

    # 转为列表并排序
    result = [(fc, score) for fc, score in matches.items()]
    result.sort(key=lambda x: -x[1])
    return result


@dataclass
class StockInfo:
    """股票信息数据类"""
    code: str
    name: str
    market: str
    full_code: str
    score: int = 50  # 匹配质量分 (0-100)


class StockSearchEngine:
    """股票搜索引擎 - 基于实时API"""

    def __init__(self):
        self._session = requests.Session()
        self._session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        self._api_timeout = 3  # 缩短超时，避免页面卡死

    def search_eastmoney(self, keyword: str) -> List[StockInfo]:
        """
        使用东方财富搜索接口 - 支持A股、港股、美股
        接口: http://searchapi.eastmoney.com/api/suggest/get?input=关键词&type=14&count=10
        """
        results = []
        try:
            url = "http://searchapi.eastmoney.com/api/suggest/get"
            params = {
                'input': keyword,
                'type': '14',  # 股票类型
                'count': '20',
                'cell': 'sh_sz_bj_hk_us',
            }
            response = self._session.get(url, params=params, timeout=self._api_timeout)
            data = response.json()

            if data.get('QuotationCodeTable') and data['QuotationCodeTable'].get('Data'):
                for item in data['QuotationCodeTable']['Data']:
                    code = item.get('Code', '')
                    name = item.get('Name', '')
                    market_type = item.get('MarketType', '')
                    security_type = item.get('SecurityTypeName', '')

                    # 映射市场类型
                    market_map = {
                        '1': ('A股-上海', f"{code}.SS"),
                        '2': ('A股-深圳', f"{code}.SZ"),
                        '3': ('港股', f"{code}.HK"),
                        '5': ('美股', code),  # 美股不需要后缀
                        '100': ('A股-北京', f"{code}.BJ"),
                    }

                    market, full_code = market_map.get(market_type, ('未知', code))

                    # ETF特殊处理
                    if 'ETF' in security_type or '基金' in name:
                        if code.startswith('5') or code.startswith('51') or code.startswith('56'):
                            market = 'ETF-上海'
                            full_code = f"{code}.SS"
                        else:
                            market = 'ETF-深圳'
                            full_code = f"{code}.SZ"

                    results.append(StockInfo(
                        code=code,
                        name=name,
                        market=market,
                        full_code=full_code
                    ))
        except Exception as e:
            print(f"东方财富搜索失败: {e}")

        return results

    def search_tencent(self, keyword: str) -> List[StockInfo]:
        """
        使用腾讯财经搜索接口 - 主要支持A股
        接口: https://smartbox.gtimg.cn/s3/?t=all&format=jsonp&q=关键词
        """
        results = []
        try:
            url = "https://smartbox.gtimg.cn/s3/"
            params = {
                't': 'all',
                'format': 'json',
                'q': keyword,
            }
            response = self._session.get(url, params=params, timeout=self._api_timeout)
            # 腾讯返回的是JSONP格式，需要处理
            text = response.text
            if text.startswith('data='):
                text = text[5:]

            data = json.loads(text)

            if data.get('data'):
                for item in data['data']:
                    # 格式: code~name~pinyin~type~...
                    parts = item.split('~')
                    if len(parts) >= 4:
                        code = parts[0]
                        name = parts[1]
                        stock_type = parts[3]

                        # 过滤非股票类型
                        if stock_type not in ['1', '2', '23', '24', '25', '26']:  # 1=A股 2=B股 23/24/25/26=指数
                            continue

                        # 判断市场
                        if code.startswith('6'):
                            market = 'A股-上海'
                            full_code = f"{code}.SS"
                        elif code.startswith('0') or code.startswith('3'):
                            market = 'A股-深圳'
                            full_code = f"{code}.SZ"
                        elif code.startswith('4') or code.startswith('8'):
                            market = 'A股-北京'
                            full_code = f"{code}.BJ"
                        elif code.startswith('sh'):
                            code = code[2:]
                            market = 'A股-上海'
                            full_code = f"{code}.SS"
                        elif code.startswith('sz'):
                            code = code[2:]
                            market = 'A股-深圳'
                            full_code = f"{code}.SZ"
                        else:
                            continue

                        results.append(StockInfo(
                            code=code,
                            name=name,
                            market=market,
                            full_code=full_code
                        ))
        except Exception as e:
            print(f"腾讯搜索失败: {e}")

        return results

    def search_sina(self, keyword: str) -> List[StockInfo]:
        """
        使用新浪财经搜索接口
        接口: https://suggest3.sinajs.cn/suggest/type=11,12,13,14,15&key=关键词
        """
        results = []
        try:
            url = "https://suggest3.sinajs.cn/suggest/"
            params = {
                'type': '11,12,13,14,15,21,22,23,24,25,26,27,28,29,31,32,33',  # 包含A股、港股、美股
                'key': keyword,
            }
            response = self._session.get(url, params=params, timeout=self._api_timeout)

            # 新浪返回的是JavaScript格式，需要解析
            text = response.text
            if 'suggest=' in text:
                # 提取内容部分
                start = text.find('"') + 1
                end = text.rfind('"')
                if start > 0 and end > start:
                    content = text[start:end]
                    items = content.split(';')

                    for item in items:
                        if not item:
                            continue
                        parts = item.split(',')
                        if len(parts) >= 4:
                            # 格式: code,name,pinyin,type,market,...
                            raw_code = parts[0]
                            name = parts[1]
                            stock_type = parts[3] if len(parts) > 3 else ''

                            # 解析代码和市场
                            if raw_code.startswith('sh'):
                                code = raw_code[2:]
                                market = 'A股-上海'
                                full_code = f"{code}.SS"
                            elif raw_code.startswith('sz'):
                                code = raw_code[2:]
                                market = 'A股-深圳'
                                full_code = f"{code}.SZ"
                            elif raw_code.startswith('bj'):
                                code = raw_code[2:]
                                market = 'A股-北京'
                                full_code = f"{code}.BJ"
                            elif raw_code.startswith('hk'):
                                code = raw_code[2:]
                                market = '港股'
                                full_code = f"{code}.HK"
                            elif raw_code.startswith('gb_'):
                                code = raw_code[3:].upper()
                                market = '美股'
                                full_code = code
                            elif raw_code.startswith('us'):
                                code = raw_code[2:].upper()
                                market = '美股'
                                full_code = code
                            else:
                                continue

                            # 过滤期货、期权等
                            if stock_type in ['fu', 'op', 'if']:
                                continue

                            results.append(StockInfo(
                                code=code,
                                name=name,
                                market=market,
                                full_code=full_code
                            ))
        except Exception as e:
            print(f"新浪搜索失败: {e}")

        return results

    def search(self, keyword: str, limit: int = 10) -> List[StockInfo]:
        """
        综合搜索 - 别名匹配 + 多数据源API

        搜索优先级：
        1. 期货直接匹配
        2. 跨市场别名精确/模糊匹配
        3. 东方财富/新浪 API 搜索
        4. 本地备选列表兜底

        结果排序: 精确匹配 > 别名精确 > 前缀 > 包含 > API结果
        """
        if not keyword or len(keyword) < 1:
            return []

        keyword_upper = keyword.upper()
        keyword_lower = keyword.lower().strip()

        # ── 1. 优先检查美股期货 ──
        us_futures_map = {
            "GC=F": ("GC=F", "COMEX黄金期货", "美股期货"),
            "SI=F": ("SI=F", "COMEX白银期货", "美股期货"),
            "HG=F": ("HG=F", "COMEX铜期货", "美股期货"),
            "CL=F": ("CL=F", "WTI原油期货", "美股期货"),
            "BZ=F": ("BZ=F", "布伦特原油期货", "美股期货"),
            "NG=F": ("NG=F", "天然气期货", "美股期货"),
            "ES=F": ("ES=F", "标普500指数期货", "美股期货"),
            "NQ=F": ("NQ=F", "纳斯达克100期货", "美股期货"),
            "YM=F": ("YM=F", "道琼斯指数期货", "美股期货"),
            "RTY=F": ("RTY=F", "罗素2000期货", "美股期货"),
            "ZB=F": ("ZB=F", "长期国债期货", "美股期货"),
            "ZN=F": ("ZN=F", "10年期国债期货", "美股期货"),
            "GOLD": ("GC=F", "黄金期货", "美股期货"),
            "SILVER": ("SI=F", "白银期货", "美股期货"),
            "OIL": ("CL=F", "原油期货", "美股期货"),
            "WTI": ("CL=F", "WTI原油", "美股期货"),
            "BRENT": ("BZ=F", "布伦特原油", "美股期货"),
            "NATGAS": ("NG=F", "天然气", "美股期货"),
            # 中文搜索期货
            "黄金": ("GC=F", "COMEX黄金期货", "美股期货"),
            "白银": ("SI=F", "COMEX白银期货", "美股期货"),
            "原油": ("CL=F", "WTI原油期货", "美股期货"),
            "天然气": ("NG=F", "天然气期货", "美股期货"),
            "标普": ("ES=F", "标普500指数期货", "美股期货"),
            "纳指": ("NQ=F", "纳斯达克100期货", "美股期货"),
            "道指": ("YM=F", "道琼斯指数期货", "美股期货"),
            "铜": ("HG=F", "COMEX铜期货", "美股期货"),
        }

        # 期货匹配（精确 + 中文）
        futures_key = keyword_upper if keyword_upper in us_futures_map else (
            keyword if keyword in us_futures_map else None
        )
        if futures_key:
            code, name, market = us_futures_map[futures_key]
            return [StockInfo(code=code, name=name, market=market, full_code=code, score=100)]

        all_results = []
        seen_codes = set()

        # ── 2. 跨市场别名匹配 ──
        alias_matches = _fuzzy_match_aliases(keyword)
        for full_code, score in alias_matches:
            if full_code in seen_codes:
                continue
            seen_codes.add(full_code)
            info = CROSS_MARKET_ALIASES[full_code]
            code_only = full_code.split(".")[0]
            all_results.append(StockInfo(
                code=code_only,
                name=info["name"],
                market=info["market"],
                full_code=full_code,
                score=score,
            ))

        # ── 3. API搜索 ──
        sources = [
            self.search_eastmoney,
            self.search_sina,
        ]

        for source in sources:
            try:
                results = source(keyword)
                for stock in results:
                    if stock.full_code not in seen_codes:
                        seen_codes.add(stock.full_code)
                        # API结果给一个基础分，精确匹配加分
                        api_score = 10
                        # Eastmoney prefixes an A-share name with ``XD`` on
                        # ex-dividend days and may truncate the displayed name
                        # (for example, ``XD药明康`` for ``药明康德``).  Keep
                        # the source spelling for display, but normalize only
                        # this known action marker while scoring the match.
                        normalized_name = (
                            stock.name[2:]
                            if stock.name[:2].upper() == 'XD'
                            else stock.name
                        )
                        normalized_name_lower = normalized_name.lower()
                        has_action_prefix = normalized_name != stock.name
                        if (
                            stock.code == keyword_upper
                            or stock.name == keyword
                            or normalized_name_lower == keyword_lower
                        ):
                            api_score = 95
                        elif (
                            stock.code.startswith(keyword_upper)
                            or stock.name.startswith(keyword)
                            or normalized_name_lower.startswith(keyword_lower)
                            or (
                                has_action_prefix
                                and normalized_name_lower
                                and keyword_lower.startswith(normalized_name_lower)
                            )
                        ):
                            api_score = 70
                        elif (
                            keyword_lower in stock.code.lower()
                            or keyword in stock.name
                            or keyword_lower in normalized_name_lower
                        ):
                            api_score = 35
                        stock.score = api_score
                        all_results.append(stock)

                if len(all_results) >= limit * 2:
                    break
                time.sleep(0.1)
            except Exception:
                continue

        # ── 4. 兜底：本地备选列表 ──
        if not all_results:
            all_results = self._search_fallback_list(keyword, limit)

        # ── 5. 按匹配质量排序 ──
        all_results.sort(key=lambda s: -s.score)
        return all_results[:limit]

    def get_stock_info(self, code: str) -> Optional[StockInfo]:
        """通过代码获取股票信息（优先本地别名字典，避免不必要的网络请求）"""
        code_upper = code.upper().strip()

        # ── 优先检查本地别名字典（零网络开销）──
        if code_upper in CROSS_MARKET_ALIASES:
            info = CROSS_MARKET_ALIASES[code_upper]
            code_only = code_upper.split(".")[0]
            return StockInfo(
                code=code_only, name=info["name"],
                market=info["market"], full_code=code_upper, score=100,
            )

        # ── 反向查找：用纯数字代码匹配带后缀的条目 ──
        for full_code, info in CROSS_MARKET_ALIASES.items():
            if full_code.split(".")[0] == code_upper:
                return StockInfo(
                    code=code_upper, name=info["name"],
                    market=info["market"], full_code=full_code, score=100,
                )

        # ── 本地没有才走 API ──
        results = self.search(code, limit=5)
        for stock in results:
            if stock.code == code_upper or stock.full_code.upper() == code_upper:
                return stock

        return None

    def format_stock_option(self, stock: StockInfo) -> str:
        """格式化股票选项显示"""
        return f"{stock.name} ({stock.code}) - {stock.market}"

    def _search_fallback_list(self, keyword: str, limit: int = 10) -> List[StockInfo]:
        """搜索本地备选股票列表"""
        # 热门股票备选列表
        fallback_stocks = [
            # 美股期货
            ("GC=F", "COMEX黄金期货", "美股期货", "GC=F"),
            ("SI=F", "COMEX白银期货", "美股期货", "SI=F"),
            ("HG=F", "COMEX铜期货", "美股期货", "HG=F"),
            ("CL=F", "WTI原油期货", "美股期货", "CL=F"),
            ("BZ=F", "布伦特原油期货", "美股期货", "BZ=F"),
            ("NG=F", "天然气期货", "美股期货", "NG=F"),
            ("ES=F", "标普500指数期货", "美股期货", "ES=F"),
            ("NQ=F", "纳斯达克100期货", "美股期货", "NQ=F"),
            ("YM=F", "道琼斯指数期货", "美股期货", "YM=F"),
            ("RTY=F", "罗素2000期货", "美股期货", "RTY=F"),
            ("ZB=F", "长期国债期货", "美股期货", "ZB=F"),
            ("ZN=F", "10年期国债期货", "美股期货", "ZN=F"),
            ("ZC=F", "玉米期货", "美股期货", "ZC=F"),
            ("ZS=F", "大豆期货", "美股期货", "ZS=F"),
            ("ZW=F", "小麦期货", "美股期货", "ZW=F"),
            ("GOLD", "黄金期货", "美股期货", "GC=F"),
            ("SILVER", "白银期货", "美股期货", "SI=F"),
            ("OIL", "原油期货", "美股期货", "CL=F"),
            ("WTI", "WTI原油", "美股期货", "CL=F"),
            ("BRENT", "布伦特原油", "美股期货", "BZ=F"),
            ("NATGAS", "天然气", "美股期货", "NG=F"),
            ("SPX500", "标普500期货", "美股期货", "ES=F"),
            ("NAS100", "纳指期货", "美股期货", "NQ=F"),

            # A股个股
            ("000001", "平安银行", "A股-深圳", "000001.SZ"),
            ("000002", "万科A", "A股-深圳", "000002.SZ"),
            ("000333", "美的集团", "A股-深圳", "000333.SZ"),
            ("000568", "泸州老窖", "A股-深圳", "000568.SZ"),
            ("000651", "格力电器", "A股-深圳", "000651.SZ"),
            ("000858", "五粮液", "A股-深圳", "000858.SZ"),
            ("002230", "科大讯飞", "A股-深圳", "002230.SZ"),
            ("002415", "海康威视", "A股-深圳", "002415.SZ"),
            ("002594", "比亚迪", "A股-深圳", "002594.SZ"),
            ("002714", "牧原股份", "A股-深圳", "002714.SZ"),
            ("300014", "亿纬锂能", "A股-深圳", "300014.SZ"),
            ("300015", "爱尔眼科", "A股-深圳", "300015.SZ"),
            ("300059", "东方财富", "A股-深圳", "300059.SZ"),
            ("300124", "汇川技术", "A股-深圳", "300124.SZ"),
            ("300274", "阳光电源", "A股-深圳", "300274.SZ"),
            ("300750", "宁德时代", "A股-深圳", "300750.SZ"),
            ("300760", "迈瑞医疗", "A股-深圳", "300760.SZ"),
            ("600000", "浦发银行", "A股-上海", "600000.SS"),
            ("600009", "上海机场", "A股-上海", "600009.SS"),
            ("600028", "中国石化", "A股-上海", "600028.SS"),
            ("600030", "中信证券", "A股-上海", "600030.SS"),
            ("600031", "三一重工", "A股-上海", "600031.SS"),
            ("600036", "招商银行", "A股-上海", "600036.SS"),
            ("600048", "保利发展", "A股-上海", "600048.SS"),
            ("600276", "恒瑞医药", "A股-上海", "600276.SS"),
            ("600309", "万华化学", "A股-上海", "600309.SS"),
            ("600406", "国电南瑞", "A股-上海", "600406.SS"),
            ("600436", "片仔癀", "A股-上海", "600436.SS"),
            ("600519", "贵州茅台", "A股-上海", "600519.SS"),
            ("600585", "海螺水泥", "A股-上海", "600585.SS"),
            ("600690", "海尔智家", "A股-上海", "600690.SS"),
            ("600809", "山西汾酒", "A股-上海", "600809.SS"),
            ("600887", "伊利股份", "A股-上海", "600887.SS"),
            ("600900", "长江电力", "A股-上海", "600900.SS"),
            ("601012", "隆基绿能", "A股-上海", "601012.SS"),
            ("601088", "中国神华", "A股-上海", "601088.SS"),
            ("601138", "工业富联", "A股-上海", "601138.SS"),
            ("601166", "兴业银行", "A股-上海", "601166.SS"),
            ("601288", "农业银行", "A股-上海", "601288.SS"),
            ("601318", "中国平安", "A股-上海", "601318.SS"),
            ("601398", "工商银行", "A股-上海", "601398.SS"),
            ("601668", "中国建筑", "A股-上海", "601668.SS"),
            ("601888", "中国中免", "A股-上海", "601888.SS"),
            ("601899", "紫金矿业", "A股-上海", "601899.SS"),
            ("603288", "海天味业", "A股-上海", "603288.SS"),
            ("603501", "韦尔股份", "A股-上海", "603501.SS"),
            ("603986", "兆易创新", "A股-上海", "603986.SS"),
            ("688008", "澜起科技", "A股-科创", "688008.SS"),
            ("688111", "金山办公", "A股-科创", "688111.SS"),
            ("688981", "中芯国际", "A股-科创", "688981.SS"),
            ("159915", "创业板ETF", "ETF-深圳", "159915.SZ"),
            ("510300", "沪深300ETF", "ETF-上海", "510300.SS"),
            ("510050", "上证50ETF", "ETF-上海", "510050.SS"),
            ("512000", "券商ETF", "ETF-上海", "512000.SS"),
            ("518880", "黄金ETF", "ETF-上海", "518880.SS"),
            ("00700", "腾讯控股", "港股", "0700.HK"),
            ("03690", "美团-W", "港股", "3690.HK"),
            ("09999", "阿里巴巴-W", "港股", "9999.HK"),
            ("01810", "小米集团-W", "港股", "1810.HK"),
            ("AAPL", "苹果", "美股", "AAPL"),
            ("TSLA", "特斯拉", "美股", "TSLA"),
            ("NVDA", "英伟达", "美股", "NVDA"),
            ("MSFT", "微软", "美股", "MSFT"),
            ("AMZN", "亚马逊", "美股", "AMZN"),
            ("BABA", "阿里巴巴", "美股", "BABA"),
            ("SPY", "标普500ETF", "美股ETF", "SPY"),
            ("QQQ", "纳指100ETF", "美股ETF", "QQQ"),
        ]

        results = []
        keyword_upper = keyword.upper()

        for code, name, market, full_code in fallback_stocks:
            # 精确匹配
            if code == keyword_upper or name == keyword:
                results.append(StockInfo(code=code, name=name, market=market, full_code=full_code))
            # 开头匹配
            elif code.startswith(keyword_upper) or name.startswith(keyword):
                results.append(StockInfo(code=code, name=name, market=market, full_code=full_code))
            # 包含匹配
            elif keyword_upper in code or keyword in name:
                results.append(StockInfo(code=code, name=name, market=market, full_code=full_code))

        return results[:limit]


# 全局搜索引擎实例
_search_engine = StockSearchEngine()


def search_stocks(keyword: str, limit: int = 10) -> List[Dict]:
    """
    便捷函数：搜索股票

    Returns:
        List[Dict]: 包含code, name, market, full_code, display的字典列表
    """
    results = _search_engine.search(keyword, limit)
    return [
        {
            "code": r.code,
            "name": r.name,
            "market": r.market,
            "full_code": r.full_code,
            "display": _search_engine.format_stock_option(r)
        }
        for r in results
    ]


def get_stock_info(code: str) -> Optional[Dict]:
    """获取单个股票信息"""
    stock = _search_engine.get_stock_info(code)
    if stock:
        return {
            "code": stock.code,
            "name": stock.name,
            "market": stock.market,
            "full_code": stock.full_code,
        }
    return None


def convert_to_full_code(code_or_name: str) -> Optional[str]:
    """
    将代码或名称转换为完整的带后缀代码
    """
    # 如果已经包含后缀，直接返回
    if "." in code_or_name:
        return code_or_name

    stock = _search_engine.get_stock_info(code_or_name)
    if stock:
        return stock.full_code

    # 尝试搜索名称
    results = _search_engine.search(code_or_name, limit=1)
    if results:
        return results[0].full_code

    return None


def get_board(ticker: str, name: str = "") -> str:
    """
    判定标的所属板块（A 股专用，非 A 股返回 '其他'）
    用前缀规则：
      - 60xxxx           → 主板（沪市）；属于沪深 300 时另算
      - 000xxx / 001xxx  → 主板（深市）
      - 002xxx           → 中小板（已合并入主板）→ 主板
      - 003xxx           → 主板（深市新增）
      - 30xxxx           → 创业板
      - 688xxx / 689xxx  → 科创板
      - 4xxxxx / 8xxxxx  → 北交所
      - 名称含 ST/*ST    → ST（不论代码）
      - 否则             → 其他
    """
    # ST 优先判定（最高风险，覆盖所有代码段）
    if name and ("ST" in name.upper().replace(" ", "") or "退" in name):
        return "ST"

    # 去掉后缀
    code = str(ticker).upper().replace(".SS", "").replace(".SZ", "").replace(".BJ", "").strip()
    if not code or not code.isdigit():
        return "其他"

    if code.startswith("688") or code.startswith("689"):
        return "科创板"
    if code.startswith("3"):
        return "创业板"
    if code.startswith("4") or code.startswith("8"):
        return "北交所"
    if code.startswith("60") or code.startswith("000") or code.startswith("001") or code.startswith("002") or code.startswith("003"):
        return "主板"
    return "其他"


if __name__ == "__main__":
    # 测试搜索功能
    test_keywords = ["茅台", "600519", "腾讯", "AAPL", "TSLA"]
    for kw in test_keywords:
        print(f"\n搜索 '{kw}':")
        results = search_stocks(kw, limit=5)
        for r in results:
            print(f"  {r['display']}")
