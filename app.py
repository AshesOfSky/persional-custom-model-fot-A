"""
app.py — 自定义股票分析模型
富途牛牛暗色风格 · EMA隧道 · 斐波那契回撤 · 基本面看板
"""

import streamlit as st
import pandas as pd
import numpy as np
from datetime import datetime

from modules.data_router import fetch_all, get_router_stats, clear_data_cache
from modules.data_cache import get_cache_stats
from modules.technical import build_chart, add_all_indicators, calc_fibonacci, IndicatorConfig, annotate_patterns_on_chart, annotate_buy_sell_signals, annotate_sr_bands
from modules.fundamental import (
    build_fundamental_summary,
    fmt_pct, fmt_num, fmt_large,
)
from modules.analysis import run_full_analysis
from modules.backtest import run_backtest, compare_strategies
from modules.alerts import (
    get_alert_manager, add_price_alert, add_ma_cross_alert,
    add_bb_break_alert, add_kdj_alert, add_rsi_alert, add_cci_alert,
    add_wr_alert, add_dmi_cross_alert, add_volume_spike_alert,
    check_alerts, get_active_alerts, remove_alert, get_alert_stats
)
from modules.social_content import (
    generate_xiaohongshu_post, generate_compact_summary, generate_professional_analysis
)
from modules.info_analysis import run_info_analysis
from modules.stock_search import search_stocks, get_stock_info, convert_to_full_code
from modules.intraday_analysis import run_intraday_analysis
from modules.news_analysis import run_news_analysis
from modules.support_resistance import calc_multi_timeframe_sr, calc_daily_levels
from modules.correlation_analysis import run_correlation_analysis, identify_asset_class, fetch_macro_calendar
from modules.indicator_interpreter import interpret_all_indicators
from modules.market_sentiment import fetch_market_sentiment


# ─── 页面配置 ────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="股票分析模型",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ─── 缓存包装：避免每次 rerun 重算指标/分析/情绪 ──────────────────────────────
# 指标和分析对 OHLCV 是纯函数；用 (ticker, period, timeframe, n_rows, last_index)
# 做 hashable cache key，DataFrame 本身用下划线前缀跳过 streamlit 哈希。

@st.cache_data(ttl=3600, show_spinner=False)
def _cached_indicators(_ohlcv: pd.DataFrame, cache_key: tuple) -> pd.DataFrame:
    return add_all_indicators(_ohlcv.copy())

@st.cache_data(ttl=3600, show_spinner=False)
def _cached_analysis(_df: pd.DataFrame, _info: dict, timeframe: str, cache_key: tuple):
    return run_full_analysis(_df, _info, timeframe)

@st.cache_data(ttl=300, show_spinner=False)
def _cached_sentiment(ticker: str, market: str):
    return fetch_market_sentiment(ticker=ticker, market=market)

def _ohlcv_key(ohlcv: pd.DataFrame, *extra) -> tuple:
    if ohlcv is None or ohlcv.empty:
        return ("empty",) + tuple(extra)
    return (len(ohlcv), str(ohlcv.index[0]), str(ohlcv.index[-1])) + tuple(extra)

# ─── 全局暗色 CSS (富途牛牛风格) ───────────────────────────────────────────

st.markdown("""
<style>
/* ===== 全局背景系统 ===== */
.stApp {
    background-color: #0d1117 !important;
    color: #e6edf3 !important;
}
[data-testid="stAppViewContainer"] {
    background-color: #0d1117 !important;
}
[data-testid="stHeader"] {
    background-color: #161b22 !important;
    border-bottom: 1px solid #30363d !important;
}

/* 主内容区域 */
.main > div {
    background-color: #0d1117 !important;
}

/* ===== 侧边栏 ===== */
[data-testid="stSidebar"] {
    background-color: #161b22 !important;
    border-right: 1px solid #30363d !important;
}
[data-testid="stSidebar"] .stMarkdown {
    color: #e6edf3 !important;
}

/* ===== 输入框系统 ===== */
/* 文本输入 */
.stTextInput > div {
    background-color: transparent !important;
}
.stTextInput > div > div > input {
    background-color: #21262d !important;
    color: #e6edf3 !important;
    border: 1px solid #30363d !important;
    border-radius: 8px !important;
    caret-color: #58a6ff !important;
}
.stTextInput > div > div > input:focus {
    border-color: #58a6ff !important;
    box-shadow: 0 0 0 2px rgba(88, 166, 255, 0.2) !important;
}
.stTextInput > div > div > input::placeholder {
    color: #6e7681 !important;
}

/* 数字输入 */
.stNumberInput > div > div > input {
    background-color: #21262d !important;
    color: #e6edf3 !important;
    border: 1px solid #30363d !important;
    border-radius: 8px !important;
}

/* ===== 下拉菜单系统 (重点修复) ===== */
/* 下拉框本体 */
.stSelectbox > label {
    color: #e6edf3 !important;
}
.stSelectbox > div > div {
    background-color: #21262d !important;
    color: #e6edf3 !important;
    border: 1px solid #30363d !important;
    border-radius: 8px !important;
}

/* 下拉箭头 */
.stSelectbox > div > div > div[data-baseweb="select"] > div > div > span {
    color: #8b949e !important;
}

/* 下拉菜单弹出层 - 这是关键修复 */
[role="listbox"] {
    background-color: #21262d !important;
    border: 1px solid #30363d !important;
    border-radius: 8px !important;
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.5) !important;
}

/* 下拉选项 */
[role="option"] {
    background-color: #21262d !important;
    color: #e6edf3 !important;
    font-size: 14px !important;
    padding: 10px 16px !important;
}

/* 下拉选项悬停 */
[role="option"]:hover {
    background-color: #30363d !important;
    color: #e6edf3 !important;
}

/* 下拉选项选中 */
[role="option"][aria-selected="true"] {
    background-color: #388bfd26 !important;
    color: #58a6ff !important;
}

/* ===== 按钮系统 ===== */
.stButton > button {
    background: linear-gradient(135deg, #238636 0%, #2ea043 100%) !important;
    color: #ffffff !important;
    border: none !important;
    border-radius: 8px !important;
    font-weight: 600 !important;
    padding: 0.6rem 2rem !important;
    transition: all 0.2s ease !important;
}
.stButton > button:hover {
    background: linear-gradient(135deg, #2ea043 0%, #3fb950 100%) !important;
    transform: translateY(-1px) !important;
    box-shadow: 0 4px 12px rgba(46, 160, 67, 0.3) !important;
}
.stButton > button:active {
    transform: translateY(0) !important;
}

/* 次要按钮 */
.stButton > button[kind="secondary"] {
    background: #21262d !important;
    border: 1px solid #30363d !important;
    color: #e6edf3 !important;
}
.stButton > button[kind="secondary"]:hover {
    background: #30363d !important;
}

/* ===== 指标卡片 (富途风格) ===== */
.metric-card {
    background-color: #161b22;
    border-radius: 12px;
    padding: 16px 20px;
    text-align: center;
    margin: 4px;
    border: 1px solid #30363d;
    transition: border-color 0.2s ease;
}
.metric-card:hover {
    border-color: #484f58;
}
.metric-label {
    font-size: 12px;
    color: #8b949e;
    margin-bottom: 6px;
    font-weight: 500;
    letter-spacing: 0.3px;
}
.metric-value {
    font-size: 22px;
    font-weight: 700;
    color: #e6edf3;
    font-family: 'SF Mono', Monaco, monospace;
}
/* 富途牛牛风格：A股红涨绿跌 */
.metric-value.up   { color: #ff4d4f; }   /* 红色上涨 */
.metric-value.down { color: #00b578; }   /* 绿色下跌 */
.metric-value.neutral { color: #8b949e; }

/* ===== 趋势面板 ===== */
.trend-panel {
    background-color: #161b22;
    border-radius: 12px;
    padding: 18px 22px;
    margin: 8px 0;
    border: 1px solid #30363d;
    border-left: 3px solid #58a6ff;
}
.trend-title {
    font-size: 13px;
    color: #8b949e;
    margin-bottom: 8px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}
.trend-value {
    font-size: 16px;
    font-weight: 600;
    color: #e6edf3;
    line-height: 1.5;
}

/* ===== Tab 样式优化 ===== */
[data-testid="stTabs"] {
    background-color: transparent !important;
}
[data-testid="stTabs"] [role="tablist"] {
    background-color: #161b22 !important;
    border-radius: 10px;
    padding: 4px;
    gap: 4px;
}
[data-testid="stTabs"] button {
    color: #8b949e !important;
    font-weight: 500 !important;
    background-color: transparent !important;
    border: none !important;
    border-radius: 8px !important;
    padding: 10px 20px !important;
    transition: all 0.2s ease !important;
}
[data-testid="stTabs"] button:hover {
    color: #e6edf3 !important;
    background-color: #21262d !important;
}
[data-testid="stTabs"] button[aria-selected="true"] {
    color: #ffffff !important;
    background-color: #238636 !important;
    font-weight: 600 !important;
}

/* ===== 分割线 ===== */
hr {
    border-color: #30363d !important;
    border-width: 1px !important;
    margin: 24px 0 !important;
}

/* ===== 文本区域 ===== */
.stTextArea > label {
    color: #e6edf3 !important;
}
.stTextArea > div > div > textarea {
    background-color: #161b22 !important;
    color: #e6edf3 !important;
    border: 1px solid #30363d !important;
    border-radius: 8px !important;
    font-family: 'SF Mono', Monaco, monospace !important;
    line-height: 1.6 !important;
}
.stTextArea > div > div > textarea:focus {
    border-color: #58a6ff !important;
    box-shadow: 0 0 0 2px rgba(88, 166, 255, 0.2) !important;
}

/* ===== 信息框/Alert ===== */
.stAlert {
    background-color: #161b22 !important;
    color: #e6edf3 !important;
    border: 1px solid #30363d !important;
    border-radius: 10px !important;
    padding: 16px !important;
}
.stAlert [data-baseweb="notification"] {
    background-color: transparent !important;
}
.stAlert [data-testid="stMarkdownContainer"] {
    color: #e6edf3 !important;
}

/* 不同类型的Alert */
.stAlert[data-kind="info"] {
    border-left: 3px solid #58a6ff !important;
}
.stAlert[data-kind="success"] {
    border-left: 3px solid #238636 !important;
}
.stAlert[data-kind="warning"] {
    border-left: 3px solid #d29922 !important;
}
.stAlert[data-kind="error"] {
    border-left: 3px solid #ff4d4f !important;
}

/* ===== Toggle开关 ===== */
.stToggle > label {
    color: #e6edf3 !important;
}
.stToggle > div > div > div {
    background-color: #30363d !important;
}
.stToggle > div > div > div[data-checked="true"] {
    background-color: #238636 !important;
}

/* ===== 复选框 ===== */
.stCheckbox > label {
    color: #e6edf3 !important;
}
.stCheckbox > div > div > div {
    background-color: #21262d !important;
    border-color: #484f58 !important;
}

/* ===== 单选框 ===== */
.stRadio > label {
    color: #e6edf3 !important;
}
.stRadio > div > label {
    color: #e6edf3 !important;
}

/* ===== 扩展器 (Expander) ===== */
.streamlit-expander {
    background-color: #161b22 !important;
    border: 1px solid #30363d !important;
    border-radius: 10px !important;
}
.streamlit-expanderHeader {
    color: #e6edf3 !important;
    font-weight: 600 !important;
    font-size: 14px !important;
    padding: 12px 16px !important;
}
.streamlit-expanderHeader:hover {
    color: #58a6ff !important;
}
.streamlit-expanderContent {
    background-color: #0d1117 !important;
    border-top: 1px solid #30363d !important;
    padding: 16px !important;
}

/* ===== 表格样式 ===== */
[data-testid="stDataFrame"] {
    background-color: #161b22 !important;
    border: 1px solid #30363d !important;
    border-radius: 10px !important;
}
[data-testid="stDataFrame"] td {
    color: #e6edf3 !important;
    border-color: #30363d !important;
}
[data-testid="stDataFrame"] th {
    background-color: #21262d !important;
    color: #e6edf3 !important;
    border-color: #30363d !important;
    font-weight: 600 !important;
}

/* ===== 图表容器 ===== */
[data-testid="stPlotlyChart"] {
    background-color: #161b22 !important;
    border: 1px solid #30363d !important;
    border-radius: 12px !important;
    padding: 12px !important;
}

/* ===== 代码/文本高亮 ===== */
pre {
    background-color: #161b22 !important;
    border: 1px solid #30363d !important;
    border-radius: 8px !important;
    padding: 16px !important;
}
code {
    background-color: #21262d !important;
    color: #e6edf3 !important;
    padding: 2px 6px !important;
    border-radius: 4px !important;
    font-family: 'SF Mono', Monaco, monospace !important;
}

/* ===== 提示文字 ===== */
st.caption {
    color: #6e7681 !important;
    font-size: 12px !important;
}

/* ===== 滚动条样式 ===== */
::-webkit-scrollbar {
    width: 8px;
    height: 8px;
}
::-webkit-scrollbar-track {
    background: #161b22;
    border-radius: 4px;
}
::-webkit-scrollbar-thumb {
    background: #30363d;
    border-radius: 4px;
}
::-webkit-scrollbar-thumb:hover {
    background: #484f58;
}

/* ===== 通用文字颜色修复 ===== */
.stMarkdown {
    color: #e6edf3 !important;
}
.stMarkdown p, .stMarkdown span, .stMarkdown li {
    color: #e6edf3 !important;
}
.stMarkdown h1, .stMarkdown h2, .stMarkdown h3,
.stMarkdown h4, .stMarkdown h5, .stMarkdown h6 {
    color: #e6edf3 !important;
    font-weight: 600 !important;
}

/* 链接 */
a {
    color: #58a6ff !important;
    text-decoration: none !important;
}
a:hover {
    text-decoration: underline !important;
    color: #79b8ff !important;
}

/* ===== 搜索框特殊样式 ===== */
.search-highlight {
    background-color: #388bfd26 !important;
    color: #58a6ff !important;
    font-weight: 600 !important;
}

/* ===== 价格颜色 (富途风格) ===== */
.price-up { color: #ff4d4f !important; }      /* 红涨 */
.price-down { color: #00b578 !important; }    /* 绿跌 */
.price-flat { color: #8b949e !important; }    /* 平 */

/* ===== 加载动画 ===== */
.stSpinner > div > div {
    border-color: #58a6ff transparent transparent transparent !important;
}

/* ===== 空状态提示 ===== */
.stEmpty {
    color: #6e7681 !important;
}

/* ===== 禁用状态 ===== */
[disabled] {
    opacity: 0.5 !important;
}

/* ===== 工具提示 ===== */
[data-baseweb="tooltip"] {
    background-color: #21262d !important;
    color: #e6edf3 !important;
    border: 1px solid #30363d !important;
    border-radius: 6px !important;
    padding: 8px 12px !important;
}

/* ===== Streamlit Dropdown 深度修复 ===== */
/* 下拉菜单容器 */
[data-baseweb="popover"] {
    background-color: #21262d !important;
    border: 1px solid #30363d !important;
    border-radius: 8px !important;
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.5) !important;
}

/* 下拉菜单内部 */
[data-baseweb="popover"] > div {
    background-color: #21262d !important;
}

/* 菜单列表 */
[data-baseweb="menu"] {
    background-color: #21262d !important;
    border: none !important;
}

[data-baseweb="menu"] > div {
    background-color: #21262d !important;
}

/* 菜单项 */
[data-baseweb="menu-item"] {
    background-color: #21262d !important;
    color: #e6edf3 !important;
    padding: 10px 16px !important;
}

[data-baseweb="menu-item"]:hover {
    background-color: #30363d !important;
}

[data-baseweb="menu-item"][aria-selected="true"] {
    background-color: #388bfd26 !important;
    color: #58a6ff !important;
}

/* 菜单项文字 */
[data-baseweb="menu-item"] > div {
    color: #e6edf3 !important;
}

/* Select 输入框内部 */
[data-baseweb="select"] {
    background-color: #21262d !important;
}

[data-baseweb="select"] > div {
    background-color: #21262d !important;
}

/* 已选中的值 */
[data-baseweb="select"] [data-testid="stMarkdownContainer"] {
    color: #e6edf3 !important;
}

/* Input 输入文字 */
[data-baseweb="base-input"] {
    background-color: #21262d !important;
    color: #e6edf3 !important;
}

/* ===== 标签页内容区域 ===== */
[data-testid="stTabContent"] {
    background-color: #0d1117 !important;
}

/* ===== 进度条 ===== */
[data-testid="stProgress"] > div > div {
    background-color: #238636 !important;
}
[data-testid="stProgress"] > div {
    background-color: #21262d !important;
}

/* ===== 分割线增强 ===== */
[data-testid="stHorizontalBlock"] {
    background-color: transparent !important;
}

/* ===== 富途牛牛风格卡片悬浮效果 ===== */
.metric-card:hover,
.trend-panel:hover,
.streamlit-expander:hover {
    border-color: #58a6ff !important;
    transition: border-color 0.2s ease !important;
}

/* ===== 选中状态高亮 ===== */
::selection {
    background-color: #388bfd26 !important;
    color: #e6edf3 !important;
}

/* ===== 增强：共振面板样式 ===== */
.trend-panel[style*="border-left: 4px"] {
    background: linear-gradient(90deg, #161b22 0%, #0d1117 100%) !important;
}

/* ===== 富途牛牛风格：渐变评分条 ===== */
.stProgress > div > div > div {
    border-radius: 4px !important;
    background: linear-gradient(90deg, #F6465D 0%, #FFD700 50%, #0ECB81 100%) !important;
}

/* ===== 子面板分隔线 ===== */
.trend-panel + .trend-panel {
    margin-top: 4px !important;
}

/* ===== 字体大小微调 ===== */
.metric-card .metric-value {
    font-size: 18px !important;
    word-break: break-word !important;
}
</style>
""", unsafe_allow_html=True)


# ─── 辅助函数 ────────────────────────────────────────────────────────────────

def price_change_color(chg):
    if chg is None:
        return "neutral"
    return "up" if chg >= 0 else "down"

def pct_fmt(val):
    if val is None:
        return "—"
    sign = "+" if val >= 0 else ""
    return f"{sign}{val*100:.2f}%"

def render_metric_card(label, value, value_class="neutral"):
    st.markdown(
        f"""<div class="metric-card">
              <div class="metric-label">{label}</div>
              <div class="metric-value {value_class}">{value}</div>
            </div>""",
        unsafe_allow_html=True,
    )

def render_trend_item(title, value, color="#E0E0E0"):
    st.markdown(
        f"""<div class="trend-panel">
              <div class="trend-title">{title}</div>
              <div class="trend-value" style="color:{color}">{value}</div>
            </div>""",
        unsafe_allow_html=True,
    )


# ─── 顶部输入区 ──────────────────────────────────────────────────────────────

st.markdown(
    '<h2 style="color:#00B4D8;margin-bottom:0">📈 股票分析模型</h2>'
    '<p style="color:#888;margin-top:2px;font-size:12px">EMA隧道 · 斐波那契回撤 · 基本面看板 · 趋势规则引擎</p>',
    unsafe_allow_html=True,
)

col_search, col_period, col_btn = st.columns([3, 2, 1])

# 初始化session_state
if 'search_keyword' not in st.session_state:
    st.session_state.search_keyword = ""
if 'selected_stock' not in st.session_state:
    st.session_state.selected_stock = "600519.SS"
if 'search_results' not in st.session_state:
    st.session_state.search_results = []
if 'should_analyze' not in st.session_state:
    st.session_state.should_analyze = False

with col_search:
    # 切换搜索/手动输入模式（初始化 key 避免 value 冲突）
    if 'manual_input_toggle' not in st.session_state:
        st.session_state.manual_input_toggle = False
    use_manual_input = st.toggle("手动输入", key="manual_input_toggle",
                                  help="切换为手动输入模式，支持期货、外汇、美股等")

    if use_manual_input:
        # 手动输入模式
        manual_input = st.text_input(
            "股票代码",
            value=st.session_state.selected_stock,
            placeholder="A股: 600519.SS | 美股: AAPL | 港股: 0700.HK | 美股期货: GC=F CL=F | 国内期货: AU0",
            label_visibility="collapsed",
        )
        st.session_state.selected_stock = manual_input.upper()
        st.markdown(f"<p style='color:#8b949e;font-size:12px;margin-top:4px'>当前: <span style='color:#58a6ff'>{st.session_state.selected_stock}</span></p>", unsafe_allow_html=True)
    else:
        # 搜索模式 - 使用搜索按钮避免实时调用API
        search_col, btn_col = st.columns([3, 1])

        with search_col:
            search_input = st.text_input(
                "🔍 搜索",
                value=st.session_state.search_keyword,
                placeholder="输入股票名称/代码，如：茅台、600519",
                label_visibility="collapsed",
                key="search_input_box"
            )

        with btn_col:
            search_btn = st.button("🔍 搜索", key="search_btn")

        # 点击搜索按钮才执行搜索
        if search_btn and search_input:
            with st.spinner("搜索中..."):
                st.session_state.search_keyword = search_input
                st.session_state.search_results = search_stocks(search_input, limit=10)

        # 显示搜索结果下拉框
        if st.session_state.search_results:
            options = ["请选择股票..."] + [r['display'] for r in st.session_state.search_results]

            selected_display = st.selectbox(
                "搜索结果",
                options=options,
                index=0,
                label_visibility="collapsed",
                key="stock_selector"
            )

            # 更新选中的股票 —— 选中后自动触发分析
            if selected_display != "请选择股票...":
                for r in st.session_state.search_results:
                    if r['display'] == selected_display:
                        if st.session_state.selected_stock != r['full_code']:
                            st.session_state.selected_stock = r['full_code']
                            st.session_state.should_analyze = True  # 自动触发分析
                            # 清空搜索结果避免重复触发
                            st.session_state.search_results = []
                            st.rerun()
                        break

        # 显示当前选中的股票 - 富途牛牛风格
        current_info = get_stock_info(st.session_state.selected_stock)
        if current_info:
            st.markdown(
                f"<p style='color:#8b949e;font-size:12px;margin-top:6px'>"
                f"当前: <span style='color:#58a6ff;font-weight:600'>{current_info['name']}</span> "
                f"<span style='color:#6e7681'>({current_info['full_code']})</span></p>",
                unsafe_allow_html=True
            )
        else:
            st.markdown(
                f"<p style='color:#8b949e;font-size:12px;margin-top:6px'>"
                f"当前: <span style='color:#e6edf3'>{st.session_state.selected_stock}</span></p>",
                unsafe_allow_html=True
            )

    ticker_input = st.session_state.selected_stock

with col_period:
    # 时间频率选择（日/周/月/小时）
    timeframe_col, period_col = st.columns([1, 1])
    with timeframe_col:
        timeframe = st.selectbox(
            "时间频率",
            ["日线", "周线", "月线", "小时线"],
            index=0,
            label_visibility="collapsed",
            key="timeframe_select"
        )
    with period_col:
        # 根据时间频率调整可选周期
        if timeframe == "日线":
            period_options = ["1M", "3M", "6M", "1Y", "2Y", "5Y"]
            period_default = 3  # 1Y
        elif timeframe == "周线":
            period_options = ["1M", "3M", "6M", "1Y", "2Y", "5Y", "10Y"]
            period_default = 4  # 2Y
        elif timeframe == "月线":
            period_options = ["1Y", "2Y", "5Y", "10Y", "MAX"]
            period_default = 2  # 5Y
        else:  # 小时线
            period_options = ["1D", "3D", "1W", "2W", "1M"]
            period_default = 2  # 1W

        period = st.selectbox(
            "时间周期",
            period_options,
            index=period_default,
            label_visibility="collapsed",
            key="period_select"
        )

with col_btn:
    analyze_btn = st.button("开始分析 ▶")
    if analyze_btn:
        st.session_state.should_analyze = True


# ─── 主逻辑 ──────────────────────────────────────────────────────────────────

if not st.session_state.should_analyze:
    st.markdown(
        '<div style="text-align:center;margin-top:60px;">'
        '<p style="font-size:48px;margin-bottom:16px">📊</p>'
        '<p style="font-size:16px;color:#e6edf3;margin-bottom:8px">输入股票代码，点击「开始分析」</p>'
        '<p style="font-size:12px;margin-top:20px;color:#8b949e;line-height:2.2">'
        '<span style="background:#21262d;padding:4px 12px;border-radius:4px;margin:2px;display:inline-block">'
        '<b style="color:#ff4d4f">A股个股</b> 600519.SS · 000858.SZ</span> '
        '<span style="background:#21262d;padding:4px 12px;border-radius:4px;margin:2px;display:inline-block">'
        '<b style="color:#00b578">美股</b> AAPL · TSLA · NVDA</span><br>'
        '<span style="background:#21262d;padding:4px 12px;border-radius:4px;margin:2px;display:inline-block">'
        '<b style="color:#d29922">ETF</b> 518880 · 510300</span> '
        '<span style="background:#21262d;padding:4px 12px;border-radius:4px;margin:2px;display:inline-block">'
        '<b style="color:#58a6ff">港股</b> 0700.HK · 9988.HK</span> '
        '<span style="background:#21262d;padding:4px 12px;border-radius:4px;margin:2px;display:inline-block">'
        '<b style="color:#a371f7">美股期货</b> GC=F · CL=F · NQ=F</span><br>'
        '<span style="background:#21262d;padding:4px 12px;border-radius:4px;margin:2px;display:inline-block">'
        '<b style="color:#a371f7">国内期货</b> AU0 · RB0 · SC0</span>'
        '</p>'
        '</div>',
        unsafe_allow_html=True,
    )
    st.stop()

# ── 数据拉取 ──────────────────────────────────────────────────────────────
ticker = ticker_input.strip().upper()

with st.spinner(f"正在拉取 {ticker} {timeframe}数据..."):
    try:
        data = fetch_all(ticker, period, timeframe)
    except Exception as e:
        st.error(f"数据获取失败：{e}")
        st.stop()

ohlcv        = data["ohlcv"]
info         = data["info"]
income_stmt  = data["income_stmt"]
balance_sheet= data["balance_sheet"]
market       = data["market"]
data_source  = data.get("data_source", "yfinance")

if ohlcv.empty:
    # 给出针对性提示
    dot = "." in ticker
    suffix = ticker.rsplit(".", 1)[-1] if dot else ""
    hint = ""
    if not dot:
        hint = "代码缺少市场后缀。对于 A股个股，请加上 `.SS`（沪市）/ `.SZ`（深市）；美股无需后缀；期货使用 GCMAIN/AU0 等"
    elif suffix == "SS" and ticker[:1] == "5":
        hint = "以 5 开头的沪市代码通常是 ETF 基金，请直接输入 ETF 代码如 `518880`（无需后缀）"
    elif suffix == "SZ" and ticker[:1] in ("1", "5"):
        hint = "以 1/5 开头的深市代码通常是 ETF/债券基金，请直接输入代码如 `159915`（无需后缀）"
    else:
        hint = "未找到该标的数据。可能原因：① 代码格式错误 ② 该股票已退市 ③ 数据源暂不收录此标的"
    st.error(f"未获取到 **{ticker}** 的行情数据")
    st.info(f"💡 {hint}")
    st.markdown(
        "**常用示例：** `600519.SS` 茅台　`518880` 黄金ETF　`GC=F` 美黄金期货　`AAPL` 苹果"
    )
    st.stop()

# 计算技术指标（按 OHLCV 内容缓存，rerun 时秒级返回）
_indicator_key = _ohlcv_key(ohlcv, ticker, timeframe)
df = _cached_indicators(ohlcv, _indicator_key)
fib = calc_fibonacci(df)

# 基本面
fundamental = build_fundamental_summary(info, income_stmt, balance_sheet)

# 分析
analysis = _cached_analysis(df, info, timeframe, _indicator_key)

# ─── 关键数据看板 ────────────────────────────────────────────────────────────

current_price = fundamental.get("current_price") or (df["Close"].iloc[-1] if not df.empty else None)
prev_close    = fundamental.get("prev_close")
if current_price and prev_close and prev_close != 0:
    chg_pct = (current_price - prev_close) / prev_close
else:
    chg_pct = None

name = fundamental.get("name", ticker)
source_badge = f'<span style="background:#0077B6;color:white;padding:2px 6px;border-radius:4px;font-size:11px;margin-left:8px">{data_source} · {timeframe}</span>'
st.markdown(
    f'<h4 style="color:#E0E0E0;margin:8px 0 4px">{name} '
    f'<span style="color:#888;font-size:13px">{ticker} · {market}</span>{source_badge}</h4>',
    unsafe_allow_html=True,
)

cols = st.columns(7)
card_data = [
    ("最新价",    f"{current_price:.2f}" if current_price else "—",  price_change_color(chg_pct)),
    ("涨跌幅",    pct_fmt(chg_pct),                                   price_change_color(chg_pct)),
    ("PE(TTM)",   fmt_num(fundamental.get("PE")),                     "neutral"),
    ("PB",        fmt_num(fundamental.get("PB")),                     "neutral"),
    ("ROE",       fmt_pct(fundamental.get("ROE")),                    "neutral"),
    ("52W高/低",  f"{fundamental.get('52w_high','—')} / {fundamental.get('52w_low','—')}", "neutral"),
    ("市值",      fmt_large(fundamental.get("market_cap")),           "neutral"),
]
for col, (label, value, cls) in zip(cols, card_data):
    with col:
        render_metric_card(label, value, cls)

st.markdown("<hr>", unsafe_allow_html=True)

# ─── 趋势判断面板 ────────────────────────────────────────────────────────────

st.markdown('<p style="color:#00B4D8;font-size:14px;font-weight:bold;margin-bottom:8px">趋势研判</p>',
            unsafe_allow_html=True)

t_cols = st.columns(5)
trend_color_map = {"多头": "#0ECB81", "空头": "#F6465D", "震荡": "#FFD700"}
trend_color = trend_color_map.get(analysis.get("trend", "震荡"), "#E0E0E0")

with t_cols[0]:
    trend_emoji = analysis.get("trend_emoji", "🟡")
    trend_text  = analysis.get("trend", "—")
    render_trend_item("趋势方向", f"{trend_emoji} {trend_text}", trend_color)

with t_cols[1]:
    render_trend_item("EMA隧道信号", analysis.get("tunnel_position", "—"), "#C0C0C0")

with t_cols[2]:
    fib_support    = analysis.get("fib_support")
    fib_resistance = analysis.get("fib_resistance")
    fib_text = (f"支撑 {fib_support:.2f}" if fib_support else "—") + \
               (" / " + f"压力 {fib_resistance:.2f}" if fib_resistance else "")
    render_trend_item("斐波那契支撑/压力", fib_text, "#FFD700")

with t_cols[3]:
    render_trend_item("估值位置", analysis.get("valuation", "—"),
                      analysis.get("valuation_color", "#888"))

with t_cols[4]:
    render_trend_item(
        f"操作建议 [{analysis.get('action','—')}]",
        analysis.get("advice", "—"),
        analysis.get("advice_color", "#888"),
    )

# ─── 新增：技术分析详情面板 ────────────────────────────────────────────────────
st.markdown(
    '<p style="color:#00B4D8;font-size:14px;font-weight:bold;margin:16px 0 8px">技术分析详情</p>',
    unsafe_allow_html=True
)

# 第一行：布林带、KDJ状态
detail_cols1 = st.columns(4)
with detail_cols1[0]:
    bb_position = analysis.get("bb_position", "—")
    bb_signal = analysis.get("bb_signal", "")
    bb_text = f"{bb_position} ({bb_signal})" if bb_signal else bb_position
    render_trend_item("布林带位置", bb_text, "#C0C0C0")

with detail_cols1[1]:
    kdj_signal = analysis.get("kdj_signal", "—")
    kdj_color = "#0ECB81" if "买入" in kdj_signal else ("#F6465D" if "卖出" in kdj_signal else "#C0C0C0")
    render_trend_item("KDJ信号", kdj_signal, kdj_color)

with detail_cols1[2]:
    squeeze = analysis.get("bb_squeeze_signal", "—")
    render_trend_item("波动状态", squeeze, "#C0C0C0")

with detail_cols1[3]:
    trend_score = analysis.get("trend_strength_score", 50)
    trend_level = analysis.get("trend_strength_level", "—")
    score_color = analysis.get("trend_strength_color", "#888")
    render_trend_item(f"趋势强度 [{trend_score:.0f}/100]", trend_level, score_color)

# 第二行：详细指标数值面板
st.markdown(
    '<p style="color:#00B4D8;font-size:14px;font-weight:bold;margin:16px 0 8px">详细指标数值</p>',
    unsafe_allow_html=True
)

# EMA隧道数值
with st.expander("📈 EMA隧道数值 (斐波那契数列: 8, 13, 21, 55, 144, 169, 288, 338)", expanded=True):
    ema_values = analysis.get("ema_values", {})
    ema_cols = st.columns(4)

    # 内隧道 EMA8, 13, 21
    inner_emas = [8, 13, 21]
    for i, n in enumerate(inner_emas):
        with ema_cols[i]:
            ema_val = ema_values.get(f"EMA_{n}")
            if ema_val:
                render_trend_item(f"EMA{n}", f"{ema_val:.2f}", "#58a6ff")

    # 外隧道 EMA55, 144
    with ema_cols[0]:
        ema55 = ema_values.get("EMA_55")
        if ema55:
            render_trend_item("EMA55", f"{ema55:.2f}", "#a371f7")
    with ema_cols[1]:
        ema144 = ema_values.get("EMA_144")
        if ema144:
            render_trend_item("EMA144", f"{ema144:.2f}", "#a371f7")
    with ema_cols[2]:
        ema169 = ema_values.get("EMA_169")
        if ema169:
            render_trend_item("EMA169", f"{ema169:.2f}", "#a371f7")
    with ema_cols[3]:
        ema288 = ema_values.get("EMA_288")
        if ema288:
            render_trend_item("EMA288", f"{ema288:.2f}", "#a371f7")

    # EMA分析
    ema_analysis_cols = st.columns(2)
    with ema_analysis_cols[0]:
        inner_bull = analysis.get("inner_bull", False)
        inner_bear = analysis.get("inner_bear", False)
        ema_trend = "多头排列 ✅" if inner_bull else ("空头排列 ❌" if inner_bear else "震荡整理")
        ema_trend_color = "#0ECB81" if inner_bull else ("#F6465D" if inner_bear else "#FFD700")
        st.markdown(f"**内隧道(8/13/21):** <span style='color:{ema_trend_color}'>{ema_trend}</span>", unsafe_allow_html=True)

    with ema_analysis_cols[1]:
        inner_top = analysis.get("inner_top")
        inner_bottom = analysis.get("inner_bottom")
        outer_top = analysis.get("outer_top")
        outer_bottom = analysis.get("outer_bottom")
        st.markdown(f"**隧道区间:** 内隧道 {inner_bottom:.2f}-{inner_top:.2f} | 外隧道 {outer_bottom:.2f}-{outer_top:.2f}")

# 布林带和KDJ数值
with st.expander("📊 布林带 & KDJ 数值", expanded=True):
    indicator_cols = st.columns(4)

    with indicator_cols[0]:
        bb_upper = analysis.get("bb_upper")
        if bb_upper:
            render_trend_item("布林上轨", f"{bb_upper:.2f}", "#ff7b72")

    with indicator_cols[1]:
        bb_middle = analysis.get("bb_middle")
        if bb_middle:
            render_trend_item("布林中轨", f"{bb_middle:.2f}", "#ffd700")

    with indicator_cols[2]:
        bb_lower = analysis.get("bb_lower")
        if bb_lower:
            render_trend_item("布林下轨", f"{bb_lower:.2f}", "#7ee787")

    with indicator_cols[3]:
        bb_bandwidth = analysis.get("bb_bandwidth")
        if bb_bandwidth:
            bandwidth_pct = analysis.get("bb_bandwidth_pct", 0) * 100
            render_trend_item("带宽", f"{bb_bandwidth:.2f} ({bandwidth_pct:.0f}%)", "#c0c0c0")

    # KDJ数值
    kdj_cols = st.columns(4)
    with kdj_cols[0]:
        kdj_k = analysis.get("kdj_k")
        if kdj_k is not None:
            render_trend_item("KDJ-K", f"{kdj_k:.2f}", "#58a6ff")

    with kdj_cols[1]:
        kdj_d = analysis.get("kdj_d")
        if kdj_d is not None:
            render_trend_item("KDJ-D", f"{kdj_d:.2f}", "#58a6ff")

    with kdj_cols[2]:
        kdj_j = analysis.get("kdj_j")
        if kdj_j is not None:
            j_color = "#ff7b72" if kdj_j > 100 else ("#7ee787" if kdj_j < 0 else "#58a6ff")
            render_trend_item("KDJ-J", f"{kdj_j:.2f}", j_color)

    with kdj_cols[3]:
        kdj_signal = analysis.get("kdj_signal", "—")
        kdj_color = "#0ECB81" if "买入" in kdj_signal else ("#F6465D" if "卖出" in kdj_signal else "#888")
        render_trend_item("KDJ信号", kdj_signal, kdj_color)

# 多指标交叉验证面板
with st.expander("🔀 多指标交叉验证（共振分析）", expanded=True):
    # 共振概览
    resonance_text = analysis.get("resonance", "无明显共振")
    resonance_color = analysis.get("resonance_color", "#888")
    buy_count = analysis.get("buy_count", 0)
    sell_count = analysis.get("sell_count", 0)
    buy_signals = analysis.get("buy_signals", [])
    sell_signals = analysis.get("sell_signals", [])
    neutral_signals = analysis.get("neutral_signals", [])

    res_col1, res_col2, res_col3 = st.columns(3)
    with res_col1:
        render_trend_item("共振结论", resonance_text, resonance_color)
    with res_col2:
        render_trend_item("看多信号", f"{buy_count} 个", "#0ECB81" if buy_count >= 2 else "#888")
    with res_col3:
        render_trend_item("看空信号", f"{sell_count} 个", "#F6465D" if sell_count >= 2 else "#888")

    # 信号详情列表
    if buy_signals:
        st.markdown(f"<span style='color:#0ECB81'>📈 看多: {' · '.join(buy_signals)}</span>", unsafe_allow_html=True)
    if sell_signals:
        st.markdown(f"<span style='color:#F6465D'>📉 看空: {' · '.join(sell_signals)}</span>", unsafe_allow_html=True)
    if neutral_signals:
        st.markdown(f"<span style='color:#888'>➖ 中性: {' · '.join(neutral_signals)}</span>", unsafe_allow_html=True)

    st.markdown("---")

    # 各指标详细状态 —— 第一行: RSI / MACD / CCI / WR
    st.markdown("**📋 各指标详细状态**")
    ind_row1 = st.columns(4)

    with ind_row1[0]:
        rsi = analysis.get("rsi_last")
        if rsi is not None:
            rsi_color = "#F6465D" if rsi > 70 else ("#0ECB81" if rsi < 30 else "#FFD700")
            rsi_label = "超买" if rsi > 70 else ("超卖" if rsi < 30 else "常态")
            render_trend_item(f"RSI ({rsi_label})", f"{rsi:.1f}", rsi_color)
        else:
            render_trend_item("RSI", "—", "#888")

    with ind_row1[1]:
        macd_val = analysis.get("macd")
        macd_sig = analysis.get("macd_signal")
        macd_hist = analysis.get("macd_hist")
        if macd_val is not None:
            macd_bull = analysis.get("macd_bullish", False)
            macd_bear = analysis.get("macd_bearish", False)
            macd_color = "#0ECB81" if macd_bull else ("#F6465D" if macd_bear else "#888")
            macd_status = "金叉" if analysis.get("macd_golden_cross") else ("死叉" if analysis.get("macd_dead_cross") else ("多头" if macd_bull else ("空头" if macd_bear else "—")))
            render_trend_item(f"MACD ({macd_status})", f"DIF:{macd_val:.3f} DEA:{macd_sig:.3f}", macd_color)
        else:
            render_trend_item("MACD", "—", "#888")

    with ind_row1[2]:
        cci_val = analysis.get("cci")
        if cci_val is not None:
            cci_signal = analysis.get("cci_signal", "—")
            cci_color = "#F6465D" if analysis.get("cci_overbought") else ("#0ECB81" if analysis.get("cci_oversold") else "#FFD700")
            render_trend_item(f"CCI", f"{cci_val:.1f} ({cci_signal})", cci_color)
        else:
            render_trend_item("CCI", "—", "#888")

    with ind_row1[3]:
        wr_val = analysis.get("wr")
        if wr_val is not None:
            wr_signal = analysis.get("wr_signal", "—")
            wr_color = "#F6465D" if analysis.get("wr_overbought") else ("#0ECB81" if analysis.get("wr_oversold") else "#FFD700")
            render_trend_item(f"WR(威廉)", f"{wr_val:.1f} ({wr_signal})", wr_color)
        else:
            render_trend_item("WR", "—", "#888")

    # 第二行: DMI/ADX / 成交量 / 资金流 / 综合信号
    ind_row2 = st.columns(4)

    with ind_row2[0]:
        adx_val = analysis.get("adx")
        if adx_val is not None:
            dmi_signal = analysis.get("dmi_signal", "—")
            dmi_bull = analysis.get("dmi_bullish", False)
            dmi_color = "#0ECB81" if dmi_bull else "#F6465D"
            render_trend_item(f"DMI (ADX:{adx_val:.1f})", dmi_signal, dmi_color)
        else:
            render_trend_item("DMI", "—", "#888")

    with ind_row2[1]:
        vol_signal = analysis.get("vol_signal")
        if vol_signal:
            vol_score = analysis.get("vol_score", 0)
            vol_color = "#0ECB81" if vol_score > 0 else ("#F6465D" if vol_score < 0 else "#888")
            render_trend_item("成交量", vol_signal, vol_color)
        else:
            render_trend_item("成交量", "—", "#888")

    with ind_row2[2]:
        mfi_val = analysis.get("mfi")
        obv_bull = analysis.get("obv_bullish")
        if mfi_val is not None:
            mfi_signal = analysis.get("mfi_signal", "—")
            obv_text = "OBV↑" if obv_bull else "OBV↓"
            mf_color = "#0ECB81" if obv_bull else "#F6465D"
            render_trend_item(f"资金流 ({obv_text})", f"MFI:{mfi_val:.1f}", mf_color)
        else:
            render_trend_item("资金流", "—", "#888")

    with ind_row2[3]:
        sig_summary = analysis.get("signal_summary", "—")
        render_trend_item("综合信号", sig_summary, "#C0C0C0")

    # ── 指标详细解释 ──
    st.markdown("---")
    st.markdown("**🔍 指标详细解释与利多利空分析**")

    indicator_interps = analysis.get("indicator_interpretations", [])
    if indicator_interps:
        for idx in range(0, len(indicator_interps), 2):
            interp_cols = st.columns(2)
            for col_idx in range(2):
                if idx + col_idx < len(indicator_interps):
                    interp = indicator_interps[idx + col_idx]
                    with interp_cols[col_idx]:
                        bias = interp.get("bias", "中性")
                        bias_color = "#0ECB81" if bias == "利多" else ("#F6465D" if bias == "利空" else "#888")
                        bias_score = interp.get("bias_score", 0)
                        prob = interp.get("probability", {})
                        bull_pct = prob.get("bullish_pct", 0)
                        bear_pct = prob.get("bearish_pct", 0)

                        st.markdown(
                            f"<div style='background:#1a1a2e;padding:10px;border-radius:8px;border-left:3px solid {bias_color};margin-bottom:8px'>"
                            f"<div style='display:flex;justify-content:space-between;align-items:center'>"
                            f"<span style='color:#e6edf3;font-weight:600'>{interp.get('name', '—')}</span>"
                            f"<span style='color:{bias_color};font-size:13px;font-weight:600'>{bias} ({bias_score:+d})</span>"
                            f"</div>"
                            f"<div style='color:#888;font-size:11px;margin:4px 0'>{interp.get('interpretation', '')}</div>"
                            f"<div style='display:flex;gap:8px;font-size:11px'>"
                            f"<span style='color:#0ECB81'>多 {bull_pct}%</span>"
                            f"<span style='color:#F6465D'>空 {bear_pct}%</span>"
                            f"</div>"
                            f"<div style='color:#666;font-size:10px;margin-top:3px'>{interp.get('action_hint', '')}</div>"
                            f"</div>",
                            unsafe_allow_html=True
                        )

# ── 技术面多因子评分 v2 ──
comp_prob = analysis.get("comprehensive_probability", {})
if comp_prob:
    with st.expander("📊 技术面多因子评分", expanded=True):
        bull_pct = comp_prob.get("bullish_pct", 0)
        bear_pct = comp_prob.get("bearish_pct", 0)
        neutral_pct = comp_prob.get("neutral_pct", 0)
        overall_bias = comp_prob.get("overall_bias", "中性")
        overall_score = comp_prob.get("overall_score", 0)

        if "看多" in overall_bias or "偏多" in overall_bias:
            overall_color = "#0ECB81"
        elif "看空" in overall_bias or "偏空" in overall_bias:
            overall_color = "#F6465D"
        else:
            overall_color = "#888"

        # Row 1: 核心指标
        prob_cols = st.columns(4)
        with prob_cols[0]:
            render_trend_item("综合判断", overall_bias, overall_color)
        with prob_cols[1]:
            render_trend_item("看多概率", f"{bull_pct:.0f}%", "#0ECB81")
        with prob_cols[2]:
            render_trend_item("看空概率", f"{bear_pct:.0f}%", "#F6465D")
        with prob_cols[3]:
            render_trend_item("综合评分", f"{overall_score:+.1f}", overall_color)

        # Row 2: 概率条
        st.markdown(
            f"<div style='display:flex;height:14px;border-radius:7px;overflow:hidden;margin:8px 0'>"
            f"<div style='width:{bull_pct}%;background:#0ECB81'></div>"
            f"<div style='width:{neutral_pct}%;background:#555'></div>"
            f"<div style='width:{bear_pct}%;background:#F6465D'></div>"
            f"</div>"
            f"<div style='display:flex;justify-content:space-between;font-size:10px;color:#888'>"
            f"<span>多 {bull_pct:.0f}%</span><span>中性 {neutral_pct:.0f}%</span><span>空 {bear_pct:.0f}%</span>"
            f"</div>",
            unsafe_allow_html=True
        )

        # Row 3: 共振状态badge
        conf_level = comp_prob.get("confirmation_level", "无")
        conf_score = comp_prob.get("confirmation_score", 0)
        confirming = comp_prob.get("confirming_categories", [])
        conflicting = comp_prob.get("conflicting_categories", [])
        if conf_level == "强共振":
            conf_color, conf_icon = "#4CAF50", "🎯"
        elif conf_level == "共振":
            conf_color, conf_icon = "#8BC34A", "✅"
        elif conf_level == "分歧":
            conf_color, conf_icon = "#FF9800", "⚠️"
        else:
            conf_color, conf_icon = "#666666", "◻️"

        conf_cats_str = "、".join(confirming[:4]) if confirming else ""
        conf_text = f"{conf_icon} {conf_level}"
        if conf_cats_str:
            conf_text += f" — {conf_cats_str}方向一致"
        if conflicting:
            conf_text += f" (对立: {'、'.join(conflicting[:2])})"

        st.markdown(
            f"<div style='background:rgba({int(conf_color[1:3],16)},{int(conf_color[3:5],16)},{int(conf_color[5:7],16)},0.15);"
            f"border:1px solid {conf_color};padding:6px 12px;border-radius:6px;margin:8px 0;"
            f"color:{conf_color};font-size:13px;font-weight:bold'>{conf_text}</div>",
            unsafe_allow_html=True
        )

        # Row 4: 因子分解 (5个基础类别 + 共振类)
        cat_details = comp_prob.get("category_details", {})
        if cat_details:
            st.markdown("<div style='color:#00B4D8;font-size:12px;font-weight:bold;margin:8px 0 4px'>因子分解 (Z-score)</div>", unsafe_allow_html=True)
            # 按权重排序显示基础类别
            display_cats = ["趋势类", "动量类", "量能类", "波动类", "形态类", "消息面"]
            active_cats = [c for c in display_cats if c in cat_details]
            if active_cats:
                cat_cols = st.columns(len(active_cats))
                for i, cat in enumerate(active_cats):
                    d = cat_details[cat]
                    z = d.get("z_score", 0)
                    w = d.get("weight", 0)
                    n = d.get("indicator_count", 0)
                    direction = d.get("direction", "中性")
                    if z > 0.3:
                        z_color = "#0ECB81"
                    elif z < -0.3:
                        z_color = "#F6465D"
                    else:
                        z_color = "#888"

                    with cat_cols[i]:
                        st.markdown(
                            f"<div style='text-align:center;padding:6px;border:1px solid #333;border-radius:6px'>"
                            f"<div style='color:#888;font-size:10px'>{cat}</div>"
                            f"<div style='color:{z_color};font-size:18px;font-weight:bold'>{z:+.2f}z</div>"
                            f"<div style='color:#555;font-size:9px'>权重{w:.0%} · {n}指标</div>"
                            f"</div>",
                            unsafe_allow_html=True
                        )

        # Row 5: 多周期共振 (如有)
        mtf_bonus = comp_prob.get("mtf_alignment_bonus", 0)
        if mtf_bonus != 0:
            mtf_dir = "看多" if mtf_bonus > 0 else "看空"
            mtf_color = "#0ECB81" if mtf_bonus > 0 else "#F6465D"
            st.markdown(
                f"<div style='color:{mtf_color};font-size:11px;margin-top:4px'>"
                f"📅 多周期共振加成: {mtf_dir} ({mtf_bonus:+.3f}z)</div>",
                unsafe_allow_html=True
            )

        # Row 6: 解读
        interpretation = comp_prob.get("interpretation", "")
        if interpretation:
            st.markdown(f"<div style='color:#aaa;font-size:12px;margin-top:8px;padding:8px;background:rgba(255,255,255,0.03);border-radius:6px'>💡 {interpretation}</div>", unsafe_allow_html=True)

        # 方法论标记
        method = comp_prob.get("scoring_method", "")
        if method:
            st.markdown(f"<div style='color:#444;font-size:9px;margin-top:4px;text-align:right'>算法: {method} | 指标数: {comp_prob.get('indicator_count', 0)} | Sigmoid概率转换</div>", unsafe_allow_html=True)

# ── 背离警告横幅 ──
div_summary = analysis.get("divergence_summary", {})
active_divs = div_summary.get("active_divergences", [])
if active_divs:
    for ad in active_divs[:2]:
        div_type = ad.get("type", "")
        div_ind = ad.get("indicator", "")
        div_rel = ad.get("reliability", "")
        div_desc = ad.get("description", "")
        if "顶" in div_type:
            bg_c, ic = "#3a1515", "#F6465D"
            emoji = "🔻"
        else:
            bg_c, ic = "#153a15", "#0ECB81"
            emoji = "🔺"
        st.markdown(
            f"<div style='background:{bg_c};border:1px solid {ic};border-radius:8px;padding:10px 16px;margin:6px 0'>"
            f"<span style='color:{ic};font-weight:bold;font-size:14px'>{emoji} {div_ind} {div_type}（{div_rel}）</span>"
            f"<br><span style='color:#ccc;font-size:12px'>{div_desc}</span></div>",
            unsafe_allow_html=True
        )

# ── K线形态面板 ──
candle_patterns = analysis.get("candlestick_patterns", [])
if candle_patterns:
    with st.expander("🕯️ K线形态识别", expanded=False):
        cp_cols = st.columns(min(len(candle_patterns), 5))
        for i, cp in enumerate(candle_patterns[:5]):
            with cp_cols[i % len(cp_cols)]:
                dir_color = "#0ECB81" if cp["direction"] == "看涨" else ("#F6465D" if cp["direction"] == "看跌" else "#888")
                dir_emoji = "🟢" if cp["direction"] == "看涨" else ("🔴" if cp["direction"] == "看跌" else "⚪")
                st.markdown(
                    f"<div style='text-align:center;padding:6px;background:#161b22;border-radius:8px;border:1px solid #30363d'>"
                    f"<div style='font-size:12px;color:{dir_color};font-weight:bold'>{dir_emoji} {cp['name_cn']}</div>"
                    f"<div style='font-size:10px;color:#888'>{cp['type']} · {cp['direction']} · {cp['reliability']}</div>"
                    f"</div>",
                    unsafe_allow_html=True
                )
        if candle_patterns:
            st.markdown(f"<div style='color:#888;font-size:11px;margin-top:6px'>💡 {candle_patterns[0].get('description', '')}</div>", unsafe_allow_html=True)

# ── 图表形态面板 ──
chart_patterns = analysis.get("chart_patterns", [])
if chart_patterns:
    with st.expander("📐 图表形态识别", expanded=True):
        for cp in chart_patterns[:3]:
            dir_color = "#0ECB81" if cp.get("direction") == "看涨" else ("#F6465D" if cp.get("direction") == "看跌" else "#888")
            cp_cols2 = st.columns(5)
            with cp_cols2[0]:
                render_trend_item("形态", cp.get("name_cn", ""), dir_color)
            with cp_cols2[1]:
                render_trend_item("方向", cp.get("direction", ""), dir_color)
            with cp_cols2[2]:
                target = cp.get("target_price", 0)
                render_trend_item("目标价", f"{target:.2f}" if target else "—", dir_color)
            with cp_cols2[3]:
                stop = cp.get("stop_price", 0)
                render_trend_item("止损位", f"{stop:.2f}" if stop else "—", "#F6465D")
            with cp_cols2[4]:
                render_trend_item("信心度", f"{cp.get('confidence', 0)}%", "#FFD700")
            st.markdown(f"<div style='color:#aaa;font-size:11px;margin-bottom:8px'>💡 {cp.get('description', '')}</div>", unsafe_allow_html=True)

# ── 风险评估面板 ──
risk_metrics = analysis.get("risk_metrics", {})
if risk_metrics and risk_metrics.get("available"):
    with st.expander("⚠️ 风险量化评估", expanded=False):
        risk_cols = st.columns(5)
        with risk_cols[0]:
            rl = risk_metrics.get("risk_level", "—")
            rc = risk_metrics.get("risk_color", "#888")
            render_trend_item("风险等级", rl, rc)
        with risk_cols[1]:
            render_trend_item("VaR(95%)", f"{risk_metrics.get('var_95', 0):.2f}%", "#F6465D")
        with risk_cols[2]:
            beta = risk_metrics.get("beta")
            render_trend_item("Beta", f"{beta:.2f}" if beta is not None else "—", "#00B4D8")
        with risk_cols[3]:
            render_trend_item("Sortino", f"{risk_metrics.get('sortino_ratio', 0):.2f}", "#0ECB81")
        with risk_cols[4]:
            render_trend_item("最大回撤", f"{risk_metrics.get('max_drawdown', {}).get('value', 0):.1f}%", "#F6465D")

        # 波动率锥
        vol_cone = risk_metrics.get("volatility_cone", {})
        if vol_cone.get("available"):
            st.markdown("---")
            vc_cols = st.columns(3)
            with vc_cols[0]:
                render_trend_item("当前波动率", f"{vol_cone.get('current_vol', 0):.1f}%", "#FFD700")
            with vc_cols[1]:
                render_trend_item("历史分位", f"{vol_cone.get('percentile', 50):.0f}%", "#00B4D8")
            with vc_cols[2]:
                render_trend_item("波动水平", vol_cone.get("level", "—"), "#888")
            st.markdown(f"<div style='color:#aaa;font-size:11px'>💡 {vol_cone.get('interpretation', '')}</div>", unsafe_allow_html=True)

        # Kelly
        kelly = risk_metrics.get("kelly_criterion", {})
        if kelly.get("half_kelly") is not None:
            st.markdown("---")
            k_cols = st.columns(4)
            with k_cols[0]:
                render_trend_item("Kelly最优仓位", f"{kelly.get('optimal_fraction', 0):.1f}%", "#FFD700")
            with k_cols[1]:
                render_trend_item("Half Kelly(建议)", f"{kelly.get('half_kelly', 0):.1f}%", "#0ECB81")
            with k_cols[2]:
                render_trend_item("历史胜率", f"{kelly.get('win_rate', 0):.1f}%", "#00B4D8")
            with k_cols[3]:
                render_trend_item("盈亏比", f"{kelly.get('avg_win', 0):.2f}/{kelly.get('avg_loss', 0):.2f}", "#888")
            st.markdown(f"<div style='color:#aaa;font-size:11px'>💡 {kelly.get('interpretation', '')}</div>", unsafe_allow_html=True)

# ── 量价深度分析面板 ──
vp_analysis = analysis.get("volume_price_analysis", {})
if vp_analysis and vp_analysis.get("volume_price_divergence"):
    with st.expander("📊 量价深度分析", expanded=False):
        vp_cols = st.columns(4)

        vp_div = vp_analysis.get("volume_price_divergence", {})
        with vp_cols[0]:
            vp_type = vp_div.get("type", "—")
            vp_color = "#F6465D" if vp_div.get("bias_score", 0) < -10 else ("#0ECB81" if vp_div.get("bias_score", 0) > 10 else "#888")
            render_trend_item("量价关系", vp_type, vp_color)

        vr = vp_analysis.get("volume_ratio", {})
        with vp_cols[1]:
            render_trend_item("量比", f"{vr.get('value', 0):.2f} ({vr.get('level', '—')})", "#00B4D8")

        turnover = vp_analysis.get("turnover_rate", {})
        with vp_cols[2]:
            if turnover.get("available"):
                render_trend_item("换手率", f"{turnover.get('current', 0):.2f}% ({turnover.get('level', '')})", "#FFD700")
            else:
                render_trend_item("换手率", "N/A", "#555")

        ad = vp_analysis.get("accumulation_distribution", {})
        with vp_cols[3]:
            ad_phase = ad.get("phase", "—")
            ad_color = "#0ECB81" if ad_phase in ("吸筹", "拉升") else ("#F6465D" if ad_phase in ("派发", "下跌") else "#888")
            render_trend_item("AD阶段", ad_phase, ad_color)

        # Wyckoff
        wyckoff = vp_analysis.get("wyckoff_phase", {})
        if wyckoff.get("phase") and wyckoff["phase"] != "数据不足":
            st.markdown(f"<div style='color:#aaa;font-size:11px;margin-top:4px'>🔄 Wyckoff: {wyckoff['phase']}（信心{wyckoff.get('confidence', 0)}%）— {wyckoff.get('description', '')}</div>", unsafe_allow_html=True)

        if vp_div.get("description"):
            st.markdown(f"<div style='color:#aaa;font-size:11px'>💡 {vp_div['description']}</div>", unsafe_allow_html=True)

# 斐波那契回撤详情
with st.expander("🎯 斐波那契回撤详情", expanded=True):
    fib_cols = st.columns(4)

    with fib_cols[0]:
        fib_support = analysis.get("fib_support")
        fib_support_lvl = analysis.get("fib_support_lvl")
        fib_support_pct = analysis.get("fib_support_pct")
        if fib_support:
            support_text = f"{fib_support:.2f}"
            if fib_support_lvl:
                support_text += f" ({fib_support_lvl})"
            if fib_support_pct:
                support_text += f"<br><small>回撤 {fib_support_pct:.1f}%</small>"
            render_trend_item("支撑位", support_text, "#7ee787")

    with fib_cols[1]:
        fib_resistance = analysis.get("fib_resistance")
        fib_resistance_lvl = analysis.get("fib_resistance_lvl")
        fib_resistance_pct = analysis.get("fib_resistance_pct")
        if fib_resistance:
            resistance_text = f"{fib_resistance:.2f}"
            if fib_resistance_lvl:
                resistance_text += f" ({fib_resistance_lvl})"
            if fib_resistance_pct:
                resistance_text += f"<br><small>回撤 {fib_resistance_pct:.1f}%</small>"
            render_trend_item("压力位", resistance_text, "#ff7b72")

    with fib_cols[2]:
        fib_high = analysis.get("fib_high")
        if fib_high:
            render_trend_item("近期高点", f"{fib_high:.2f}", "#c0c0c0")

    with fib_cols[3]:
        fib_low = analysis.get("fib_low")
        if fib_low:
            render_trend_item("近期低点", f"{fib_low:.2f}", "#c0c0c0")

    # 斐波那契区间
    fib_range = analysis.get("fib_range")
    current_fib_pct = 0
    if fib_range and fib_low and current_price:
        current_fib_pct = (current_price - fib_low) / fib_range * 100
        st.progress(min(current_fib_pct / 100, 1.0), text=f"当前价格处于回撤区间: {current_fib_pct:.1f}%")

# 第三行：精确交易区间
with st.expander("📊 精确交易区间分析", expanded=True):
    # 获取交易区间数据
    zones = analysis
    current = current_price or 0

    if zones.get("buy_zone_low"):
        # 主要区间显示
        zone_cols = st.columns(5)

        with zone_cols[0]:
            render_trend_item(
                "📗 买入区间",
                f"{zones['buy_zone_low']:.2f} - {zones['buy_zone_high']:.2f}",
                "#7ee787"
            )
            if zones.get("buy_zone_center"):
                st.caption(f"理想入场: {zones['buy_zone_center']:.2f}")

        with zone_cols[1]:
            render_trend_item(
                "📕 目标区间",
                f"{zones['sell_zone_low']:.2f} - {zones['sell_zone_high']:.2f}",
                "#ff7b72"
            )
            if zones.get("primary_resistance"):
                st.caption(f"主要压力: {zones['primary_resistance']:.2f}")

        with zone_cols[2]:
            sl_pct = zones.get('stop_loss_pct', 0) * 100
            render_trend_item(
                "🛡️ 止损位",
                f"{zones['stop_loss']:.2f} ({sl_pct:.1f}%)",
                "#ffa657"
            )

        with zone_cols[3]:
            rr = zones.get('risk_reward_ratio', 0)
            rr_color = "#7ee787" if rr >= 2 else ("#ffa657" if rr >= 1.5 else "#ff7b72")
            render_trend_item(
                "⚖️ 盈亏比",
                f"1:{rr:.1f}",
                rr_color
            )

        with zone_cols[4]:
            conf = zones.get('confidence_score', 0)
            conf_color = "#7ee787" if conf >= 70 else ("#ffa657" if conf >= 40 else "#ff7b72")
            render_trend_item(
                "🎯 信心评分",
                f"{conf:.0f}/100 ({zones.get('confidence_level', '?')})",
                conf_color
            )

        # 详细信息
        st.markdown("---")
        detail_cols = st.columns(2)

        with detail_cols[0]:
            st.markdown("**📈 多层级止盈目标**")
            tp1_pct = zones.get('take_profit_pct_1', 0) * 100
            tp2_pct = zones.get('take_profit_pct_2', 0) * 100
            tp3_pct = zones.get('take_profit_pct_3', 0) * 100

            st.markdown(f"""
            • **第一目标位**: {zones['take_profit_1']:.2f} (+{tp1_pct:.1f}%) - 建议减仓30%
            • **第二目标位**: {zones['take_profit_2']:.2f} (+{tp2_pct:.1f}%) - 建议减仓40%
            • **第三目标位**: {zones['take_profit_3']:.2f} (+{tp3_pct:.1f}%) - 清仓或持有
            """)

            st.markdown("**💰 建议仓位**: ", unsafe_allow_html=True)
            st.markdown(f"<span style='color:#7ee787;font-weight:bold'>{zones.get('position_size', 'N/A')}</span>", unsafe_allow_html=True)

        with detail_cols[1]:
            st.markdown("**📊 关键价位详情**")

            if zones.get('support_levels'):
                st.markdown(f"• **支撑位** ({zones['support_count']}个): " + ", ".join([f"{s:.2f}" for s in zones['support_levels'][:3]]))
            if zones.get('resistance_levels'):
                st.markdown(f"• **压力位** ({zones['resistance_count']}个): " + ", ".join([f"{r:.2f}" for r in zones['resistance_levels'][:3]]))

            if zones.get('confidence_factors'):
                st.markdown(f"• **信心因素**: {', '.join(zones['confidence_factors'])}")

            atr_pct = zones.get('atr_pct', 0) * 100
            st.markdown(f"• **ATR波动**: {atr_pct:.2f}% ({zones.get('atr', 0):.2f})")

    else:
        st.info("数据不足，无法计算精确交易区间")

    # ── 分周期支撑压力位表 ──
    multi_tf_sr = analysis.get("multi_tf_sr", {})
    if multi_tf_sr:
        st.markdown("---")
        st.markdown("**📐 分周期支撑压力位**")

        sr_data_rows = []
        for tf_name, sr_levels in multi_tf_sr.items():
            if isinstance(sr_levels, dict) and not sr_levels.get("error"):
                ps = sr_levels.get("primary_support")
                pr = sr_levels.get("primary_resistance")
                ps_price = ps.get("price", 0) if isinstance(ps, dict) else (ps if isinstance(ps, (int, float)) else 0)
                pr_price = pr.get("price", 0) if isinstance(pr, dict) else (pr if isinstance(pr, (int, float)) else 0)
                ps_method = ps.get("method", "—") if isinstance(ps, dict) else "—"
                pr_method = pr.get("method", "—") if isinstance(pr, dict) else "—"
                ps_dist = ps.get("distance_pct", 0) if isinstance(ps, dict) else 0
                pr_dist = pr.get("distance_pct", 0) if isinstance(pr, dict) else 0
                ps_strength = ps.get("strength", "—") if isinstance(ps, dict) else "—"
                row = {
                    "周期": tf_name,
                    "主要支撑": f"{ps_price:.2f}" if ps_price else "—",
                    "支撑来源": ps_method,
                    "主要压力": f"{pr_price:.2f}" if pr_price else "—",
                    "压力来源": pr_method,
                    "距支撑%": f"{ps_dist:.2f}%",
                    "距压力%": f"{pr_dist:.2f}%",
                    "强度": ps_strength,
                }
                sr_data_rows.append(row)

        if sr_data_rows:
            sr_df = pd.DataFrame(sr_data_rows)
            st.dataframe(sr_df, width="stretch", hide_index=True)

    # 尝试计算多周期支撑压力位（如果还没有）
    if not multi_tf_sr and df is not None and len(df) > 30:
        try:
            from modules.support_resistance import calc_multi_timeframe_sr
            sr_result = calc_multi_timeframe_sr(df, current_price)
            if sr_result:
                st.markdown("---")
                st.markdown("**📐 多周期关键价位聚合**")

                clusters = sr_result.get("clusters", [])
                if clusters:
                    cluster_rows = []
                    for c in clusters[:10]:
                        cluster_rows.append({
                            "价位": f"{c.get('price', 0):.2f}",
                            "类型": c.get("type", "—"),
                            "来源": c.get("sources", "—"),
                            "周期重叠": c.get("tf_count", 0),
                            "距当前价": f"{c.get('distance_pct', 0):.2f}%",
                        })
                    st.dataframe(pd.DataFrame(cluster_rows), width="stretch", hide_index=True)
        except Exception:
            pass

st.markdown("<hr>", unsafe_allow_html=True)

# ─── Tab 区域 ────────────────────────────────────────────────────────────────

tab_tech, tab_intraday, tab_fund, tab_info, tab_news, tab_report, tab_backtest, tab_alerts, tab_dragon, tab_factor, tab_social, tab_settings = st.tabs(
    ["📈 技术分析", "⚡ 超短线", "📊 基本面", "📰 信息面", "📰 消息面", "📋 趋势报告", "🔄 策略回测", "🔔 预警系统", "🐉 龙空龙", "📊 因子选股", "📱 社媒推文", "⚙️ 设置"]
)

# ── 全局指标初始化（确保每次运行都正确初始化）────────────────────────
indicator_defaults = {
    'show_ema_inner': True,
    'show_ema_outer': True,
    'show_bollinger': True,
    'show_vwap': True,
    'show_sar': False,
    'show_fibonacci': True,
    'show_ichimoku': False,
    'show_td_sequential': False,
    'show_elliott_wave': False,
    'chart_type': 'candle',
    'secondary_indicator_1': 'MACD',
    'secondary_indicator_2': 'KDJ',
    'secondary_indicator_3': 'VOL',
    'secondary_indicator_4': 'OBV',
}

# 同时初始化 toggle 的 key（避免 value 与 key 冲突）
toggle_key_map = {
    'toggle_ema_inner': 'show_ema_inner',
    'toggle_ema_outer': 'show_ema_outer',
    'toggle_bollinger': 'show_bollinger',
    'toggle_vwap': 'show_vwap',
    'toggle_sar': 'show_sar',
    'toggle_fibonacci': 'show_fibonacci',
    'toggle_ichimoku': 'show_ichimoku',
    'toggle_td_sequential': 'show_td_sequential',
    'toggle_elliott_wave': 'show_elliott_wave',
}

for key, default_value in indicator_defaults.items():
    if key not in st.session_state:
        st.session_state[key] = default_value

for toggle_key, source_key in toggle_key_map.items():
    if toggle_key not in st.session_state:
        st.session_state[toggle_key] = st.session_state[source_key]

# ── Tab1：技术分析图 ──────────────────────────────────────────────────────
with tab_tech:
    # 指标控制面板 - 使用session_state单独管理每个开关
    with st.expander("🔧 图表指标设置", expanded=False):

        st.markdown('<p style="color:#00B4D8;font-size:13px;font-weight:bold">主图指标</p>',
                    unsafe_allow_html=True)

        # 主图指标控制 - 第一行（使用 key 直接绑定，不设 value 避免冲突）
        main_col1, main_col2, main_col3 = st.columns(3)
        with main_col1:
            st.toggle("EMA内隧道", key="toggle_ema_inner")
            st.session_state.show_ema_inner = st.session_state.toggle_ema_inner
        with main_col2:
            st.toggle("EMA外隧道", key="toggle_ema_outer")
            st.session_state.show_ema_outer = st.session_state.toggle_ema_outer
        with main_col3:
            st.toggle("布林带", key="toggle_bollinger")
            st.session_state.show_bollinger = st.session_state.toggle_bollinger

        # 主图指标控制 - 第二行
        main_col4, main_col5, main_col6 = st.columns(3)
        with main_col4:
            st.toggle("VWAP", key="toggle_vwap")
            st.session_state.show_vwap = st.session_state.toggle_vwap
        with main_col5:
            st.toggle("SAR抛物线", key="toggle_sar")
            st.session_state.show_sar = st.session_state.toggle_sar
        with main_col6:
            st.toggle("斐波那契回撤", key="toggle_fibonacci")
            st.session_state.show_fibonacci = st.session_state.toggle_fibonacci

        # 主图指标控制 - 第三行（新增）
        main_col7, main_col8, main_col9 = st.columns(3)
        with main_col7:
            st.toggle("一目均衡表", key="toggle_ichimoku")
            st.session_state.show_ichimoku = st.session_state.toggle_ichimoku
        with main_col8:
            st.toggle("TD序列标注", key="toggle_td_sequential")
            st.session_state.show_td_sequential = st.session_state.toggle_td_sequential
        with main_col9:
            chart_type_options = ["普通K线", "平均K线(Heikin-Ashi)"]
            ct_idx = 1 if st.session_state.get("chart_type") == "heikin_ashi" else 0
            ct_sel = st.selectbox("K线类型", chart_type_options, index=ct_idx, key="select_chart_type")
            st.session_state.chart_type = "heikin_ashi" if ct_sel == "平均K线(Heikin-Ashi)" else "candle"

        # 主图指标控制 - 第四行
        main_col10, main_col11, main_col12 = st.columns(3)
        with main_col10:
            st.toggle("Elliott波浪", key="toggle_elliott_wave")
            st.session_state.show_elliott_wave = st.session_state.toggle_elliott_wave

        st.markdown('<p style="color:#00B4D8;font-size:13px;font-weight:bold;margin-top:12px">副图指标</p>',
                    unsafe_allow_html=True)

        # 副图指标选择
        sec_options = ["MACD", "RSI", "KDJ", "CCI", "WR", "DMI", "MOM", "STOCH", "VOLATILITY", "VOL", "OBV", "None"]
        sec_labels = {
            "MACD": "MACD", "RSI": "RSI", "KDJ": "KDJ", "CCI": "CCI",
            "WR": "威廉指标(WR)", "DMI": "DMI趋向指标", "MOM": "动量(MOM)",
            "STOCH": "随机指标(Stoch)",
            "VOLATILITY": "波动率", "VOL": "成交量", "OBV": "OBV能量潮", "None": "无"
        }

        def _safe_index(value, options, fallback=0):
            return options.index(value) if value in options else fallback

        sec_col1, sec_col2, sec_col3, sec_col4 = st.columns(4)
        with sec_col1:
            st.session_state.secondary_indicator_1 = st.selectbox(
                "副图1", sec_options,
                index=_safe_index(st.session_state.secondary_indicator_1, sec_options),
                format_func=lambda x: sec_labels.get(x, x),
                key="select_sec1"
            )
        with sec_col2:
            st.session_state.secondary_indicator_2 = st.selectbox(
                "副图2", sec_options,
                index=_safe_index(st.session_state.secondary_indicator_2, sec_options, 1),
                format_func=lambda x: sec_labels.get(x, x),
                key="select_sec2"
            )
        with sec_col3:
            st.session_state.secondary_indicator_3 = st.selectbox(
                "副图3", sec_options,
                index=_safe_index(st.session_state.secondary_indicator_3, sec_options, 2),
                format_func=lambda x: sec_labels.get(x, x),
                key="select_sec3"
            )
        with sec_col4:
            st.session_state.secondary_indicator_4 = st.selectbox(
                "副图4", sec_options,
                index=_safe_index(st.session_state.secondary_indicator_4, sec_options, 9),
                format_func=lambda x: sec_labels.get(x, x),
                key="select_sec4"
            )

        if st.button("🔄 重置为默认", key="reset_indicators"):
            for key, default_value in indicator_defaults.items():
                st.session_state[key] = default_value
            # 同步重置 toggle keys
            for toggle_key, source_key in toggle_key_map.items():
                st.session_state[toggle_key] = indicator_defaults[source_key]
            st.rerun()

    # 创建配置对象传递给图表构建函数
    config = IndicatorConfig()
    config.show_ema_inner = st.session_state.show_ema_inner
    config.show_ema_outer = st.session_state.show_ema_outer
    config.show_bollinger = st.session_state.show_bollinger
    config.show_vwap = st.session_state.show_vwap
    config.show_sar = st.session_state.show_sar
    config.show_fibonacci = st.session_state.show_fibonacci
    config.secondary_indicator_1 = st.session_state.secondary_indicator_1
    config.secondary_indicator_2 = st.session_state.secondary_indicator_2
    config.secondary_indicator_3 = st.session_state.secondary_indicator_3
    config.secondary_indicator_4 = st.session_state.secondary_indicator_4
    config.show_ichimoku = st.session_state.get("show_ichimoku", False)
    config.show_td_sequential = st.session_state.get("show_td_sequential", False)
    config.show_elliott_wave = st.session_state.get("show_elliott_wave", False)
    config.chart_type = st.session_state.get("chart_type", "candle")

    # 生成图表
    with st.spinner("生成图表..."):
        fig = build_chart(df, ticker=ticker, period=period, config=config)
        # 叠加K线形态标注 + 背离连线
        fig = annotate_patterns_on_chart(fig, df, analysis)
        # 叠加买卖信号标注
        comp_signals = analysis.get("composite_signals", {})
        if comp_signals:
            fig = annotate_buy_sell_signals(fig, df, comp_signals)
        # 叠加支撑压力带
        sr_data = analysis.get("multi_tf_sr", {}).get("daily", {})
        if sr_data:
            fig = annotate_sr_bands(fig, sr_data, analysis.get("close", 0))
    st.plotly_chart(fig, width='stretch')

    # ── 精确交易建议面板 ──────────────────────────────────────────────
    comp_signals = analysis.get("composite_signals", {})
    if comp_signals and comp_signals.get("trade_suggestion"):
        suggestion = comp_signals["trade_suggestion"]
        action = suggestion.get("action", "观望")
        if action != "观望":
            sig_strength = comp_signals.get("signal_strength", "弱")
            with st.expander(f"🎯 精确交易建议 — {action}({sig_strength})", expanded=True):
                # 信号badge
                if action == "买入":
                    badge_bg = "#26A69A"
                elif action == "卖出":
                    badge_bg = "#EF5350"
                else:
                    badge_bg = "#FF9800"
                st.markdown(f'<div style="background:{badge_bg};color:white;padding:8px 16px;border-radius:8px;text-align:center;font-size:16px;font-weight:bold;margin-bottom:12px">{action} — 强度: {sig_strength}</div>', unsafe_allow_html=True)

                # 入场/止损/止盈
                tc = st.columns(5)
                entry = suggestion.get("entry_zone", [0, 0])
                with tc[0]:
                    st.markdown(f'<div style="text-align:center;padding:6px;border:1px solid #333;border-radius:6px"><span style="color:#888;font-size:11px">入场区间</span><br><span style="color:#E0E0E0;font-size:13px;font-weight:bold">{entry[0]:.2f}-{entry[1]:.2f}</span></div>', unsafe_allow_html=True)
                with tc[1]:
                    sl = suggestion.get("stop_loss", 0)
                    st.markdown(f'<div style="text-align:center;padding:6px;border:1px solid #333;border-radius:6px"><span style="color:#888;font-size:11px">止损</span><br><span style="color:#EF5350;font-size:13px;font-weight:bold">{sl:.2f}</span></div>', unsafe_allow_html=True)
                with tc[2]:
                    tp1 = suggestion.get("take_profit_1", 0)
                    st.markdown(f'<div style="text-align:center;padding:6px;border:1px solid #333;border-radius:6px"><span style="color:#888;font-size:11px">止盈1</span><br><span style="color:#26A69A;font-size:13px;font-weight:bold">{tp1:.2f}</span></div>', unsafe_allow_html=True)
                with tc[3]:
                    tp2 = suggestion.get("take_profit_2", 0)
                    st.markdown(f'<div style="text-align:center;padding:6px;border:1px solid #333;border-radius:6px"><span style="color:#888;font-size:11px">止盈2</span><br><span style="color:#26A69A;font-size:13px;font-weight:bold">{tp2:.2f}</span></div>', unsafe_allow_html=True)
                with tc[4]:
                    rr = suggestion.get("risk_reward_ratio", 0)
                    rr_color = "#26A69A" if rr >= 1.5 else ("#FF9800" if rr >= 1.0 else "#EF5350")
                    st.markdown(f'<div style="text-align:center;padding:6px;border:1px solid #333;border-radius:6px"><span style="color:#888;font-size:11px">风险收益比</span><br><span style="color:{rr_color};font-size:13px;font-weight:bold">{rr:.2f}</span></div>', unsafe_allow_html=True)

                risk_pct = suggestion.get("risk_pct", 0)
                st.caption(f"风险距离: {risk_pct:.2f}% | ATR: {suggestion.get('atr', 'N/A')}")

    # ── 买入操作计划面板 ────────────────────────────────────────────────
    _action_plan = analysis.get("buy_action_plan")
    if _action_plan:
        with st.expander("📋 买入操作计划（如果现在买入…）", expanded=True):
            try:
                _bs = _action_plan["buy_score"]
                _tgt = _action_plan["targets"]
                _dp = _action_plan["daily_plan"]

                # ── 综合评分头部 ──
                _grade = _bs["grade"]
                _total = _bs["total_score"]
                _grade_colors = {"A": "#26A69A", "B": "#66BB6A", "C": "#FF9800", "D": "#EF5350", "F": "#B71C1C"}
                _gc = _grade_colors.get(_grade, "#888")
                st.markdown(f"""
                <div style="display:flex;align-items:center;gap:16px;margin-bottom:12px">
                  <div style="background:{_gc};color:white;width:56px;height:56px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:24px;font-weight:bold">{_grade}</div>
                  <div>
                    <div style="color:#E0E0E0;font-size:18px;font-weight:bold">综合评分 {_total:.0f}/100</div>
                    <div style="color:#AAA;font-size:13px">{_bs['verdict']}</div>
                  </div>
                </div>""", unsafe_allow_html=True)

                # ── 六维雷达指标条 ──
                _dims = _bs["dimensions"]
                _dim_cols = st.columns(6)
                for idx_d, (dk, dv) in enumerate(_dims.items()):
                    with _dim_cols[idx_d]:
                        _ds = dv["score"]
                        _dc = "#26A69A" if _ds >= 65 else ("#FF9800" if _ds >= 45 else "#EF5350")
                        st.markdown(f"""
                        <div style="text-align:center;padding:4px">
                          <div style="color:#888;font-size:10px">{dv['label']}</div>
                          <div style="background:#333;border-radius:4px;height:6px;margin:4px 0">
                            <div style="background:{_dc};height:6px;border-radius:4px;width:{min(_ds,100):.0f}%"></div>
                          </div>
                          <div style="color:{_dc};font-size:12px;font-weight:bold">{_ds:.0f}</div>
                        </div>""", unsafe_allow_html=True)

                st.markdown("---")

                # ── 止盈止损价位卡片 ──
                st.markdown('<div style="color:#E0E0E0;font-size:14px;font-weight:bold;margin-bottom:8px">🎯 关键价位</div>', unsafe_allow_html=True)
                _pc = st.columns(6)
                _price_cards = [
                    ("入场价", _tgt["entry_price"], "#E0E0E0", ""),
                    ("止损", _tgt["stop_loss"], "#EF5350", f"-{_tgt['stop_loss_pct']:.1f}%"),
                    ("目标1", _tgt["take_profit_1"], "#26A69A", f"+{_tgt['tp1_pct']:.1f}%"),
                    ("目标2", _tgt["take_profit_2"], "#26A69A", f"+{_tgt['tp2_pct']:.1f}%"),
                    ("目标3", _tgt["take_profit_3"], "#66BB6A", f"+{_tgt['tp3_pct']:.1f}%"),
                    ("风险收益比", _tgt["risk_reward_1"], "#FF9800" if _tgt["risk_reward_1"] < 1.5 else "#26A69A", f"1:{_tgt['risk_reward_1']:.1f}"),
                ]
                for idx_p, (plabel, pval, pcolor, psub) in enumerate(_price_cards):
                    with _pc[idx_p]:
                        _disp = f"{pval:.2f}" if idx_p < 5 else psub
                        _sub_html = f'<div style="color:#888;font-size:10px">{psub}</div>' if idx_p < 5 else ""
                        st.markdown(f"""
                        <div style="text-align:center;padding:6px;border:1px solid #444;border-radius:6px;background:#1A1A2E">
                          <div style="color:#888;font-size:10px">{plabel}</div>
                          <div style="color:{pcolor};font-size:14px;font-weight:bold">{_disp}</div>
                          {_sub_html}
                        </div>""", unsafe_allow_html=True)

                _tgt_method_txt = f"止损依据: {_tgt['stop_method']} | TP1: {_tgt['tp1_method']} | TP2: {_tgt['tp2_method']} | ATR: {_tgt['atr']:.3f}"
                st.caption(_tgt_method_txt)

                st.markdown("---")

                # ── 逐日操作计划 ──
                st.markdown('<div style="color:#E0E0E0;font-size:14px;font-weight:bold;margin-bottom:8px">📅 持仓操作计划（T+0 至 T+5）</div>', unsafe_allow_html=True)

                for day_plan in _dp:
                    _day = day_plan["day"]
                    _title = day_plan["title"]
                    _actions = day_plan["actions"]
                    _pos_adv = day_plan["position_advice"]
                    _watch = day_plan.get("watch_levels", {})

                    # 日期色带
                    _day_colors = ["#26A69A", "#2196F3", "#FF9800", "#AB47BC", "#EC407A", "#78909C"]
                    _dcolor = _day_colors[_day] if _day < len(_day_colors) else "#666"

                    _watch_html = ""
                    if _watch:
                        _wl = " | ".join([f"{wk}: {wv:.2f}" for wk, wv in _watch.items()])
                        _watch_html = f'<div style="color:#888;font-size:11px;margin-top:4px">关注价位: {_wl}</div>'

                    _actions_html = "".join([f'<div style="color:#CCC;font-size:12px;padding:2px 0">• {a}</div>' for a in _actions])

                    st.markdown(f"""
                    <div style="border-left:3px solid {_dcolor};padding:8px 12px;margin-bottom:8px;background:#1A1A2E;border-radius:0 6px 6px 0">
                      <div style="display:flex;justify-content:space-between;align-items:center">
                        <span style="color:{_dcolor};font-size:13px;font-weight:bold">{_title}</span>
                        <span style="color:#888;font-size:11px;background:#333;padding:2px 8px;border-radius:10px">{_pos_adv}</span>
                      </div>
                      {_actions_html}
                      {_watch_html}
                    </div>""", unsafe_allow_html=True)

                # ── 底部风险提示 ──
                st.markdown("""
                <div style="background:#2A1A1A;border:1px solid #5A3030;border-radius:6px;padding:8px 12px;margin-top:8px">
                  <span style="color:#EF5350;font-size:12px;font-weight:bold">⚠️ 风险提示:</span>
                  <span style="color:#AAA;font-size:11px"> 以上为基于技术指标的量化分析，不构成投资建议。市场存在不可预见风险，请严格执行止损纪律，控制单笔亏损在总资金2%以内。</span>
                </div>""", unsafe_allow_html=True)

            except Exception as _plan_err:
                st.warning(f"操作计划渲染异常: {_plan_err}")

    # ── 多周期趋势共振仪表盘 ────────────────────────────────────────────
    mtf_summary = analysis.get("multi_timeframe_summary")
    if mtf_summary and mtf_summary.get("alignment") != "数据不足":
        with st.expander("🔄 多周期趋势共振", expanded=False):
            try:
                # 共振总结badge
                alignment = mtf_summary.get("alignment", "—")
                dominant = mtf_summary.get("dominant_trend", "—")
                align_score = mtf_summary.get("alignment_score", 0)
                avg_score = mtf_summary.get("avg_score", 0)
                if "多" in alignment:
                    badge_color = "#4CAF50"
                elif "空" in alignment:
                    badge_color = "#F44336"
                else:
                    badge_color = "#FF9800"
                st.markdown(f'<div style="background:{badge_color};color:white;padding:8px 16px;border-radius:8px;text-align:center;font-size:16px;font-weight:bold;margin-bottom:12px">🎯 {alignment} — 共振度 {align_score}% | 均分 {avg_score}</div>', unsafe_allow_html=True)

                # 三列展示各周期
                mtf_analyses = mtf_summary.get("timeframes", {})
                tf_names = list(mtf_analyses.keys())
                if tf_names:
                    mtf_cols = st.columns(len(tf_names))
                    for i, tf_name in enumerate(tf_names):
                        tf_data = mtf_analyses[tf_name]
                        with mtf_cols[i]:
                            trend = tf_data.get("trend", "—")
                            strength = tf_data.get("strength", 0)
                            if "多" in trend:
                                arrow, color = "▲", "#4CAF50"
                            elif "空" in trend:
                                arrow, color = "▼", "#F44336"
                            else:
                                arrow, color = "◆", "#FF9800"
                            st.markdown(f'<div style="text-align:center;padding:8px;border:1px solid #333;border-radius:8px"><span style="color:#888;font-size:12px">{tf_name}</span><br><span style="color:{color};font-size:22px;font-weight:bold">{arrow} {trend}</span><br><span style="color:#888;font-size:11px">强度 {strength} | {tf_data.get("ema_alignment","")}</span><br><span style="color:#888;font-size:10px">{tf_data.get("macd_direction","")} | {tf_data.get("rsi_zone","")}</span></div>', unsafe_allow_html=True)
            except Exception as e:
                st.caption(f"多周期分析暂不可用: {e}")

    # ── 一目均衡表信号面板 ──────────────────────────────────────────────
    ichimoku_data = analysis.get("ichimoku", {})
    if ichimoku_data and ichimoku_data.get("tk_cross"):
        with st.expander("☁️ 一目均衡表信号", expanded=False):
            ichi_cols = st.columns(4)
            with ichi_cols[0]:
                tk = ichimoku_data.get("tk_cross", "—")
                tk_color = "#4CAF50" if "金叉" in tk or "多头" in tk else "#F44336" if "死叉" in tk or "空头" in tk else "#FF9800"
                st.markdown(f'<div style="text-align:center;padding:6px;border:1px solid #333;border-radius:6px"><span style="color:#888;font-size:11px">TK交叉</span><br><span style="color:{tk_color};font-size:14px;font-weight:bold">{tk}</span></div>', unsafe_allow_html=True)
            with ichi_cols[1]:
                cp = ichimoku_data.get("cloud_position", "—")
                cp_color = "#4CAF50" if "多头" in cp else "#F44336" if "空头" in cp else "#FF9800"
                st.markdown(f'<div style="text-align:center;padding:6px;border:1px solid #333;border-radius:6px"><span style="color:#888;font-size:11px">云带位置</span><br><span style="color:{cp_color};font-size:14px;font-weight:bold">{cp}</span></div>', unsafe_allow_html=True)
            with ichi_cols[2]:
                cc = ichimoku_data.get("cloud_color", "—")
                cc_color = "#4CAF50" if "绿" in cc else "#F44336"
                st.markdown(f'<div style="text-align:center;padding:6px;border:1px solid #333;border-radius:6px"><span style="color:#888;font-size:11px">云带颜色</span><br><span style="color:{cc_color};font-size:14px;font-weight:bold">{cc}</span></div>', unsafe_allow_html=True)
            with ichi_cols[3]:
                tenkan_v = ichimoku_data.get("tenkan", "—")
                kijun_v = ichimoku_data.get("kijun", "—")
                st.markdown(f'<div style="text-align:center;padding:6px;border:1px solid #333;border-radius:6px"><span style="color:#888;font-size:11px">关键价位</span><br><span style="color:#26A69A;font-size:12px">转换{tenkan_v}</span><br><span style="color:#EF5350;font-size:12px">基准{kijun_v}</span></div>', unsafe_allow_html=True)

    # ── TD序列信号面板 ─────────────────────────────────────────────────
    td_data = analysis.get("td_sequential", {})
    if td_data and td_data.get("status"):
        with st.expander("🔢 TD序列 (Tom DeMark)", expanded=False):
            td_cols = st.columns(3)
            with td_cols[0]:
                status = td_data.get("status", "—")
                if "买" in status:
                    s_color = "#4CAF50"
                elif "卖" in status:
                    s_color = "#F44336"
                else:
                    s_color = "#888"
                st.markdown(f'<div style="text-align:center;padding:8px;border:1px solid #333;border-radius:6px"><span style="color:#888;font-size:11px">当前状态</span><br><span style="color:{s_color};font-size:16px;font-weight:bold">{status}</span></div>', unsafe_allow_html=True)
            with td_cols[1]:
                last_sig = td_data.get("last_signal_type", "无")
                sig_date = td_data.get("last_signal_date", "—")
                if sig_date and len(sig_date) > 10:
                    sig_date = sig_date[:10]
                sig_color = "#4CAF50" if "买入" in last_sig else "#F44336" if "卖出" in last_sig else "#888"
                st.markdown(f'<div style="text-align:center;padding:8px;border:1px solid #333;border-radius:6px"><span style="color:#888;font-size:11px">最近信号</span><br><span style="color:{sig_color};font-size:13px;font-weight:bold">{last_sig}</span><br><span style="color:#666;font-size:10px">{sig_date}</span></div>', unsafe_allow_html=True)
            with td_cols[2]:
                bars_since = td_data.get("bars_since_signal", "—")
                st.markdown(f'<div style="text-align:center;padding:8px;border:1px solid #333;border-radius:6px"><span style="color:#888;font-size:11px">距信号</span><br><span style="color:#FFD700;font-size:18px;font-weight:bold">{bars_since}</span><br><span style="color:#666;font-size:10px">根K线</span></div>', unsafe_allow_html=True)

    # ── 行业对比分析面板 ────────────────────────────────────────────────
    with st.expander("📊 行业对比分析", expanded=False):
        try:
            from modules.sector_comparison import get_sector_benchmark, run_sector_comparison
            from modules.data_router import fetch_all as _fetch_all2

            bench_ticker, bench_name = get_sector_benchmark(ticker, info)
            bench_data = _fetch_all2(bench_ticker, period)
            if bench_data and "ohlcv" in bench_data and not bench_data["ohlcv"].empty:
                sector_result = run_sector_comparison(df, bench_data["ohlcv"], bench_name)
                if "error" not in sector_result:
                    st.markdown(f'<span style="color:#888;font-size:12px">对比基准: <b style="color:#00B4D8">{bench_name}</b></span>', unsafe_allow_html=True)

                    # 强弱badge
                    sl = sector_result.get("strength_label", "—")
                    sl_color = "#4CAF50" if "跑赢" in sl else "#F44336" if "跑输" in sl else "#FF9800"
                    rs_trend = sector_result.get("rs_trend", "—")
                    st.markdown(f'<div style="display:inline-block;background:{sl_color};color:white;padding:4px 12px;border-radius:12px;font-size:13px;font-weight:bold;margin:4px 0">{sl} | RS趋势{rs_trend}</div>', unsafe_allow_html=True)

                    # 各窗口超额收益
                    perf = sector_result.get("performance", {})
                    if perf:
                        perf_data = []
                        for window, data in perf.items():
                            excess = data.get("excess_return", 0)
                            perf_data.append({
                                "窗口": window,
                                "个股收益": f"{data.get('stock_return',0):.2f}%",
                                "行业收益": f"{data.get('benchmark_return',0):.2f}%",
                                "超额收益": f"{excess:+.2f}%",
                            })
                        if perf_data:
                            st.dataframe(pd.DataFrame(perf_data), hide_index=True, width="stretch")
                else:
                    st.caption(sector_result.get("error", "数据不足"))
            else:
                st.caption(f"无法获取基准数据 ({bench_ticker})")
        except Exception as e:
            st.caption(f"行业对比暂不可用: {e}")

    # ── 蒙特卡洛模拟面板 ───────────────────────────────────────────────
    with st.expander("🎲 蒙特卡洛价格模拟", expanded=False):
        try:
            from modules.risk_metrics import run_monte_carlo
            import plotly.graph_objects as go

            mc_result = run_monte_carlo(df, n_simulations=1000, n_days=60)
            if mc_result and mc_result.get("median_price"):
                current_p = mc_result["current_price"]
                median_p = mc_result["median_price"]
                prob_profit = mc_result.get("prob_profit", 0)

                # 顶部指标
                mc_top = st.columns(4)
                with mc_top[0]:
                    st.metric("当前价格", f"{current_p:.2f}")
                with mc_top[1]:
                    diff_pct = (median_p / current_p - 1) * 100
                    st.metric("60日中位预测", f"{median_p:.2f}", f"{diff_pct:+.1f}%")
                with mc_top[2]:
                    pp_color = "normal" if prob_profit >= 50 else "inverse"
                    st.metric("盈利概率", f"{prob_profit:.1f}%")
                with mc_top[3]:
                    st.metric("亏损>10%概率", f"{mc_result.get('prob_loss_10pct', 0):.1f}%")

                # 概率扇形图
                pct_paths = mc_result.get("percentile_paths", {})
                if pct_paths:
                    mc_fig = go.Figure()
                    days_x = list(range(len(pct_paths.get(50, []))))

                    # 5%-95% 区间
                    if 5 in pct_paths and 95 in pct_paths:
                        mc_fig.add_trace(go.Scatter(x=days_x, y=pct_paths[95], mode="lines", line=dict(width=0), showlegend=False))
                        mc_fig.add_trace(go.Scatter(x=days_x, y=pct_paths[5], mode="lines", line=dict(width=0), fill="tonexty", fillcolor="rgba(100,181,246,0.15)", name="5%-95%区间"))

                    # 25%-75% 区间
                    if 25 in pct_paths and 75 in pct_paths:
                        mc_fig.add_trace(go.Scatter(x=days_x, y=pct_paths[75], mode="lines", line=dict(width=0), showlegend=False))
                        mc_fig.add_trace(go.Scatter(x=days_x, y=pct_paths[25], mode="lines", line=dict(width=0), fill="tonexty", fillcolor="rgba(100,181,246,0.3)", name="25%-75%区间"))

                    # 中位数线
                    if 50 in pct_paths:
                        mc_fig.add_trace(go.Scatter(x=days_x, y=pct_paths[50], mode="lines", line=dict(color="#2196F3", width=2), name="中位数"))

                    # 当前价格线
                    mc_fig.add_hline(y=current_p, line_dash="dash", line_color="#FF9800", annotation_text=f"当前 {current_p:.2f}")

                    mc_fig.update_layout(
                        title="60日蒙特卡洛模拟 (1000次)",
                        xaxis_title="天数", yaxis_title="价格",
                        paper_bgcolor="#1A1A2E", plot_bgcolor="#16213E",
                        font=dict(color="#C0C0C0", size=10),
                        height=350, margin=dict(l=50, r=30, t=40, b=40),
                    )
                    st.plotly_chart(mc_fig, width="stretch")

                # 价格预测表
                targets = mc_result.get("targets", [])
                if targets:
                    target_data = []
                    for t in targets:
                        target_data.append({
                            "预测天数": f"{t['days']}天",
                            "悲观(5%)": f"{t['p5']:.2f}",
                            "偏低(25%)": f"{t['p25']:.2f}",
                            "中位(50%)": f"{t['p50']:.2f}",
                            "偏高(75%)": f"{t['p75']:.2f}",
                            "乐观(95%)": f"{t['p95']:.2f}",
                        })
                    st.dataframe(pd.DataFrame(target_data), hide_index=True, width="stretch")
            else:
                st.caption("数据不足，无法进行蒙特卡洛模拟")
        except Exception as e:
            st.caption(f"蒙特卡洛模拟暂不可用: {e}")

# ── Tab2：基本面看板 ──────────────────────────────────────────────────────
with tab_fund:
    st.markdown('<p style="color:#00B4D8;font-size:13px;font-weight:bold">估值指标</p>',
                unsafe_allow_html=True)
    v_cols = st.columns(4)
    v_data = [
        ("PE (TTM)",        fmt_num(fundamental.get("PE"))),
        ("PB",              fmt_num(fundamental.get("PB"))),
        ("PS",              fmt_num(fundamental.get("PS"))),
        ("股息率",          fmt_pct(fundamental.get("dividend_yield"))),
    ]
    for col, (label, val) in zip(v_cols, v_data):
        with col:
            render_metric_card(label, val)

    st.markdown('<p style="color:#00B4D8;font-size:13px;font-weight:bold;margin-top:12px">盈利能力</p>',
                unsafe_allow_html=True)
    p_cols = st.columns(5)
    p_data = [
        ("ROE",       fmt_pct(fundamental.get("ROE"))),
        ("净利率",    fmt_pct(fundamental.get("net_margin"))),
        ("毛利率",    fmt_pct(fundamental.get("gross_margin"))),
        ("营业利润率",fmt_pct(fundamental.get("op_margin"))),
        ("营收增速",  fmt_pct(fundamental.get("rev_growth"))),
    ]
    for col, (label, val) in zip(p_cols, p_data):
        with col:
            render_metric_card(label, val)

    st.markdown('<p style="color:#00B4D8;font-size:13px;font-weight:bold;margin-top:12px">财务健康</p>',
                unsafe_allow_html=True)
    h_cols = st.columns(4)
    h_data = [
        ("资产负债率",  fmt_pct(fundamental.get("debt_ratio"))),
        ("流动比率",    fmt_num(fundamental.get("current_ratio"))),
        ("速动比率",    fmt_num(fundamental.get("quick_ratio"))),
        ("总现金",      fmt_large(fundamental.get("total_cash"))),
    ]
    for col, (label, val) in zip(h_cols, h_data):
        with col:
            render_metric_card(label, val)

    st.markdown('<p style="color:#00B4D8;font-size:13px;font-weight:bold;margin-top:12px">公司信息</p>',
                unsafe_allow_html=True)
    st.markdown(
        f'<div class="trend-panel">'
        f'<span style="color:#888">行业：</span>{fundamental.get("sector","—")} · {fundamental.get("industry","—")}'
        f'&nbsp;&nbsp;&nbsp;<span style="color:#888">货币：</span>{fundamental.get("currency","—")}'
        f'</div>',
        unsafe_allow_html=True,
    )

# ── Tab3：信息面分析 ──────────────────────────────────────────────────────
with tab_info:
    st.markdown('<p style="color:#00B4D8;font-size:14px;font-weight:bold">📰 信息面综合分析</p>',
                unsafe_allow_html=True)
    st.markdown(
        '<p style="color:#888;font-size:12px">整合基本面、资金面、市场情绪、公司事件等多维度信息</p>',
        unsafe_allow_html=True
    )

    # ── 市场情绪面板 ──
    try:
        market_type = data.get("market", "A股") if data else "A股"
        sentiment_data = _cached_sentiment(ticker, market_type)

        with st.expander("🌡️ 市场情绪指标", expanded=True):
            sent_cols = st.columns(4)

            # VIX
            vix = sentiment_data.get("vix", {})
            with sent_cols[0]:
                if vix.get("available"):
                    vix_val = vix.get("value", 0)
                    vix_color = "#F6465D" if vix_val > 25 else ("#FFD700" if vix_val > 18 else "#0ECB81")
                    render_trend_item("VIX恐慌指数", f"{vix_val:.1f} ({vix.get('level', '')})", vix_color)
                else:
                    render_trend_item("VIX恐慌指数", "N/A", "#555")

            # 综合情绪
            with sent_cols[1]:
                combined = sentiment_data.get("combined_sentiment", "—")
                s_score = sentiment_data.get("sentiment_score", 0)
                s_color = "#0ECB81" if s_score > 15 else ("#F6465D" if s_score < -15 else "#888")
                render_trend_item("综合情绪", combined, s_color)

            # 北向资金
            nb = sentiment_data.get("northbound_flow", {})
            with sent_cols[2]:
                if nb.get("available"):
                    nb_today = nb.get("today_net", 0)
                    nb_color = "#0ECB81" if nb_today > 0 else "#F6465D"
                    render_trend_item("北向资金(今日)", f"{'+'if nb_today>0 else ''}{nb_today:.1f}亿", nb_color)
                else:
                    render_trend_item("北向资金", "N/A", "#555")

            # 融资融券
            margin = sentiment_data.get("margin_balance", {})
            with sent_cols[3]:
                if margin.get("available"):
                    m_chg = margin.get("change_pct", 0)
                    m_color = "#0ECB81" if m_chg > 0 else "#F6465D"
                    render_trend_item("融资余额变动", f"{m_chg:+.2f}%", m_color)
                else:
                    render_trend_item("融资融券", "N/A", "#555")

            # 详细解读
            if vix.get("available"):
                st.markdown(f"<div style='color:#aaa;font-size:11px'>📌 {vix.get('interpretation', '')}</div>", unsafe_allow_html=True)
            if nb.get("available"):
                st.markdown(f"<div style='color:#aaa;font-size:11px'>📌 {nb.get('interpretation', '')}</div>", unsafe_allow_html=True)

        st.markdown("---")
    except Exception:
        pass

    # 运行信息面分析
    try:
        info_analysis = run_info_analysis(ticker, info, df, fundamental, analysis)

        # ── 综合评分看板 ──
        st.markdown("##### 📊 综合信息面评分")
        score_cols = st.columns(4)
        with score_cols[0]:
            render_trend_item(
                "信息面总评",
                f"{info_analysis['overall_rating']} [{info_analysis['overall_score']:.0f}/100]",
                info_analysis['overall_color']
            )
        with score_cols[1]:
            fa = info_analysis['fundamental']
            render_trend_item("基本面", f"{fa['rating']} [{fa['overall_score']:.0f}]", fa['rating_color'])
        with score_cols[2]:
            cf = info_analysis['capital_flow']
            render_trend_item("资金面", f"{cf['flow_rating']} [{cf['flow_score']:.0f}]", cf['flow_color'])
        with score_cols[3]:
            se = info_analysis['sentiment']
            render_trend_item("市场情绪", f"{se['sentiment']} [{se['sentiment_score']:.0f}]", se['sentiment_color'])

        # ── 公司概览卡片 ──
        st.markdown("---")
        st.markdown("##### 🏢 公司概览")
        ev = info_analysis['events']
        overview_cols = st.columns(4)
        with overview_cols[0]:
            render_metric_card("行业", f"{ev.get('sector', '—')}")
        with overview_cols[1]:
            render_metric_card("细分行业", f"{ev.get('industry', '—')}")
        with overview_cols[2]:
            render_metric_card("市场地位", f"{ev.get('market_position', '—')}")
        with overview_cols[3]:
            render_metric_card("货币", f"{fundamental.get('currency', '—')}")

        # 52周位置进度条
        w52_pos = ev.get('52w_position')
        high_52w = fundamental.get('52w_high')
        low_52w = fundamental.get('52w_low')
        if w52_pos is not None and high_52w and low_52w:
            st.markdown(f"**52周价格位置：** {low_52w:.2f} → <span style='color:#00B4D8;font-weight:bold'>{current_price:.2f}</span> → {high_52w:.2f}",
                        unsafe_allow_html=True)
            st.progress(min(max(w52_pos, 0.0), 1.0), text=f"当前处于52周 {w52_pos*100:.1f}% 位置")
        elif high_52w and low_52w and current_price:
            _range = high_52w - low_52w
            if _range > 0:
                _pos = (current_price - low_52w) / _range
                st.markdown(f"**52周价格位置：** {low_52w:.2f} → <span style='color:#00B4D8;font-weight:bold'>{current_price:.2f}</span> → {high_52w:.2f}",
                            unsafe_allow_html=True)
                st.progress(min(max(_pos, 0.0), 1.0), text=f"当前处于52周 {_pos*100:.1f}% 位置")

        # 公司亮点和风险
        if ev.get('events') or ev.get('risk_factors'):
            ev_col1, ev_col2 = st.columns(2)
            with ev_col1:
                if ev.get('events'):
                    st.markdown("**✅ 亮点：**")
                    for event in ev['events']:
                        st.markdown(f"• {event}")
            with ev_col2:
                if ev.get('risk_factors'):
                    st.markdown("**⚠️ 风险：**")
                    for risk in ev['risk_factors']:
                        st.markdown(f"• {risk}")

        # ── 基本面核心指标 ──
        st.markdown("---")
        st.markdown("##### 💰 基本面分析")
        fa = info_analysis['fundamental']

        # 评分仪表盘
        score_detail_cols = st.columns(4)
        with score_detail_cols[0]:
            _vc = "#0ECB81" if fa['valuation_score'] >= 60 else ("#FFD700" if fa['valuation_score'] >= 40 else "#F6465D")
            render_trend_item("估值", f"{fa['valuation_score']:.0f}/100", _vc)
        with score_detail_cols[1]:
            _pc = "#0ECB81" if fa['profitability_score'] >= 60 else ("#FFD700" if fa['profitability_score'] >= 40 else "#F6465D")
            render_trend_item("盈利能力", f"{fa['profitability_score']:.0f}/100", _pc)
        with score_detail_cols[2]:
            _gc = "#0ECB81" if fa['growth_score'] >= 60 else ("#FFD700" if fa['growth_score'] >= 40 else "#F6465D")
            render_trend_item("成长性", f"{fa['growth_score']:.0f}/100", _gc)
        with score_detail_cols[3]:
            _hc = "#0ECB81" if fa['health_score'] >= 60 else ("#FFD700" if fa['health_score'] >= 40 else "#F6465D")
            render_trend_item("财务健康", f"{fa['health_score']:.0f}/100", _hc)

        # 核心指标数值
        metric_cols = st.columns(6)
        _metric_items = [
            ("PE (TTM)", fmt_num(fundamental.get("PE"))),
            ("PB", fmt_num(fundamental.get("PB"))),
            ("ROE", fmt_pct(fundamental.get("ROE"))),
            ("净利率", fmt_pct(fundamental.get("net_margin"))),
            ("营收增速", fmt_pct(fundamental.get("rev_growth"))),
            ("负债率", fmt_pct(fundamental.get("debt_ratio"))),
        ]
        for col, (lbl, val) in zip(metric_cols, _metric_items):
            with col:
                render_metric_card(lbl, val)

        # 基本面评语
        if fa['comments']:
            st.markdown("**分析要点：**")
            for comment in fa['comments']:
                st.markdown(f"• {comment}")
        else:
            st.info("基本面数据有限，评分基于默认值。建议通过其他渠道获取详细财务数据。")

        # ── 资金面详情 ──
        st.markdown("---")
        st.markdown("##### 💸 资金面分析")
        cf = info_analysis['capital_flow']

        cf_detail_cols = st.columns(3)
        with cf_detail_cols[0]:
            render_trend_item("资金流向", cf['flow_rating'], cf['flow_color'])
        with cf_detail_cols[1]:
            _vol = df["Volume"].iloc[-1] if not df.empty and "Volume" in df.columns else 0
            _vol_ma20 = df.iloc[-1].get("Vol_MA_20", _vol) if not df.empty else _vol
            _vol_ratio = _vol / _vol_ma20 if _vol_ma20 > 0 else 1
            _vr_color = "#0ECB81" if _vol_ratio > 1.5 else ("#F6465D" if _vol_ratio < 0.5 else "#FFD700")
            render_trend_item("量比(vs 20日均量)", f"{_vol_ratio:.2f}x", _vr_color)
        with cf_detail_cols[2]:
            _obv_bull = analysis.get("obv_bullish")
            _mfi = analysis.get("mfi")
            _mfi_text = f"MFI: {_mfi:.1f}" if _mfi is not None else "—"
            _obv_text = "OBV↑" if _obv_bull else ("OBV↓" if _obv_bull is not None else "—")
            render_trend_item("资金指标", f"{_obv_text} | {_mfi_text}", "#0ECB81" if _obv_bull else "#F6465D")

        if cf['comments']:
            for comment in cf['comments']:
                st.markdown(f"• {comment}")
        else:
            st.info("暂无显著资金流向信号")

        # ── 市场情绪详情 ──
        st.markdown("---")
        st.markdown("##### 😊 市场情绪分析")
        se = info_analysis['sentiment']

        se_detail_cols = st.columns(3)
        with se_detail_cols[0]:
            render_trend_item("情绪指数", f"{se['sentiment']} [{se['sentiment_score']:.0f}]", se['sentiment_color'])
        with se_detail_cols[1]:
            _trend = analysis.get("trend", "震荡")
            _trend_c = "#0ECB81" if _trend == "多头" else ("#F6465D" if _trend == "空头" else "#FFD700")
            render_trend_item("技术面趋势", _trend, _trend_c)
        with se_detail_cols[2]:
            _res = analysis.get("resonance", "—")
            _res_c = analysis.get("resonance_color", "#888")
            render_trend_item("指标共振", _res, _res_c)

        if se['comments']:
            for comment in se['comments']:
                st.markdown(f"• {comment}")

        # ── 综合投资建议 ──
        st.markdown("---")
        st.markdown(
            f'<div class="trend-panel" style="padding:16px;border-left:4px solid {info_analysis["overall_color"]}">'
            f'<div style="color:#888;font-size:12px;margin-bottom:8px">💡 综合投资建议</div>'
            f'<div style="color:{info_analysis["overall_color"]};font-size:16px;font-weight:bold;margin-bottom:8px">'
            f'{info_analysis["investment_advice"]}'
            f'</div>'
            f'<div style="color:#888;font-size:11px">'
            f'基本面({fa["overall_score"]:.0f}) × 35% + 资金面({cf["flow_score"]:.0f}) × 25% + '
            f'情绪({se["sentiment_score"]:.0f}) × 25% + 市场地位 × 15% = <b style="color:{info_analysis["overall_color"]}">{info_analysis["overall_score"]:.1f}</b>'
            f'</div>'
            f'</div>',
            unsafe_allow_html=True
        )

    except Exception as e:
        st.error(f"信息面分析失败: {e}")
        import traceback
        st.code(traceback.format_exc(), language="text")
        st.info("部分数据缺失或分析模块异常，请检查数据源连接")

# ── Tab4：超短线分析 ───────────────────────────────────────────────────────
with tab_intraday:
    st.markdown('<p style="color:#00B4D8;font-size:14px;font-weight:bold">⚡ 超短线+短线趋势分析</p>',
                unsafe_allow_html=True)

    st.markdown(
        '<p style="color:#888;font-size:12px">基于5分钟/30分钟/60分钟K线的日内趋势分析，适合短线交易决策</p>',
        unsafe_allow_html=True
    )

    # 显示小时线短线趋势分析（如果当前是小时线）
    if timeframe == "小时线" and analysis.get("short_term_score") is not None:
        st.markdown("---")
        st.markdown("##### 📊 小时级短线趋势分析")

        short_cols = st.columns(4)
        with short_cols[0]:
            st.metric("短线评分", f"{analysis.get('short_term_score', 0)}")
        with short_cols[1]:
            trend = analysis.get("short_term_trend", "震荡")
            trend_color = analysis.get("short_term_color", "#888")
            st.markdown(f"<span style='color:{trend_color};font-size:18px;font-weight:bold'>{trend}</span>", unsafe_allow_html=True)
        with short_cols[2]:
            st.markdown(f"**操作建议**: {analysis.get('short_term_action', '观望')}")
        with short_cols[3]:
            st.markdown(f"支撑: {analysis.get('short_support', 0):.2f}")
            st.markdown(f"阻力: {analysis.get('short_resistance', 0):.2f}")

        # 显示短线信号
        signals = analysis.get("short_term_signals", [])
        if signals:
            signal_cols = st.columns(min(len(signals), 4))
            for i, sig in enumerate(signals[:4]):
                with signal_cols[i % 4]:
                    emoji = "🟢" if any(k in sig for k in ["向上", "金叉", "超卖", "买入", "增强", "突破"]) else ("🔴" if any(k in sig for k in ["向下", "死叉", "超买", "卖出", "减弱", "跌破"]) else "🟡")
                    st.markdown(f"{emoji} {sig}")

        st.markdown("---")

    # 运行日内分析
    try:
        with st.spinner("获取分钟级数据并分析中..."):
            intraday_analysis = run_intraday_analysis(ticker)

        # 多周期共振看板
        st.markdown("##### 🎯 多周期共振信号")
        resonance = intraday_analysis.get("多周期共振", {})

        res_cols = st.columns(3)
        with res_cols[0]:
            signal = resonance.get("signal", "观望")
            conf = resonance.get("confidence", 50)
            signal_color = "#ff4d4f" if "多" in signal and "空" not in signal else ("#00b578" if "空" in signal else "#8b949e")
            render_trend_item("共振信号", signal, signal_color)
        with res_cols[1]:
            render_trend_item("置信度", f"{conf}%", "#58a6ff")
        with res_cols[2]:
            desc = resonance.get("description", "数据不足")
            render_trend_item("策略建议", desc[:20] + "..." if len(desc) > 20 else desc, "#e6edf3")

        # 各周期详细分析
        st.markdown("---")
        st.markdown("##### 📊 各周期技术分析")

        timeframe_cols = st.columns(3)

        # 超短线(5分钟)
        with timeframe_cols[0]:
            st.markdown("**⏱️ 超短线(5分钟)**")
            analysis_5m = intraday_analysis.get("超短线")
            if analysis_5m:
                trend_color = "#ff4d4f" if "多" in analysis_5m.get("trend", "") else ("#00b578" if "空" in analysis_5m.get("trend", "") else "#8b949e")
                st.markdown(f"趋势: <span style='color:{trend_color};font-weight:600'>{analysis_5m.get('trend', '—')}</span>", unsafe_allow_html=True)
                st.markdown(f"得分: {analysis_5m.get('trend_score', 0)}/100")
                st.markdown(f"RSI: {analysis_5m.get('rsi', '—')}")
                st.markdown(f"MACD: {analysis_5m.get('macd', '—')}")
                if analysis_5m.get('volume_signal'):
                    st.markdown(f"量能: {analysis_5m.get('volume_signal')}")
                if analysis_5m.get('support') and analysis_5m.get('resistance'):
                    st.markdown(f"支撑: {analysis_5m.get('support')}")
                    st.markdown(f"阻力: {analysis_5m.get('resistance')}")
            else:
                st.info("5分钟数据暂不可用")

        # 短线(30分钟)
        with timeframe_cols[1]:
            st.markdown("**⏱️ 短线(30分钟)**")
            analysis_30m = intraday_analysis.get("短线")
            if analysis_30m:
                trend_color = "#ff4d4f" if "多" in analysis_30m.get("trend", "") else ("#00b578" if "空" in analysis_30m.get("trend", "") else "#8b949e")
                st.markdown(f"趋势: <span style='color:{trend_color};font-weight:600'>{analysis_30m.get('trend', '—')}</span>", unsafe_allow_html=True)
                st.markdown(f"得分: {analysis_30m.get('trend_score', 0)}/100")
                st.markdown(f"RSI: {analysis_30m.get('rsi', '—')}")
                st.markdown(f"MACD: {analysis_30m.get('macd', '—')}")
                if analysis_30m.get('volume_signal'):
                    st.markdown(f"量能: {analysis_30m.get('volume_signal')}")
                if analysis_30m.get('support') and analysis_30m.get('resistance'):
                    st.markdown(f"支撑: {analysis_30m.get('support')}")
                    st.markdown(f"阻力: {analysis_30m.get('resistance')}")
            else:
                st.info("30分钟数据暂不可用")

        # 中短线(60分钟)
        with timeframe_cols[2]:
            st.markdown("**⏱️ 中短线(60分钟)**")
            analysis_60m = intraday_analysis.get("中短线")
            if analysis_60m:
                trend_color = "#ff4d4f" if "多" in analysis_60m.get("trend", "") else ("#00b578" if "空" in analysis_60m.get("trend", "") else "#8b949e")
                st.markdown(f"趋势: <span style='color:{trend_color};font-weight:600'>{analysis_60m.get('trend', '—')}</span>", unsafe_allow_html=True)
                st.markdown(f"得分: {analysis_60m.get('trend_score', 0)}/100")
                st.markdown(f"RSI: {analysis_60m.get('rsi', '—')}")
                st.markdown(f"MACD: {analysis_60m.get('macd', '—')}")
                if analysis_60m.get('volume_signal'):
                    st.markdown(f"量能: {analysis_60m.get('volume_signal')}")
                if analysis_60m.get('support') and analysis_60m.get('resistance'):
                    st.markdown(f"支撑: {analysis_60m.get('support')}")
                    st.markdown(f"阻力: {analysis_60m.get('resistance')}")
            else:
                st.info("60分钟数据暂不可用")

        # 交易建议
        st.markdown("---")
        st.markdown("##### 💡 超短线交易建议")

        advice_cols = st.columns(2)
        with advice_cols[0]:
            st.markdown("**入场策略**")
            if resonance.get("signal") and "多" in resonance.get("signal"):
                st.markdown("""
                - ✅ 共振看多，可考虑轻仓试多
                - ✅ 回踩5分钟EMA9支撑时入场
                - ⚠️ 设置严格止损（参考ATR值）
                - ⏱️ 持仓时间：15-60分钟
                """)
            elif resonance.get("signal") and "空" in resonance.get("signal"):
                st.markdown("""
                - ✅ 共振看空，可考虑轻仓试空
                - ✅ 反弹至5分钟EMA9压力时入场
                - ⚠️ 设置严格止损（参考ATR值）
                - ⏱️ 持仓时间：15-60分钟
                """)
            else:
                st.markdown("""
                - ⏸️ 信号不一致，建议观望
                - ⏸️ 等待5分钟与30分钟方向统一
                - ⚠️ 震荡市减少交易频率
                """)

        with advice_cols[1]:
            st.markdown("**风险控制**")
            avg_atr = 0
            if analysis_5m and analysis_5m.get("atr"):
                avg_atr = analysis_5m.get("atr", 0)
            st.markdown(f"""
            - 📊 5分钟ATR: {avg_atr:.2f} (波动参考)
            - 🛑 建议止损: {avg_atr*1.5:.2f} ~ {avg_atr*2:.2f}
            - 💰 单笔风险: 不超过账户2%
            - ⚠️ 重要提示: 超短线交易风险极高
            """)

        # ── 关联资产分析面板 ──
        st.markdown("---")
        st.markdown("##### 🔗 跨资产关联分析")

        corr_data = intraday_analysis.get("关联分析", {})
        if corr_data and not corr_data.get("error"):
            # 资产类别
            asset_class = corr_data.get("asset_class", "未识别")
            st.markdown(f"**资产类别:** {asset_class}")

            # 关联信号总结
            corr_signal = corr_data.get("correlation_signal", "")
            if corr_signal:
                signal_color = "#0ECB81" if "利多" in corr_signal else ("#F6465D" if "利空" in corr_signal else "#888")
                st.markdown(
                    f"<div style='background:#1a1a2e;padding:10px;border-radius:8px;border-left:3px solid {signal_color};margin:8px 0'>"
                    f"<span style='color:{signal_color};font-weight:600'>📊 {corr_signal}</span>"
                    f"</div>",
                    unsafe_allow_html=True
                )

            # 相关性表格
            correlations = corr_data.get("correlations", [])
            if correlations:
                st.markdown("**📈 关联资产相关性**")
                corr_rows = []
                for c in correlations:
                    corr_20d = c.get("corr_20d", 0)
                    corr_rows.append({
                        "资产": c.get("asset", "—"),
                        "20日相关系数": f"{corr_20d:.2f}" if corr_20d else "—",
                        "60日相关系数": f"{c.get('corr_60d', 0):.2f}" if c.get("corr_60d") else "—",
                        "信号": c.get("signal", "—"),
                    })
                st.dataframe(pd.DataFrame(corr_rows), width="stretch", hide_index=True)

            # 关键比率
            ratios = corr_data.get("ratios", [])
            if ratios:
                st.markdown("**⚖️ 关键比率分析**")
                ratio_cols = st.columns(min(len(ratios), 3))
                for i, r in enumerate(ratios[:3]):
                    with ratio_cols[i]:
                        pct = r.get("historical_pct", 0.5)
                        pct_color = "#F6465D" if pct > 0.8 else ("#0ECB81" if pct < 0.2 else "#FFD700")
                        st.markdown(
                            f"<div style='background:#1a1a2e;padding:8px;border-radius:8px;text-align:center'>"
                            f"<div style='color:#888;font-size:11px'>{r.get('name', '—')}</div>"
                            f"<div style='color:#e6edf3;font-size:20px;font-weight:bold'>{r.get('current', 0):.2f}</div>"
                            f"<div style='color:{pct_color};font-size:11px'>历史 {pct:.0%} 分位</div>"
                            f"<div style='color:#666;font-size:10px'>{r.get('signal', '')}</div>"
                            f"</div>",
                            unsafe_allow_html=True
                        )

            # 宏观事件日历
            macro_events = corr_data.get("macro_events", [])
            if macro_events:
                st.markdown("**📅 近期重要宏观事件**")
                for evt in macro_events[:5]:
                    impact = evt.get("impact", "")
                    evt_color = "#F6465D" if impact == "高" else ("#FFD700" if impact == "中" else "#888")
                    st.markdown(
                        f"<span style='color:{evt_color}'>●</span> "
                        f"**{evt.get('date', '—')}** {evt.get('event', '—')} "
                        f"<span style='color:{evt_color};font-size:11px'>({impact}影响)</span>",
                        unsafe_allow_html=True
                    )

            # 多空信号统计
            bull_sig = corr_data.get("bullish_signals", 0)
            bear_sig = corr_data.get("bearish_signals", 0)
            if bull_sig or bear_sig:
                st.markdown(
                    f"<div style='display:flex;gap:16px;margin-top:8px'>"
                    f"<span style='color:#0ECB81'>📈 利多信号: {bull_sig}个</span>"
                    f"<span style='color:#F6465D'>📉 利空信号: {bear_sig}个</span>"
                    f"</div>",
                    unsafe_allow_html=True
                )
        else:
            err_msg = corr_data.get("error", "暂无关联分析数据") if corr_data else "暂无关联分析数据"
            st.info(f"关联分析: {err_msg}")

    except Exception as e:
        st.error(f"超短线分析失败: {e}")
        st.info("分钟级数据获取失败，可能原因：\n1. 当前非交易时间\n2. 该标的暂不支持分钟数据\n3. 数据源限制")

# ── Tab5：消息面分析 ───────────────────────────────────────────────────────
with tab_news:
    st.markdown('<p style="color:#00B4D8;font-size:14px;font-weight:bold">📰 消息面整理分析</p>',
                unsafe_allow_html=True)

    st.markdown(
        '<p style="color:#888;font-size:12px">整合新闻、公告、研报等多维度消息，分析市场情绪</p>',
        unsafe_allow_html=True
    )

    # 运行消息面分析
    try:
        with st.spinner("获取最新消息并分析中..."):
            news_analysis = run_news_analysis(ticker, name)

        # 整体情绪看板
        st.markdown("##### 😊 市场情绪概览")
        sentiment_cols = st.columns(4)

        with sentiment_cols[0]:
            overall = news_analysis.get("整体情绪", "中性")
            color = news_analysis.get("情绪颜色", "#8b949e")
            render_trend_item("整体情绪", overall, color)

        with sentiment_cols[1]:
            score = news_analysis.get("情绪得分", 0)
            render_trend_item("情绪得分", f"{score:+.1f}", "#58a6ff")

        with sentiment_cols[2]:
            pos_ratio = news_analysis.get("积极新闻比例", 0)
            render_trend_item("积极新闻占比", f"{pos_ratio:.0f}%", "#ff4d4f")

        with sentiment_cols[3]:
            neg_ratio = news_analysis.get("消极新闻比例", 0)
            render_trend_item("消极新闻占比", f"{neg_ratio:.0f}%", "#00b578")

        # 重要新闻
        st.markdown("---")
        st.markdown("##### 📰 重要新闻分析")

        key_news = news_analysis.get("重要新闻", [])
        if key_news:
            for news in key_news[:5]:
                emoji = "📈" if news.get("sentiment_score", 0) > 0 else "📉"
                impact_color = "#ff4d4f" if news.get("impact") == "重大影响" else ("#d29922" if news.get("impact") == "较大影响" else "#8b949e")
                with st.expander(f"{emoji} {news.get('title', '无标题')}"):
                    st.markdown(f"**来源:** {news.get('source', '未知')} | **时间:** {news.get('time', '—')}")
                    st.markdown(f"**影响级别:** <span style='color:{impact_color}'>{news.get('impact', '—')}</span>", unsafe_allow_html=True)
                    st.markdown(f"**情感得分:** {news.get('sentiment_score', 0):+.1f}")
        else:
            st.info("暂无重大新闻")

        # 最新新闻列表
        st.markdown("---")
        st.markdown("##### 📋 最新新闻")

        latest_news = news_analysis.get("最新新闻", [])
        if latest_news:
            news_df_data = []
            for news in latest_news[:10]:
                news_df_data.append({
                    "时间": news.get("time", "—")[:10] if news.get("time") else "—",
                    "标题": news.get("title", "—")[:40] + "..." if len(news.get("title", "")) > 40 else news.get("title", "—"),
                    "情感": news.get("sentiment_label", "—"),
                    "影响": news.get("impact", "—"),
                })
            st.dataframe(news_df_data, hide_index=True)
        else:
            st.info("暂无新闻数据")

        # 公司公告
        st.markdown("---")
        st.markdown("##### 📋 最新公告")

        announcements = news_analysis.get("最新公告", [])
        if announcements:
            for ann in announcements[:5]:
                with st.expander(f"📋 [{ann.get('type', '公告')}] {ann.get('title', '无标题')[:40]}..."):
                    st.markdown(f"**发布时间:** {ann.get('time', '—')}")
        else:
            st.info("暂无公告数据")

        # 机构研报
        st.markdown("---")
        st.markdown("##### 📊 机构研报")

        reports = news_analysis.get("最新研报", [])
        if reports:
            report_cols = st.columns(min(len(reports), 3))
            for i, report in enumerate(reports[:3]):
                with report_cols[i]:
                    st.markdown(f"**{report.get('org', '机构')}**")
                    st.markdown(f"评级: **{report.get('rating', '—')}**")
                    if report.get('target_price'):
                        st.markdown(f"目标价: {report.get('target_price')}")
                    st.caption(f"{report.get('time', '')}")
        else:
            st.info("暂无研报数据")

        # 公告类型统计
        st.markdown("---")
        st.markdown("##### 📈 公告类型统计")

        ann_stats = news_analysis.get("公告统计", {})
        if ann_stats:
            st.bar_chart(ann_stats)
        else:
            st.info("暂无统计数据")

    except Exception as e:
        st.error(f"消息面分析失败: {e}")
        st.info("新闻数据获取失败，可能原因：\n1. 网络连接问题\n2. 该标的暂无新闻覆盖\n3. API限制")

# ── Tab6：趋势报告 ────────────────────────────────────────────────────────
with tab_report:
    st.markdown('<p style="color:#00B4D8;font-size:14px;font-weight:bold">📋 技术分析报告</p>',
                unsafe_allow_html=True)

    close_price = df["Close"].iloc[-1]
    report_date = df.index[-1].strftime("%Y-%m-%d")

    # 检查预警
    triggered_alerts = check_alerts(ticker, df)
    if triggered_alerts:
        st.warning("🔔 **预警触发！**")
        for alert in triggered_alerts:
            st.info(f"{alert.message} (价格: {alert.price:.2f})")

    report_md = f"""
**分析日期：** {report_date}　　**标的：** {name} ({ticker})　　**市场：** {market}

---

### 一、趋势方向
- **综合趋势：** {analysis.get("trend_emoji","")} {analysis.get("trend","—")}（{analysis.get("strength","—")}）
- **趋势强度：** {analysis.get("trend_strength_level","—")} [{analysis.get("trend_strength_score",50):.0f}/100]
- **EMA 内隧道状态：** {"多头排列 ✅" if analysis.get("inner_bull") else "非多头排列"}
- **隧道位置：** {analysis.get("tunnel_position","—")}
- **内外隧道关系：** {analysis.get("crossover_signal","—")}

---

### 二、布林带分析
- **上轨：** {analysis.get("bb_upper", 0):.2f}　　**中轨：** {analysis.get("bb_middle", 0):.2f}　　**下轨：** {analysis.get("bb_lower", 0):.2f}
- **当前位置：** {analysis.get("bb_position","—")}（{analysis.get("bb_signal","—")}）
- **带宽状态：** {analysis.get("bb_squeeze_signal","—")}（{analysis.get("bb_bandwidth_trend","—")}）

---

### 三、KDJ指标分析
- **K值：** {analysis.get("kdj_k", 0):.2f}　　**D值：** {analysis.get("kdj_d", 0):.2f}　　**J值：** {analysis.get("kdj_j", 0):.2f}
- **信号：** {analysis.get("kdj_signal","—")}
- **状态：** {"超买区" if analysis.get("kdj_overbought") else ("超卖区" if analysis.get("kdj_oversold") else "常态区")}

---

### 四、斐波那契回撤
- **周期高点：** {analysis.get("fib_high", 0):.2f}　　**周期低点：** {analysis.get("fib_low", 0):.2f}
- **当前价格：** {close_price:.2f}
- **最近支撑位：** {f"{analysis.get('fib_support',0):.2f} ({analysis.get('fib_support_lvl',0)*100:.1f}%)" if analysis.get("fib_support") else "—"}
- **最近压力位：** {f"{analysis.get('fib_resistance',0):.2f} ({analysis.get('fib_resistance_lvl',0)*100:.1f}%)" if analysis.get("fib_resistance") else "—"}

---

### 五、交易区间
- **买入区间：** {f"{analysis.get('buy_zone_low',0):.2f} - {analysis.get('buy_zone_high',0):.2f}" if analysis.get("buy_zone_low") else "—"}
- **目标/卖出区间：** {f"{analysis.get('sell_zone_low',0):.2f} - {analysis.get('sell_zone_high',0):.2f}" if analysis.get("sell_zone_low") else "—"}
- **止损位：** {f"{analysis.get('stop_loss',0):.2f} ({analysis.get('stop_loss_pct',0)*100:.1f}%)" if analysis.get("stop_loss") else "—"}
- **止盈T1/T2/T3：** {f"{analysis.get('take_profit_1',0):.2f} / {analysis.get('take_profit_2',0):.2f} / {analysis.get('take_profit_3',0):.2f}" if analysis.get("take_profit_1") else "—"}
- **盈亏比：** {f"1:{analysis.get('risk_reward_ratio',0):.1f}" if analysis.get("risk_reward_ratio") else "—"}
- **信心评分：** {f"{analysis.get('confidence_score',0):.0f}/100 ({analysis.get('confidence_level','?')})" if analysis.get("confidence_score") else "—"}

---

### 六、支撑与压力位
- **支撑位({analysis.get('support_count',0)}个)：** {", ".join([f"{s:.2f}" for s in analysis.get("support_levels",[])[:4]]) if analysis.get("support_levels") else "—"}
- **压力位({analysis.get('resistance_count',0)}个)：** {", ".join([f"{r:.2f}" for r in analysis.get("resistance_levels",[])[:4]]) if analysis.get("resistance_levels") else "—"}
- **建议仓位：** {analysis.get("position_size", "—")}

---

### 七、多指标信号汇总
{analysis.get("signal_summary","暂无信号")}

---

### 八、估值判断
- **PE (TTM)：** {fmt_num(fundamental.get("PE"))}
- **估值位置：** {analysis.get("valuation","—")}
- **PB：** {fmt_num(fundamental.get("PB"))}　　**ROE：** {fmt_pct(fundamental.get("ROE"))}

---

### 九、RSI 状态
- **RSI(14)：** {f"{analysis.get('rsi_last',0):.1f}" if analysis.get("rsi_last") else "—"}
{"- ⚠️ RSI超买（>70），注意回调风险" if analysis.get("rsi_last") and analysis.get("rsi_last") > 70 else ""}
{"- ✅ RSI超卖（<30），关注反弹机会" if analysis.get("rsi_last") and analysis.get("rsi_last") < 30 else ""}

---

### 十、CCI与WR指标
- **CCI：** {analysis.get("cci",0):.1f} - {analysis.get("cci_signal","—")}
- **WR：** {analysis.get("wr",0):.1f} - {analysis.get("wr_signal","—")}

---

### 十一、DMI趋向分析
- **DI+：** {analysis.get("di_plus",0):.1f}　　**DI-：** {analysis.get("di_minus",0):.1f}　　**ADX：** {analysis.get("adx",0):.1f}
- **信号：** {analysis.get("dmi_signal","—")}

---

### 十二、成交量与资金流向
- **成交量信号：** {analysis.get("vol_signal","—")}
- **资金流向：** {analysis.get("mfi_signal","—")}
- **OBV趋势：** {"多头" if analysis.get("obv_bullish") else "空头"}

---

### 十三、多指标共振
- **共振状态：** {analysis.get("resonance","—")}
- **买入信号：** {", ".join(analysis.get("buy_signals",[])) if analysis.get("buy_signals") else "无"}
- **卖出信号：** {", ".join(analysis.get("sell_signals",[])) if analysis.get("sell_signals") else "无"}

---

### 十四、操作建议
> **{analysis.get("action","—")}**：{analysis.get("advice","—")}

---
*本报告基于规则引擎生成，仅供参考，不构成投资建议。*
"""
    st.markdown(report_md)

    # ── 增强报告：指标详细解释 ──
    indicator_interps = analysis.get("indicator_interpretations", [])
    if indicator_interps:
        st.markdown("---")
        st.markdown("### 附录A：指标详细利多利空分析")

        for interp in indicator_interps:
            bias = interp.get("bias", "中性")
            bias_color = "#0ECB81" if bias == "利多" else ("#F6465D" if bias == "利空" else "#888")
            prob = interp.get("probability", {})
            st.markdown(
                f"**{interp.get('name', '—')}** "
                f"<span style='color:{bias_color}'>[{bias} {interp.get('bias_score', 0):+d}]</span> "
                f"— 多{prob.get('bullish_pct', 0):.0f}% / 空{prob.get('bearish_pct', 0):.0f}%",
                unsafe_allow_html=True
            )
            st.markdown(f"> {interp.get('interpretation', '—')}")
            if interp.get("action_hint"):
                st.caption(f"💡 {interp['action_hint']}")

    # ── 增强报告：综合概率 ──
    comp_prob = analysis.get("comprehensive_probability", {})
    if comp_prob:
        st.markdown("---")
        st.markdown("### 附录B：综合利多利空概率")
        st.markdown(
            f"- **综合判断:** {comp_prob.get('overall_bias', '中性')} (评分: {comp_prob.get('overall_score', 0):+.0f})\n"
            f"- **看多概率:** {comp_prob.get('bullish_pct', 0):.0f}%\n"
            f"- **看空概率:** {comp_prob.get('bearish_pct', 0):.0f}%\n"
            f"- **中性概率:** {comp_prob.get('neutral_pct', 0):.0f}%"
        )
        if comp_prob.get("interpretation"):
            st.markdown(f"> {comp_prob['interpretation']}")

    # ── 增强报告：多周期Elliott波浪分析 ──
    mtf_ew = analysis.get("elliott_wave_multi_tf", {})
    if mtf_ew and mtf_ew.get("timeframes"):
        st.markdown("---")
        st.markdown("### 附录C-1：🌊 多周期Elliott波浪分析")

        # 各周期浪位汇总表
        ew_rows = []
        for tf_name in ["周线", "日线", "4小时", "1小时"]:
            tf_data = mtf_ew["timeframes"].get(tf_name)
            if tf_data:
                cw = tf_data.get("current_wave", {})
                wave_type = cw.get("type", "unknown")
                wave_num = cw.get("wave_number", 0)
                direction = cw.get("direction", "neutral")
                confidence = cw.get("confidence", 0)
                phase = cw.get("phase", "未识别")

                if wave_type == "impulse":
                    wave_str = f"推动W{wave_num}"
                elif wave_type == "corrective":
                    letter = {1: "A", 2: "B", 3: "C"}.get(wave_num, "?")
                    wave_str = f"修正{letter}"
                else:
                    wave_str = "未识别"

                dir_str = {"bullish": "看多", "bearish": "看空"}.get(direction, "中性")
                dir_color = "#0ECB81" if direction == "bullish" else ("#F6465D" if direction == "bearish" else "#888")

                targets = tf_data.get("projected_targets", [])
                target_str = f"{targets[0]['price']:.2f}" if targets else "—"

                ew_rows.append({
                    "周期": tf_name,
                    "当前浪": wave_str,
                    "方向": dir_str,
                    "置信度": f"{confidence}%",
                    "阶段": phase,
                    "预测目标": target_str,
                })

        if ew_rows:
            ew_table = pd.DataFrame(ew_rows)
            st.dataframe(ew_table, hide_index=True, width="stretch")

        # 综合研判
        synthesis = mtf_ew.get("synthesis", {})
        if synthesis:
            alignment = mtf_ew.get("alignment", "数据不足")
            bias = synthesis.get("bias", "中性")
            conf = synthesis.get("confidence", 0)
            action = synthesis.get("suggested_action", "观望")
            timing = synthesis.get("entry_timing", "")
            reasoning = synthesis.get("reasoning", [])

            action_color = {"强买": "#0ECB81", "弱买": "#26A69A", "观望": "#888",
                            "弱卖": "#EF9A9A", "强卖": "#F6465D"}.get(action, "#888")

            st.markdown(
                f'<div style="background:#161b22;padding:12px 16px;border-radius:8px;'
                f'border-left:4px solid {action_color};margin:8px 0">'
                f'<span style="color:#c9d1d9;font-weight:bold">综合研判:</span> '
                f'<span style="color:{action_color};font-size:16px;font-weight:bold">{action}</span> '
                f'<span style="color:#8b949e">({alignment}, 置信度{conf}%)</span><br>'
                f'<span style="color:#58a6ff;font-size:13px">{timing}</span><br>'
                f'<span style="color:#8b949e;font-size:12px">{"；".join(reasoning)}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )

    # ── 增强报告：分周期支撑压力位 ──
    multi_tf_sr = analysis.get("multi_tf_sr", {})
    if multi_tf_sr:
        st.markdown("---")
        st.markdown("### 附录C：分周期支撑压力位")
        sr_rows = []
        for tf_name, sr in multi_tf_sr.items():
            if isinstance(sr, dict) and not sr.get("error"):
                ps = sr.get("primary_support")
                pr = sr.get("primary_resistance")
                ps_price = ps.get("price", 0) if isinstance(ps, dict) else (ps if isinstance(ps, (int, float)) else 0)
                pr_price = pr.get("price", 0) if isinstance(pr, dict) else (pr if isinstance(pr, (int, float)) else 0)
                ps_method = ps.get("method", "—") if isinstance(ps, dict) else "—"
                pr_method = pr.get("method", "—") if isinstance(pr, dict) else "—"
                ps_strength = ps.get("strength", "—") if isinstance(ps, dict) else "—"
                sr_rows.append({
                    "周期": tf_name,
                    "主要支撑": f"{ps_price:.2f}" if ps_price else "—",
                    "来源": ps_method,
                    "主要压力": f"{pr_price:.2f}" if pr_price else "—",
                    "来源 ": pr_method,
                    "强度": ps_strength,
                })
        if sr_rows:
            st.dataframe(pd.DataFrame(sr_rows), width="stretch", hide_index=True)

    # ── 附录D：K线形态 + 背离信号 + 图表形态 ──
    candle_pats = analysis.get("candlestick_patterns", [])
    if candle_pats:
        st.markdown("### 附录D：K线形态识别")
        cp_rows = []
        for cp in candle_pats:
            cp_rows.append({
                "形态": cp.get("name_cn", ""),
                "类型": cp.get("type", ""),
                "方向": cp.get("direction", ""),
                "可靠度": cp.get("reliability", ""),
                "评分": cp.get("bias_score", 0),
                "说明": cp.get("description", "")[:40],
            })
        st.dataframe(pd.DataFrame(cp_rows), width="stretch", hide_index=True)

    div_summary = analysis.get("divergence_summary", {})
    active_divs = div_summary.get("active_divergences", [])
    if active_divs:
        st.markdown("### 附录E：背离信号")
        for d in active_divs:
            d_color = "#F6465D" if "顶" in d.get("type", "") else "#0ECB81"
            st.markdown(
                f"<div style='color:{d_color};font-weight:bold'>⚠️ {d.get('indicator', '')} {d.get('type', '')}（{d.get('reliability', '')}）</div>"
                f"<div style='color:#aaa;font-size:12px'>{d.get('description', '')}</div>",
                unsafe_allow_html=True
            )

    chart_pats = analysis.get("chart_patterns", [])
    if chart_pats:
        st.markdown("### 附录F：图表形态")
        for cp in chart_pats:
            st.markdown(
                f"**{cp.get('name_cn', '')}** ({cp.get('direction', '')}, 信心{cp.get('confidence', 0)}%) — "
                f"目标价 {cp.get('target_price', 0):.2f}, 止损 {cp.get('stop_price', 0):.2f}"
            )
            st.markdown(f"<div style='color:#aaa;font-size:12px'>{cp.get('description', '')}</div>", unsafe_allow_html=True)

    # ── 附录G：风险评估 ──
    risk_m = analysis.get("risk_metrics", {})
    if risk_m and risk_m.get("available"):
        st.markdown("### 附录G：风险量化评估")
        risk_rows = {
            "VaR(95%)": f"{risk_m.get('var_95', 0):.2f}%",
            "VaR(99%)": f"{risk_m.get('var_99', 0):.2f}%",
            "CVaR(95%)": f"{risk_m.get('cvar_95', 0):.2f}%",
            "Beta": f"{risk_m.get('beta', '—')}",
            "Alpha": f"{risk_m.get('alpha', '—')}",
            "Sharpe": f"{risk_m.get('sharpe_ratio', 0):.2f}",
            "Sortino": f"{risk_m.get('sortino_ratio', 0):.2f}",
            "Calmar": f"{risk_m.get('calmar_ratio', 0):.2f}",
            "最大回撤": f"{risk_m.get('max_drawdown', {}).get('value', 0):.1f}%",
            "风险等级": risk_m.get("risk_level", "—"),
        }
        r_df = pd.DataFrame(list(risk_rows.items()), columns=["指标", "数值"])
        st.dataframe(r_df, width="stretch", hide_index=True)

        kelly = risk_m.get("kelly_criterion", {})
        if kelly.get("interpretation"):
            st.markdown(f"**Kelly仓位建议：** {kelly['interpretation']}")

        vol_cone = risk_m.get("volatility_cone", {})
        if vol_cone.get("interpretation"):
            st.markdown(f"**波动率评估：** {vol_cone['interpretation']}")

# ── Tab7：策略回测 ────────────────────────────────────────────────────────
with tab_backtest:
    st.markdown('<p style="color:#00B4D8;font-size:14px;font-weight:bold">🔄 策略回测</p>',
                unsafe_allow_html=True)

    col1, col2, col3 = st.columns([2, 2, 1])

    with col1:
        strategy_type = st.selectbox(
            "选择策略",
            ["ema", "bollinger", "kdj", "macd", "rsi", "divergence", "ichimoku", "td_sequential"],
            format_func=lambda x: {
                "ema": "EMA隧道趋势", "bollinger": "布林带均值回归", "kdj": "KDJ超买超卖",
                "macd": "MACD金叉死叉", "rsi": "RSI反转", "divergence": "背离交易",
                "ichimoku": "一目均衡表趋势", "td_sequential": "TD序列反转",
            }[x]
        )

    with col2:
        initial_capital = st.number_input("初始资金", value=100000, step=10000)

    with col3:
        run_backtest_btn = st.button("运行回测")

    if run_backtest_btn:
        with st.spinner("正在运行回测..."):
            try:
                result = run_backtest(df, strategy_type, ticker, period, initial_capital)

                # 显示回测结果
                st.markdown("#### 回测结果")

                result_cols = st.columns(4)
                with result_cols[0]:
                    render_trend_item("总收益率", f"{result.total_return*100:.2f}%",
                                     "#0ECB81" if result.total_return > 0 else "#F6465D")
                with result_cols[1]:
                    render_trend_item("交易次数", str(result.total_trades), "#E0E0E0")
                with result_cols[2]:
                    render_trend_item("胜率", f"{result.win_rate*100:.1f}%", "#FFD700")
                with result_cols[3]:
                    render_trend_item("最大回撤", f"{result.max_drawdown_pct*100:.2f}%", "#FFA500")

                result_cols2 = st.columns(4)
                with result_cols2[0]:
                    render_trend_item("夏普比率", f"{result.sharpe_ratio:.2f}", "#E0E0E0")
                with result_cols2[1]:
                    render_trend_item("盈亏比", f"{result.profit_factor:.2f}", "#E0E0E0")
                with result_cols2[2]:
                    render_trend_item("平均盈利", f"{result.avg_profit:.2f}", "#0ECB81")
                with result_cols2[3]:
                    render_trend_item("平均亏损", f"{result.avg_loss:.2f}", "#F6465D")

                # 权益曲线
                if not result.equity_curve.empty:
                    import plotly.graph_objects as go
                    equity_fig = go.Figure()
                    equity_fig.add_trace(go.Scatter(
                        x=result.equity_curve.index,
                        y=result.equity_curve['equity'],
                        mode='lines',
                        name='权益曲线',
                        line=dict(color='#00B4D8', width=1.5)
                    ))
                    equity_fig.update_layout(
                        title="权益曲线",
                        paper_bgcolor='#16213e',
                        plot_bgcolor='#1a1a2e',
                        font=dict(color='#E0E0E0'),
                        height=300,
                        margin=dict(l=40, r=40, t=40, b=40),
                    )
                    st.plotly_chart(equity_fig, width='stretch')

                # 交易记录
                if result.trades:
                    st.markdown("#### 最近交易记录")
                    trades_data = []
                    for t in result.trades[-10:]:  # 只显示最近10笔
                        trades_data.append({
                            "入场日期": t.entry_date.strftime("%Y-%m-%d"),
                            "入场价": f"{t.entry_price:.2f}",
                            "出场日期": t.exit_date.strftime("%Y-%m-%d") if t.exit_date else "—",
                            "出场价": f"{t.exit_price:.2f}",
                            "盈亏": f"{t.pnl:.2f}",
                            "收益率": f"{t.pnl_pct*100:.2f}%",
                            "原因": t.exit_reason,
                        })
                    st.dataframe(pd.DataFrame(trades_data))

            except Exception as e:
                st.error(f"回测运行失败: {e}")

    # 策略对比
    st.markdown("#### 策略对比")
    if st.button("对比所有策略"):
        with st.spinner("正在对比策略..."):
            try:
                comparison = compare_strategies(df, ticker, period)
                st.dataframe(comparison, hide_index=True)
            except Exception as e:
                st.error(f"策略对比失败: {e}")

    # ── Walk-Forward 滚动优化 ──────────────────────────────────────────────
    st.markdown("---")
    st.markdown("#### Walk-Forward 滚动优化")
    st.caption("在滚动窗口上优化策略参数，并在样本外 (OOS) 验证，检验参数稳定性和过拟合风险")

    wf_cols = st.columns([2, 1])
    with wf_cols[0]:
        wf_strategy = st.selectbox(
            "WF策略", ["ema", "bollinger", "kdj", "macd", "rsi"],
            format_func=lambda x: {"ema": "EMA隧道", "bollinger": "布林带", "kdj": "KDJ",
                                   "macd": "MACD", "rsi": "RSI"}[x],
            key="wf_strategy_select"
        )
    with wf_cols[1]:
        wf_run_btn = st.button("运行 Walk-Forward", key="wf_run_btn")

    if wf_run_btn:
        from modules.walk_forward import quick_walk_forward
        wf_progress = st.progress(0, text="准备中...")

        def _wf_cb(pct, msg):
            wf_progress.progress(min(int(pct), 100), text=msg)

        wf_report = quick_walk_forward(df, strategy_type=wf_strategy, ticker=ticker, progress_callback=_wf_cb)
        wf_progress.empty()

        if wf_report.get("success"):
            # 汇总指标卡片
            wf_mc = st.columns(5)
            wf_metrics = [
                ("OOS综合收益", f"{wf_report['oos_total_return']*100:.2f}%",
                 "#26A69A" if wf_report['oos_total_return'] > 0 else "#EF5350"),
                ("OOS平均Sharpe", f"{wf_report['oos_avg_sharpe']:.2f}",
                 "#26A69A" if wf_report['oos_avg_sharpe'] > 0.5 else "#FF9800"),
                ("OOS平均胜率", f"{wf_report['oos_avg_win_rate']*100:.1f}%", "#E0E0E0"),
                ("OOS最大回撤", f"{wf_report['oos_worst_dd']*100:.2f}%", "#EF5350"),
                ("窗口数", str(wf_report['n_windows']), "#E0E0E0"),
            ]
            for i_wf, (wl, wv, wc) in enumerate(wf_metrics):
                with wf_mc[i_wf]:
                    st.markdown(f'<div style="text-align:center;padding:6px;border:1px solid #444;border-radius:6px">'
                                f'<div style="color:#888;font-size:10px">{wl}</div>'
                                f'<div style="color:{wc};font-size:14px;font-weight:bold">{wv}</div></div>',
                                unsafe_allow_html=True)

            # 窗口明细表
            with st.expander("📊 各窗口明细", expanded=False):
                wf_windows = wf_report.get("windows", [])
                if wf_windows:
                    wf_df = pd.DataFrame(wf_windows)
                    st.dataframe(wf_df, hide_index=True)

            # 参数稳定性
            with st.expander("🔒 参数稳定性分析", expanded=True):
                stab = wf_report.get("param_stability", {})
                if stab:
                    for pk, pv in stab.items():
                        is_stable = pv.get("stable", False)
                        badge_color = "#26A69A" if is_stable else "#EF5350"
                        badge_text = "稳定" if is_stable else "不稳定"
                        cv_pct = pv.get("cv", 0) * 100
                        st.markdown(
                            f'<div style="display:flex;align-items:center;gap:8px;margin:4px 0">'
                            f'<span style="color:#E0E0E0;font-weight:bold;width:120px">{pk}</span>'
                            f'<span style="background:{badge_color};color:white;padding:2px 8px;border-radius:10px;font-size:11px">{badge_text}</span>'
                            f'<span style="color:#AAA;font-size:12px">均值={pv.get("mean", 0):.3f} | 变异系数={cv_pct:.1f}% | 众数={pv.get("mode", "—")}</span>'
                            f'</div>', unsafe_allow_html=True
                        )
                        # 参数漂移折线
                        vals = pv.get("values", [])
                        if len(vals) > 1:
                            import plotly.graph_objects as go
                            fig_drift = go.Figure()
                            fig_drift.add_trace(go.Scatter(
                                x=list(range(1, len(vals)+1)), y=vals,
                                mode="lines+markers", name=pk,
                                line=dict(color="#00B4D8", width=2),
                                marker=dict(size=6),
                            ))
                            fig_drift.update_layout(
                                height=150, margin=dict(l=30, r=10, t=10, b=30),
                                paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='#1a1a2e',
                                font=dict(color='#888', size=10),
                                xaxis_title="窗口", yaxis_title=pk,
                            )
                            st.plotly_chart(fig_drift, use_container_width=True)
                else:
                    st.info("无参数稳定性数据")

            # OOS 拼接权益曲线
            wf_equity = wf_report.get("combined_equity")
            if wf_equity is not None and not wf_equity.empty:
                with st.expander("📈 OOS 拼接权益曲线", expanded=False):
                    import plotly.graph_objects as go
                    fig_oos = go.Figure()
                    fig_oos.add_trace(go.Scatter(
                        x=wf_equity.index, y=wf_equity["equity"],
                        mode="lines", name="OOS权益",
                        line=dict(color="#26A69A", width=1.5),
                    ))
                    fig_oos.update_layout(
                        title="Walk-Forward OOS 拼接权益曲线",
                        height=300, paper_bgcolor='#16213e', plot_bgcolor='#1a1a2e',
                        font=dict(color='#E0E0E0'),
                        margin=dict(l=40, r=40, t=40, b=40),
                    )
                    st.plotly_chart(fig_oos, use_container_width=True)

        else:
            st.warning(f"Walk-Forward 失败: {wf_report.get('error', '未知错误')}")

    # ── 参数敏感度分析 ──────────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("#### 参数敏感度热力图")
    st.caption("对关键参数做网格搜索，找到 Sharpe 的\"稳定高原\"区域，避免脆弱参数")

    sens_cols = st.columns([2, 2, 1])
    with sens_cols[0]:
        sens_strategy = st.selectbox(
            "敏感度策略", ["ema", "bollinger", "kdj", "macd", "rsi"],
            format_func=lambda x: {"ema": "EMA隧道", "bollinger": "布林带", "kdj": "KDJ",
                                   "macd": "MACD", "rsi": "RSI"}[x],
            key="sens_strategy_select"
        )
    with sens_cols[1]:
        from modules.param_sensitivity import get_available_pairs
        avail_pairs = get_available_pairs(sens_strategy)
        pair_labels = [p["label"] for p in avail_pairs] if avail_pairs else ["无可用参数对"]
        sens_pair_idx = st.selectbox("参数对", range(len(pair_labels)),
                                     format_func=lambda i: pair_labels[i],
                                     key="sens_pair_select")
    with sens_cols[2]:
        sens_run_btn = st.button("运行敏感度分析", key="sens_run_btn")

    if sens_run_btn and avail_pairs:
        from modules.param_sensitivity import run_sensitivity_analysis
        sens_progress = st.progress(0, text="准备中...")

        def _sens_cb(pct, msg):
            sens_progress.progress(min(int(pct), 100), text=msg)

        sens_result = run_sensitivity_analysis(
            df, strategy_type=sens_strategy, ticker=ticker,
            pair_index=sens_pair_idx, progress_callback=_sens_cb,
        )
        sens_progress.empty()

        if sens_result.get("success"):
            import plotly.graph_objects as go

            # Sharpe 热力图
            fig_hm = go.Figure(data=go.Heatmap(
                z=sens_result["sharpe_matrix"],
                x=[str(v) for v in sens_result["x_values"]],
                y=[str(v) for v in sens_result["y_values"]],
                colorscale="RdYlGn",
                text=[[f"{v:.2f}" for v in row] for row in sens_result["sharpe_matrix"]],
                texttemplate="%{text}",
                textfont={"size": 10},
                hovertemplate=f"{sens_result['param_x']}=%{{x}}<br>{sens_result['param_y']}=%{{y}}<br>Sharpe=%{{z:.3f}}<extra></extra>",
            ))
            fig_hm.update_layout(
                title=f"Sharpe Ratio 热力图 ({sens_result['param_x']} × {sens_result['param_y']})",
                xaxis_title=sens_result["param_x"],
                yaxis_title=sens_result["param_y"],
                height=400, paper_bgcolor='#16213e', plot_bgcolor='#1a1a2e',
                font=dict(color='#E0E0E0'),
            )
            st.plotly_chart(fig_hm, use_container_width=True)

            # 摘要
            sm_cols = st.columns(3)
            with sm_cols[0]:
                bp = sens_result["best_params"]
                bp_str = ", ".join([f"{k}={v}" for k, v in bp.items()])
                st.markdown(f'<div style="text-align:center;padding:6px;border:1px solid #444;border-radius:6px">'
                            f'<div style="color:#888;font-size:10px">最优参数</div>'
                            f'<div style="color:#26A69A;font-size:13px;font-weight:bold">{bp_str}</div></div>',
                            unsafe_allow_html=True)
            with sm_cols[1]:
                st.markdown(f'<div style="text-align:center;padding:6px;border:1px solid #444;border-radius:6px">'
                            f'<div style="color:#888;font-size:10px">最优Sharpe</div>'
                            f'<div style="color:#26A69A;font-size:14px;font-weight:bold">{sens_result["best_sharpe"]:.3f}</div></div>',
                            unsafe_allow_html=True)
            with sm_cols[2]:
                ps = sens_result["plateau_score"]
                ps_color = "#26A69A" if ps > 30 else ("#FF9800" if ps > 15 else "#EF5350")
                st.markdown(f'<div style="text-align:center;padding:6px;border:1px solid #444;border-radius:6px">'
                            f'<div style="color:#888;font-size:10px">高原稳定度</div>'
                            f'<div style="color:{ps_color};font-size:14px;font-weight:bold">{ps:.1f}%</div></div>',
                            unsafe_allow_html=True)

            st.caption("高原稳定度 = 达到最优Sharpe 90%以上的参数组合占比，越高说明参数越鲁棒")

        else:
            st.warning(f"敏感度分析失败: {sens_result.get('error', '未知错误')}")

    # ── 遗传算法(GA)参数优化 ──────────────────────────────────────────────
    st.markdown("#### 遗传算法(GA)参数优化")
    st.caption("用遗传算法搜索策略最优参数，比网格搜索更高效，尤其适合高维参数空间（如 MACD fast/slow/signal）。")

    ga_c1, ga_c2 = st.columns(2)
    with ga_c1:
        ga_strategy = st.selectbox(
            "GA 策略", ["macd", "ema", "bollinger", "kdj", "rsi"],
            key="ga_strategy_sel"
        )
    with ga_c2:
        ga_objective = st.selectbox(
            "优化目标", ["sharpe", "return", "calmar"],
            format_func=lambda x: {"sharpe": "Sharpe Ratio", "return": "总收益率", "calmar": "Calmar Ratio"}[x],
            key="ga_obj_sel"
        )

    ga_c3, ga_c4, ga_c5 = st.columns(3)
    with ga_c3:
        ga_pop = st.slider("种群大小", 10, 50, 30, key="ga_pop")
    with ga_c4:
        ga_gens = st.slider("进化代数", 5, 40, 20, key="ga_gens")
    with ga_c5:
        ga_mut = st.slider("变异率", 0.05, 0.50, 0.20, step=0.05, key="ga_mut")

    ga_run_btn = st.button("运行 GA 优化", key="ga_run_btn")

    if ga_run_btn:
        from modules.ga_optimizer import run_ga_optimization
        ga_progress = st.progress(0, text="GA 初始化...")

        def _ga_cb(pct, msg):
            ga_progress.progress(int(pct), text=msg)

        with st.spinner("遗传算法优化中..."):
            ga_result = run_ga_optimization(
                df, strategy_type=ga_strategy, ticker=ticker,
                population_size=ga_pop, generations=ga_gens,
                mutation_rate=ga_mut, objective=ga_objective,
                progress_callback=_ga_cb,
            )

        ga_progress.empty()

        if ga_result.best_fitness > -999:
            st.success(f"GA 优化完成！共 {ga_result.total_evaluations} 次评估，在第 {ga_result.convergence_gen+1} 代收敛")

            # 最优参数展示
            ga_m1, ga_m2, ga_m3 = st.columns(3)
            with ga_m1:
                st.metric("最优适应度", f"{ga_result.best_fitness:.3f}")
            with ga_m2:
                st.metric("交易次数", ga_result.best_trades)
            with ga_m3:
                st.metric("总评估次数", ga_result.total_evaluations)

            st.markdown("**最优参数:**")
            param_cols = st.columns(min(len(ga_result.best_params), 5))
            for i, (k, v) in enumerate(ga_result.best_params.items()):
                with param_cols[i % len(param_cols)]:
                    display_v = f"{v}" if isinstance(v, int) else f"{v:.4f}"
                    st.metric(k, display_v)

            # 进化曲线
            if ga_result.fitness_history:
                import plotly.graph_objects as go
                fig_ga = go.Figure()
                fig_ga.add_trace(go.Scatter(
                    y=ga_result.fitness_history,
                    mode="lines+markers", name="最优适应度",
                    line=dict(color="#00B4D8", width=2),
                ))
                if ga_result.avg_fitness_history:
                    fig_ga.add_trace(go.Scatter(
                        y=ga_result.avg_fitness_history,
                        mode="lines", name="平均适应度",
                        line=dict(color="#FF6B6B", width=1, dash="dash"),
                    ))
                fig_ga.update_layout(
                    title="GA 进化曲线",
                    xaxis_title="代数", yaxis_title="适应度",
                    template="plotly_dark", height=350,
                )
                st.plotly_chart(fig_ga, use_container_width=True)
        else:
            st.warning("GA 未找到有效参数组合，可能数据不足或策略不适配。")

# ── Tab8：预警系统 ────────────────────────────────────────────────────────
with tab_alerts:
    st.markdown('<p style="color:#00B4D8;font-size:14px;font-weight:bold">🔔 预警系统</p>',
                unsafe_allow_html=True)

    # 添加新预警
    with st.expander("➕ 添加新预警", expanded=False):
        alert_type = st.selectbox(
            "预警类型",
            ["price_above", "price_below", "ma_cross", "bb_break", "kdj", "rsi", "cci", "wr", "dmi_cross", "volume_spike"],
            format_func=lambda x: {
                "price_above": "价格上破",
                "price_below": "价格下破",
                "ma_cross": "均线交叉",
                "bb_break": "布林带突破",
                "kdj": "KDJ超买超卖",
                "rsi": "RSI超买超卖",
                "cci": "CCI超买超卖",
                "wr": "WR超买超卖",
                "dmi_cross": "DMI交叉",
                "volume_spike": "成交量异常",
            }[x]
        )

        if alert_type == "price_above":
            alert_price = st.number_input("目标价格（上破）", value=float(current_price*1.05), step=0.01)
            if st.button("添加价格预警"):
                rule = add_price_alert(ticker, alert_price, "above")
                st.success(f"预警已添加: {rule.name} (ID: {rule.id})")

        elif alert_type == "price_below":
            alert_price = st.number_input("目标价格（下破）", value=float(current_price*0.95), step=0.01)
            if st.button("添加价格预警"):
                rule = add_price_alert(ticker, alert_price, "below")
                st.success(f"预警已添加: {rule.name} (ID: {rule.id})")

        elif alert_type == "ma_cross":
            cross_type = st.selectbox("交叉类型", ["golden", "dead"], format_func=lambda x: "金叉" if x=="golden" else "死叉")
            if st.button("添加均线交叉预警"):
                rule = add_ma_cross_alert(ticker, cross_type=cross_type)
                st.success(f"预警已添加: {rule.name} (ID: {rule.id})")

        elif alert_type == "bb_break":
            bb_dir = st.selectbox("突破方向", ["upper", "lower"], format_func=lambda x: "上轨" if x=="upper" else "下轨")
            if st.button("添加布林带突破预警"):
                rule = add_bb_break_alert(ticker, bb_dir)
                st.success(f"预警已添加: {rule.name} (ID: {rule.id})")

        elif alert_type == "kdj":
            kdj_cond = st.selectbox("条件", ["oversold", "overbought"], format_func=lambda x: "超卖" if x=="oversold" else "超买")
            if st.button("添加KDJ预警"):
                rule = add_kdj_alert(ticker, kdj_cond)
                st.success(f"预警已添加: {rule.name} (ID: {rule.id})")

        elif alert_type == "rsi":
            rsi_cond = st.selectbox("条件", ["oversold", "overbought"], format_func=lambda x: "超卖" if x=="oversold" else "超买")
            if st.button("添加RSI预警"):
                rule = add_rsi_alert(ticker, rsi_cond)
                st.success(f"预警已添加: {rule.name} (ID: {rule.id})")

        elif alert_type == "cci":
            cci_cond = st.selectbox("条件", ["oversold", "overbought"], format_func=lambda x: "超卖(<-100)" if x=="oversold" else "超买(>100)")
            if st.button("添加CCI预警"):
                rule = add_cci_alert(ticker, cci_cond)
                st.success(f"预警已添加: {rule.name} (ID: {rule.id})")

        elif alert_type == "wr":
            wr_cond = st.selectbox("条件", ["oversold", "overbought"], format_func=lambda x: "超卖(<-80)" if x=="oversold" else "超买(>-20)")
            if st.button("添加WR预警"):
                rule = add_wr_alert(ticker, wr_cond)
                st.success(f"预警已添加: {rule.name} (ID: {rule.id})")

        elif alert_type == "dmi_cross":
            dmi_cross = st.selectbox("交叉类型", ["golden", "dead"], format_func=lambda x: "金叉(DI+上穿DI-)" if x=="golden" else "死叉(DI-上穿DI+)")
            if st.button("添加DMI交叉预警"):
                rule = add_dmi_cross_alert(ticker, dmi_cross)
                st.success(f"预警已添加: {rule.name} (ID: {rule.id})")

        elif alert_type == "volume_spike":
            multiplier = st.number_input("放量倍数", value=2.0, min_value=1.5, max_value=5.0, step=0.5)
            if st.button("添加成交量预警"):
                rule = add_volume_spike_alert(ticker, multiplier)
                st.success(f"预警已添加: {rule.name} (ID: {rule.id})")

    # 当前预警列表
    st.markdown("#### 当前预警")
    active_alerts = get_active_alerts(ticker)
    if active_alerts:
        for alert in active_alerts:
            col1, col2, col3 = st.columns([3, 1, 1])
            with col1:
                st.text(f"{alert.name} ({alert.alert_type.value})")
            with col2:
                if st.button("删除", key=f"del_{alert.id}"):
                    remove_alert(alert.id)
                    st.rerun()
    else:
        st.info("暂无活跃预警")

    # 预警统计
    stats = get_alert_stats()
    st.markdown("#### 预警统计")
    stat_cols = st.columns(4)
    with stat_cols[0]:
        render_metric_card("总规则数", str(stats['total_rules']))
    with stat_cols[1]:
        render_metric_card("活跃规则", str(stats['active_rules']))
    with stat_cols[2]:
        render_metric_card("已触发", str(stats['triggered_rules']))
    with stat_cols[3]:
        render_metric_card("今日触发", str(stats['recent_triggers']))

# ── Tab9：龙空龙战术面板 ──────────────────────────────────────────────────
with tab_dragon:
    st.markdown('<p style="color:#F6465D;font-size:14px;font-weight:bold">🐉 龙空龙战术面板</p>',
                unsafe_allow_html=True)
    st.markdown('<p style="color:#888;font-size:12px">扫描A股全市场，筛选过去一年涨停次数超过指定阈值的龙头股，按板块分组展示</p>',
                unsafe_allow_html=True)

    from modules.dragon_screener import (
        scan_all_stocks_for_dragons, group_by_sector,
        build_sector_heatmap_data, DragonScreenerCache,
    )

    # 控制面板
    dragon_ctrl = st.columns([1, 1, 2])
    with dragon_ctrl[0]:
        min_zt = st.number_input("最低涨停次数", min_value=3, max_value=30, value=10, key="dragon_min_zt")
    with dragon_ctrl[1]:
        scan_days = st.selectbox("扫描周期", [125, 250, 500], index=1,
                                  format_func=lambda x: f"近{x}个交易日", key="dragon_days")
    with dragon_ctrl[2]:
        scan_btn = st.button("🔍 一键筛选龙头股", type="primary", width="stretch")

    # 缓存检查
    dragon_cache = DragonScreenerCache()
    cached_df = dragon_cache.get_cached_result(min_zt=min_zt, days=scan_days)

    dragon_df = None
    if scan_btn:
        progress_bar = st.progress(0, text="初始化扫描...")
        def _update_progress(pct, text):
            progress_bar.progress(pct, text=text)
        dragon_df = scan_all_stocks_for_dragons(
            min_limit_up=min_zt, days=scan_days, progress_callback=_update_progress
        )
        if dragon_df is not None and not dragon_df.empty:
            dragon_cache.save_result(dragon_df, min_zt=min_zt, days=scan_days)
        progress_bar.empty()
    elif cached_df is not None and not cached_df.empty:
        dragon_df = cached_df
        st.info("📦 使用24小时内缓存数据，点击「一键筛选」刷新")

    if dragon_df is not None and not dragon_df.empty:
        # 汇总指标
        dm = st.columns(4)
        with dm[0]:
            render_trend_item("龙头股数量", f"{len(dragon_df)}", "#F6465D")
        with dm[1]:
            top = dragon_df.iloc[0]
            render_trend_item("最强龙头", f"{top.get('名称','?')}({int(top.get('涨停次数',0))}次)", "#FFD700")
        with dm[2]:
            sectors = dragon_df["板块"].nunique()
            render_trend_item("涉及板块", f"{sectors}个", "#00B4D8")
        with dm[3]:
            avg = dragon_df["涨停次数"].mean()
            render_trend_item("平均涨停", f"{avg:.1f}次", "#8b949e")

        # 板块热力图
        st.markdown("##### 🗺️ 板块龙头分布")
        grouped = group_by_sector(dragon_df)
        heatmap_data = build_sector_heatmap_data(grouped)
        if not heatmap_data.empty:
            import plotly.express as px
            fig_tree = px.treemap(
                heatmap_data, path=["板块"], values="龙头数量",
                color="平均涨停", color_continuous_scale="RdYlGn",
                hover_data=["最强龙头"],
            )
            fig_tree.update_layout(
                paper_bgcolor="#0d1117", plot_bgcolor="#161b22",
                font=dict(color="#c9d1d9"),
                margin=dict(t=30, l=10, r=10, b=10),
                height=350,
            )
            st.plotly_chart(fig_tree, width="stretch")

        # 筛选表格
        st.markdown("##### 📊 龙头股筛选结果")
        sector_list = sorted(dragon_df["板块"].unique().tolist())
        sector_filter = st.multiselect("按板块筛选", sector_list, key="dragon_sector_filter")
        display_df = dragon_df if not sector_filter else dragon_df[dragon_df["板块"].isin(sector_filter)]
        display_df = display_df.sort_values("涨停次数", ascending=False)

        # 格式化流通市值
        show_df = display_df[["代码", "名称", "板块", "涨停次数", "最大连板", "最近涨停日", "最新价", "流通市值"]].copy()
        show_df["流通市值"] = show_df["流通市值"].apply(
            lambda x: f"{x/1e8:.1f}亿" if x >= 1e8 else (f"{x/1e4:.0f}万" if x >= 1e4 else str(x))
        )
        st.dataframe(show_df, hide_index=True, width="stretch", height=400)

        # 选择分析
        st.markdown("##### 🔍 选择股票进行分析")
        dragon_options = display_df["代码"].tolist()
        if dragon_options:
            selected_dragon = st.selectbox(
                "选择龙头股",
                dragon_options,
                format_func=lambda x: f"{x} - {display_df[display_df['代码']==x].iloc[0]['名称']} ({int(display_df[display_df['代码']==x].iloc[0]['涨停次数'])}次涨停)",
                key="dragon_select",
            )
            if st.button("📈 分析该股票", key="dragon_analyze"):
                # 转换为带后缀的格式
                code = str(selected_dragon)
                if code.startswith("6") or code.startswith("5"):
                    full_code = f"{code}.SS"
                else:
                    full_code = f"{code}.SZ"
                st.session_state["manual_ticker"] = full_code
                st.rerun()
    elif scan_btn:
        st.warning(f"未找到过去{scan_days}个交易日内涨停≥{min_zt}次的股票")
    else:
        st.info("点击「一键筛选」开始扫描全市场涨停龙头股")

def _classify_from_spot(row) -> dict:
    """基于spot数据的简化位置分类"""
    reasons = []
    chg = float(row.get("涨跌幅", 0) or 0)
    vol_ratio = float(row.get("量比", 1) or 1)
    turnover = float(row.get("换手率", 0) or 0)
    amplitude = float(row.get("振幅", 0) or 0)

    # 低吸点：跌幅较大 + 缩量
    if chg < -2 and vol_ratio < 0.8:
        reasons.append(f"下跌{chg:.1f}%缩量")
        if amplitude > 3:
            reasons.append(f"振幅{amplitude:.1f}%")
        return {"position_type": "低吸点", "confidence": min(80, 40 + len(reasons) * 20),
                "reasons": reasons, "action": "逢低分批建仓"}

    # 突破点：涨幅大 + 放量
    if chg > 3 and vol_ratio > 1.5:
        reasons.append(f"上涨{chg:.1f}%放量")
        if turnover > 5:
            reasons.append(f"换手{turnover:.1f}%")
        return {"position_type": "突破点", "confidence": min(80, 40 + len(reasons) * 20),
                "reasons": reasons, "action": "突破确认后追多"}

    # 破位点：大跌 + 放量
    if chg < -5 and vol_ratio > 1.2:
        reasons.append(f"大跌{chg:.1f}%放量")
        return {"position_type": "破位点", "confidence": min(80, 40 + len(reasons) * 20),
                "reasons": reasons, "action": "止损或回避"}

    # 支撑反弹：小幅上涨 + 缩量后放量
    if 0 < chg < 3 and vol_ratio > 1.0:
        reasons.append(f"温和上涨{chg:.1f}%")
        if turnover > 3:
            reasons.append(f"换手率{turnover:.1f}%")
        if len(reasons) >= 2:
            return {"position_type": "支撑反弹", "confidence": min(70, 30 + len(reasons) * 20),
                    "reasons": reasons, "action": "轻仓试多"}

    return {"position_type": "无明确信号", "confidence": 0,
            "reasons": ["不满足分类条件"], "action": "观望"}


# ── Tab10：因子选股 ───────────────────────────────────────────────────────
with tab_factor:
    st.markdown('<p style="color:#00B4D8;font-size:14px;font-weight:bold">📊 量化多因子选股</p>',
                unsafe_allow_html=True)
    st.markdown('<p style="color:#888;font-size:12px">基于动量/量能/技术/波动/估值/市值六类因子，Z-score截面标准化，加权评分排名</p>',
                unsafe_allow_html=True)

    from modules.factor_screener import (
        run_factor_screening, WEIGHT_PRESETS, UNIVERSE_OPTIONS,
        FactorScreenerCache,
    )
    from modules.signal_generator import classify_position

    # 控制面板
    fc1, fc2, fc3, fc4, fc5 = st.columns([1, 1, 1, 1, 1])
    with fc1:
        factor_universe = st.selectbox("选股范围", UNIVERSE_OPTIONS, key="factor_universe")
    with fc2:
        preset_options = list(WEIGHT_PRESETS.keys()) + ["自定义"]
        factor_preset = st.selectbox("因子权重", preset_options, key="factor_preset")
    with fc3:
        factor_top_n = st.number_input("深度分析数量", min_value=10, max_value=200, value=50, step=10, key="factor_top_n")
    with fc4:
        factor_btn = st.button("🔍 开始选股", type="primary", width="stretch", key="factor_btn")
    with fc5:
        st.link_button("📺 TradingView筛选器", "https://cn.tradingview.com/screener/", width="stretch")

    # 自定义权重
    custom_w = None
    if factor_preset == "自定义":
        with st.expander("⚙️ 自定义因子权重", expanded=True):
            cw1, cw2, cw3 = st.columns(3)
            with cw1:
                w_momentum = st.slider("动量", 0.0, 0.5, 0.20, 0.05, key="cw_mom")
                w_volume = st.slider("量能", 0.0, 0.5, 0.20, 0.05, key="cw_vol")
            with cw2:
                w_tech = st.slider("技术", 0.0, 0.5, 0.25, 0.05, key="cw_tech")
                w_volatility = st.slider("波动", 0.0, 0.5, 0.15, 0.05, key="cw_vola")
            with cw3:
                w_valuation = st.slider("估值", 0.0, 0.5, 0.10, 0.05, key="cw_val")
                w_cap = st.slider("市值", 0.0, 0.5, 0.10, 0.05, key="cw_cap")
            total_w = w_momentum + w_volume + w_tech + w_volatility + w_valuation + w_cap
            if abs(total_w - 1.0) > 0.01:
                st.warning(f"权重总和 = {total_w:.2f}，建议调整至1.0")
            custom_w = {"动量": w_momentum, "量能": w_volume, "技术": w_tech,
                        "波动": w_volatility, "估值": w_valuation, "市值": w_cap}

    # 缓存检查
    factor_cache = FactorScreenerCache()
    cached_factor = factor_cache.get_cached_result(factor_universe, factor_preset, factor_top_n)

    factor_df = None
    if factor_btn:
        progress_bar = st.progress(0, text="初始化因子选股...")

        def _factor_progress(pct, text):
            progress_bar.progress(min(pct, 1.0), text=text)

        factor_df = run_factor_screening(
            universe_key=factor_universe,
            preset=factor_preset,
            custom_weights=custom_w,
            top_n=factor_top_n,
            deep_analysis=True,
            progress_callback=_factor_progress,
        )
        if factor_df is not None and not factor_df.empty:
            factor_cache.save_result(factor_df, factor_universe, factor_preset, factor_top_n)
        progress_bar.empty()
    elif cached_factor is not None and not cached_factor.empty:
        factor_df = cached_factor
        st.info("📦 使用4小时内缓存数据，点击「开始选股」刷新")

    if factor_df is not None and not factor_df.empty:
        # 汇总指标
        fm = st.columns(4)
        with fm[0]:
            render_trend_item("入选股数", f"{len(factor_df)}", "#F6465D")
        with fm[1]:
            top_score = factor_df["综合评分"].max() if "综合评分" in factor_df.columns else 0
            render_trend_item("最高分", f"{top_score:.2f}", "#FFD700")
        with fm[2]:
            avg_score = factor_df["综合评分"].mean() if "综合评分" in factor_df.columns else 0
            render_trend_item("平均分", f"{avg_score:.2f}", "#00B4D8")
        with fm[3]:
            bullish = len(factor_df[factor_df.get("cat_动量", pd.Series(dtype=float)) > 0]) if "cat_动量" in factor_df.columns else 0
            render_trend_item("动量偏多", f"{bullish}/{len(factor_df)}", "#0ECB81")

        # 综合评分排名表
        st.markdown("##### 📊 综合评分排名")
        show_cols = ["代码", "名称"]
        if "综合评分" in factor_df.columns:
            show_cols.append("综合评分")
        for cat_col in ["cat_动量", "cat_量能", "cat_技术", "cat_波动", "cat_估值", "cat_市值"]:
            if cat_col in factor_df.columns:
                show_cols.append(cat_col)
        for extra in ["涨跌幅", "量比", "换手率", "最新价", "流通市值"]:
            if extra in factor_df.columns:
                show_cols.append(extra)
        if "排名" in factor_df.columns:
            show_cols.append("排名")

        display_factor = factor_df[[c for c in show_cols if c in factor_df.columns]].copy()

        # 格式化数值列
        for col in display_factor.columns:
            if col.startswith("cat_") or col == "综合评分":
                display_factor[col] = display_factor[col].apply(lambda x: f"{x:.2f}" if pd.notna(x) else "-")
        if "流通市值" in display_factor.columns:
            display_factor["流通市值"] = display_factor["流通市值"].apply(
                lambda x: f"{float(x)/1e8:.1f}亿" if pd.notna(x) and float(x) >= 1e8 else
                (f"{float(x)/1e4:.0f}万" if pd.notna(x) and float(x) >= 1e4 else "-")
            )

        # 重命名类别列
        rename_map = {"cat_动量": "动量", "cat_量能": "量能", "cat_技术": "技术",
                      "cat_波动": "波动", "cat_估值": "估值", "cat_市值": "市值"}
        display_factor = display_factor.rename(columns=rename_map)

        st.dataframe(display_factor, hide_index=True, width="stretch", height=400)

        # 买卖点分类
        st.markdown("##### 🎯 买卖点分类")
        st.markdown('<p style="color:#888;font-size:12px">基于支撑压力位、RSI、MACD、量能等多维度综合判定</p>',
                    unsafe_allow_html=True)

        # 对Top 20做位置分类（避免过多API调用）
        position_results = []
        for _, row in factor_df.head(20).iterrows():
            code = str(row.get("代码", ""))
            name = str(row.get("名称", ""))
            score = float(row.get("综合评分", 0)) if "综合评分" in factor_df.columns else 0
            # 位置分类需要历史数据，这里用简化版基于spot因子
            pos = _classify_from_spot(row)
            pos["代码"] = code
            pos["名称"] = name
            pos["综合评分"] = score
            position_results.append(pos)

        if position_results:
            pos_df = pd.DataFrame(position_results)
            sub_tab_dip, sub_tab_break, sub_tab_bounce, sub_tab_down = st.tabs(
                ["🟢 低吸候选", "🔵 突破候选", "🟡 支撑反弹", "🔴 破位警告"]
            )
            for sub_tab, pos_type, color in [
                (sub_tab_dip, "低吸点", "#0ECB81"),
                (sub_tab_break, "突破点", "#00B4D8"),
                (sub_tab_bounce, "支撑反弹", "#FFD700"),
                (sub_tab_down, "破位点", "#F6465D"),
            ]:
                with sub_tab:
                    filtered = pos_df[pos_df["position_type"] == pos_type]
                    if filtered.empty:
                        st.info(f"当前无{pos_type}候选")
                    else:
                        for _, r in filtered.iterrows():
                            reasons_str = " / ".join(r.get("reasons", []))
                            st.markdown(
                                f'<div style="background:#161b22;padding:8px 12px;border-radius:6px;'
                                f'border-left:3px solid {color};margin-bottom:6px">'
                                f'<span style="color:#c9d1d9;font-weight:bold">{r["代码"]} {r["名称"]}</span>'
                                f' <span style="color:#8b949e">综合分: {r["综合评分"]:.2f}</span><br>'
                                f'<span style="color:{color};font-size:12px">{r.get("action","")}</span>'
                                f' <span style="color:#8b949e;font-size:11px">{reasons_str}</span>'
                                f'</div>',
                                unsafe_allow_html=True,
                            )

        # 选择分析
        st.markdown("##### 🔍 选择股票深度分析")
        factor_options = factor_df["代码"].tolist() if "代码" in factor_df.columns else []
        if factor_options:
            selected_factor = st.selectbox(
                "选择股票",
                factor_options,
                format_func=lambda x: f"{x} - {factor_df[factor_df['代码']==x].iloc[0].get('名称', '')} (分:{factor_df[factor_df['代码']==x].iloc[0].get('综合评分', 0):.2f})",
                key="factor_select",
            )
            if st.button("📈 分析该股票", key="factor_analyze"):
                code = str(selected_factor)
                if code.startswith("6") or code.startswith("5"):
                    full_code = f"{code}.SS"
                else:
                    full_code = f"{code}.SZ"
                st.session_state["manual_ticker"] = full_code
                st.rerun()
    elif factor_btn:
        st.warning("选股未返回结果，请稍后重试")
    else:
        st.info("点击「开始选股」启动量化多因子扫描")


# ── Tab11：社媒推文生成 ───────────────────────────────────────────────────
with tab_social:
    st.markdown('<p style="color:#00B4D8;font-size:14px;font-weight:bold">📱 社交媒体推文生成</p>',
                unsafe_allow_html=True)

    st.markdown(
        '<p style="color:#888;font-size:12px">将技术分析结果一键转换为适合分享的内容格式</p>',
        unsafe_allow_html=True
    )

    # 初始化session_state
    if 'generated_post' not in st.session_state:
        st.session_state.generated_post = None
    if 'generated_summary' not in st.session_state:
        st.session_state.generated_summary = None

    # 确保info_analysis已计算（用于包含基本面信息选项）
    try:
        info_analysis = run_info_analysis(ticker, info, df, fundamental, tech_analysis=analysis)
    except Exception:
        info_analysis = None

    # 专业分析推文生成
    with st.expander("📕 社媒推文生成", expanded=True):
        st.markdown("##### 🎯 生成设置")

        col1, col2 = st.columns(2)
        with col1:
            post_style = st.selectbox(
                "文案风格",
                ["专业研报", "小红书风格", "简洁摘要"],
                help="选择推文的语言风格：专业研报=深度技术分析；小红书风格=社交媒体友好；简洁摘要=快速概览",
                key="post_style"
            )
        with col2:
            if 'include_fundamental' not in st.session_state:
                st.session_state.include_fundamental = True
            include_fundamental = st.toggle("包含基本面信息", key="include_fundamental",
                                           help="在分析中融入基本面数据")

        # 生成推文按钮
        generate_btn = st.button(
            "✨ 生成推文",
            type="primary",
            key="generate_social_post",
            help="点击生成社媒推文"
        )

        # 生成结果文件路径
        post_file_path = f"generated_posts/{ticker}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"

        if generate_btn:
            with st.spinner("正在生成推文..."):
                try:
                    # 检查必要数据是否存在
                    if not analysis:
                        st.error("分析数据不存在，请先获取股票数据")
                    elif current_price is None:
                        st.error("当前价格数据不存在")
                    else:
                        # 准备数据
                        df_info = {
                            "current_price": current_price,
                            "chg_pct": chg_pct if chg_pct else 0,
                        }

                        # 生成推文 - 使用新的专业分析生成器
                        if include_fundamental and info_analysis:
                            post_content = generate_professional_analysis(ticker, name, analysis, df_info, info_analysis)
                        elif post_style == "专业研报":
                            post_content = generate_professional_analysis(ticker, name, analysis, df_info)
                        elif post_style == "简洁摘要":
                            post_content = generate_compact_summary(ticker, name, analysis, df_info)
                        else:  # 小红书风格
                            post_content = generate_xiaohongshu_post(ticker, name, analysis, df_info)

                        # 保存到session_state
                        st.session_state.generated_post = post_content

                        # 保存到文件
                        import os
                        os.makedirs("generated_posts", exist_ok=True)
                        with open(post_file_path, 'w', encoding='utf-8') as f:
                            f.write(f"股票: {ticker} ({name})\n")
                            f.write(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                            f.write(f"风格: {post_style}\n")
                            f.write("=" * 50 + "\n\n")
                            f.write(post_content)

                        st.success(f"推文生成成功！已保存至: {post_file_path}")

                except Exception as e:
                    st.error(f"生成失败: {e}")
                    # 保存错误日志到文件
                    error_path = f"generated_posts/error_{ticker}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
                    import os
                    os.makedirs("generated_posts", exist_ok=True)
                    with open(error_path, 'w', encoding='utf-8') as f:
                        f.write(f"Error generating post for {ticker}:\n")
                        f.write(str(e))
                        f.write("\n\n")
                        import traceback
                        f.write(traceback.format_exc())
                    st.info(f"错误日志已保存至: {error_path}")

        # 显示生成的推文（如果有）

        # 显示生成的推文（如果有）
        if st.session_state.generated_post:
            st.markdown("---")
            st.markdown("##### 生成结果")
            st.text_area("推文内容", st.session_state.generated_post, height=300)
            if st.button("复制到剪贴板", key="copy_post_btn"):
                st.write("请手动复制上方文本")

# ── Tab12：设置 ────────────────────────────────────────────────────────
with tab_settings:
    st.markdown('<p style="color:#00B4D8;font-size:14px;font-weight:bold">设置</p>',
                unsafe_allow_html=True)

    st.markdown("##### 图表主题")
    chart_theme = st.selectbox("配色方案", ["深色(默认)", "浅色", "经典"], key="chart_theme_sel")

    st.markdown("##### 数据缓存")
    if st.button("清除所有缓存", key="clear_cache_btn"):
        st.cache_data.clear()
        st.success("缓存已清除！")

    st.markdown("##### 系统信息")
    st.info(f"Streamlit 版本: {st.__version__}")
    st.info("Custom-Model v2.0 — 量化分析系统")
