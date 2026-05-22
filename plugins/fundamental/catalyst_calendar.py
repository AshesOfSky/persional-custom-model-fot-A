"""
催化剂日历（A 股优先）

数据源：
- A 股财报日：akshare stock_yjbb_em / stock_yysj_em
- A 股分红除权：stock_fhps_detail_em
- A 股公告：FinancialReportsPlugin.fetch_announcements
- A 股限售解禁：stock_restricted_release_queue_sina
- 美股（辅）：yfinance stock.calendar
- 宏观：LPR / FOMC 等

排序：time × probability × impact 加权
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Optional
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)


@dataclass
class CatalystEvent:
    """催化剂事件"""
    date: datetime
    event_type: str                    # earnings / dividend / announcement / restricted_release / macro
    title: str
    description: str
    impact: str                        # high / medium / low
    probability: float = 1.0           # 发生概率（非"确定发生"）
    scenario_mapping: str = ""         # "超预期→Bull" / "不及预期→Bear"
    related_kpis: List[str] = field(default_factory=list)
    importance_score: float = 0.0      # 综合排序分
    is_restricted_release: bool = False  # A 股独有：限售解禁


class CatalystCalendar:
    """催化剂日历"""

    IMPACT_SCORES = {"high": 3.0, "medium": 2.0, "low": 1.0}

    def __init__(self, data_router=None):
        self.data_router = data_router
        self.events: List[CatalystEvent] = []

    def _ensure_router(self):
        if self.data_router is None:
            from modules.data_router import DataRouter
            self.data_router = DataRouter()

    def get_upcoming(self, ticker: str, days_ahead: int = 90) -> List[CatalystEvent]:
        """
        获取未来催化剂事件（统一入口）
        A 股优先，美股兼容
        """
        self._ensure_router()
        is_a_share = (
            ticker.endswith(".SS") or ticker.endswith(".SZ") or
            (ticker.replace(".", "").isdigit() and len(ticker.replace(".", "")) == 6)
        )

        events = []

        if is_a_share:
            events.extend(self._get_earnings_dates_a_share(ticker))
            events.extend(self._get_dividend_dates(ticker))
            events.extend(self._get_announcements_a_share(ticker))
            events.extend(self._get_restricted_release(ticker))
        else:
            events.extend(self._get_earnings_dates_us(ticker))
            events.extend(self._get_dividend_dates_us(ticker))

        # 宏观节点（所有市场）
        events.extend(self._get_macro_events())

        # 筛选未来 N 天内
        cutoff = datetime.now() + timedelta(days=days_ahead)
        events = [e for e in events if e.date <= cutoff and e.date >= datetime.now() - timedelta(days=1)]

        # 计算 importance_score 并排序
        for e in events:
            days_until = max(1, (e.date - datetime.now()).days)
            e.importance_score = (e.probability * self.IMPACT_SCORES.get(e.impact, 1.0)) / np.log(days_until + 1)

        events.sort(key=lambda x: x.importance_score, reverse=True)
        self.events = events
        return events

    def _get_earnings_dates_a_share(self, ticker: str) -> List[CatalystEvent]:
        """A 股财报日期"""
        code = ticker.replace(".SS", "").replace(".SZ", "")
        events = []

        # 方法1: 业绩预告
        try:
            import akshare as ak
            df = ak.stock_yysj_em(symbol=code)
            if df is not None and not df.empty:
                for _, row in df.head(4).iterrows():
                    date_str = row.get('公告日期', row.get('预报日期', ''))
                    if date_str:
                        try:
                            date = pd.to_datetime(date_str)
                            events.append(CatalystEvent(
                                date=date,
                                event_type="earnings",
                                title=f"财报发布 ({row.get('报告期', 'Q?')})",
                                description=f"业绩报告: {row.get('预告类型', '定期报告')}",
                                impact="high",
                                probability=0.95,
                                scenario_mapping="超预期→Bull / miss→Bear",
                                related_kpis=["Revenue", "Net Income", "EPS"],
                            ))
                        except Exception:
                            continue
        except Exception as e:
            logger.warning(f"A-share earnings fetch failed for {ticker}: {e}")

        # 若没数据，用推算（每季度）
        if not events:
            base = datetime.now()
            for i in range(1, 5):
                q_date = base + timedelta(days=i * 90)
                events.append(CatalystEvent(
                    date=q_date,
                    event_type="earnings",
                    title=f"Q{((base.month - 1) // 3 + i - 1) % 4 + 1} 财报（估算）",
                    description="基于历史发布周期推算",
                    impact="high",
                    probability=0.80,
                    scenario_mapping="超预期→Bull / miss→Bear",
                    related_kpis=["Revenue", "Net Income", "EPS"],
                ))

        return events

    def _get_dividend_dates(self, ticker: str) -> List[CatalystEvent]:
        """A 股分红除权"""
        code = ticker.replace(".SS", "").replace(".SZ", "")
        events = []

        try:
            import akshare as ak
            df = ak.stock_fhps_detail_em(symbol=code)
            if df is not None and not df.empty:
                for _, row in df.head(2).iterrows():
                    date_str = row.get('公告日期', '')
                    if date_str:
                        try:
                            date = pd.to_datetime(date_str)
                            dividend = row.get('每股派息', row.get('分红', 'N/A'))
                            events.append(CatalystEvent(
                                date=date,
                                event_type="dividend",
                                title=f"分红除权 ({dividend})",
                                description=f"分红方案: {row.get('分红方案', '待公告')}",
                                impact="medium",
                                probability=0.90,
                                scenario_mapping="高股息→价值认可",
                                related_kpis=["Dividend Yield", "Payout Ratio"],
                            ))
                        except Exception:
                            continue
        except Exception as e:
            logger.warning(f"Dividend fetch failed for {ticker}: {e}")

        return events

    def _get_announcements_a_share(self, ticker: str) -> List[CatalystEvent]:
        """A 股公告（复用 FinancialReportsPlugin，增加重要性分级）"""
        events = []
        try:
            from plugins.data_ingestion.financial_reports import FinancialReportsPlugin
            plugin = FinancialReportsPlugin()
            announcements = plugin.fetch_announcements(ticker, days=90)

            importance_map = {
                "critical": ("high", 0.90),
                "important": ("medium", 0.75),
                "normal": ("low", 0.60),
            }

            for ann in announcements[:10]:  # 取最近10条
                imp, prob = importance_map.get(ann.importance, ("low", 0.60))
                events.append(CatalystEvent(
                    date=ann.publish_date,
                    event_type="announcement",
                    title=ann.title[:50],
                    description=ann.content_summary[:100],
                    impact=imp,
                    probability=prob,
                    scenario_mapping="重大公告→视内容而定",
                    related_kpis=["Sentiment"],
                ))
        except Exception as e:
            logger.warning(f"Announcements fetch failed for {ticker}: {e}")

        return events

    def _get_restricted_release(self, ticker: str) -> List[CatalystEvent]:
        """A 股限售解禁（短期股价压力信号）"""
        code = ticker.replace(".SS", "").replace(".SZ", "")
        events = []

        try:
            import akshare as ak
            df = ak.stock_restricted_release_queue_sina(symbol=code)
            if df is not None and not df.empty:
                for _, row in df.head(5).iterrows():
                    date_str = row.get('解禁日期', '')
                    if date_str:
                        try:
                            date = pd.to_datetime(date_str)
                            shares = row.get('解禁数量', 'N/A')
                            events.append(CatalystEvent(
                                date=date,
                                event_type="restricted_release",
                                title=f"限售解禁 ({shares}万股)",
                                description=f"限售股解禁，潜在抛压",
                                impact="high",
                                probability=0.98,
                                scenario_mapping="大额解禁→短期压力",
                                related_kpis=["流通股比例", "解禁市值"],
                                is_restricted_release=True,
                            ))
                        except Exception:
                            continue
        except Exception as e:
            logger.warning(f"Restricted release fetch failed for {ticker}: {e}")

        return events

    def _get_earnings_dates_us(self, ticker: str) -> List[CatalystEvent]:
        """美股财报"""
        events = []
        try:
            import yfinance as yf
            stock = yf.Ticker(ticker)
            calendar = stock.calendar
            if calendar is not None and not calendar.empty:
                for _, row in calendar.iterrows():
                    date = row.get('Earnings Date')
                    if date and not pd.isna(date):
                        events.append(CatalystEvent(
                            date=pd.to_datetime(date),
                            event_type="earnings",
                            title="财报发布",
                            description="季度业绩公告",
                            impact="high",
                            probability=0.95,
                            scenario_mapping="beat→Bull / miss→Bear",
                            related_kpis=["Revenue", "EPS", "Guidance"],
                        ))
        except Exception as e:
            logger.warning(f"US earnings fetch failed for {ticker}: {e}")

        # Fallback: 每季度推算
        if not events:
            base = datetime.now()
            for i in range(1, 5):
                events.append(CatalystEvent(
                    date=base + timedelta(days=i * 90),
                    event_type="earnings",
                    title=f"Q{((base.month - 1) // 3 + i - 1) % 4 + 1} 财报（估算）",
                    description="基于季度周期推算",
                    impact="high",
                    probability=0.80,
                    scenario_mapping="beat→Bull / miss→Bear",
                    related_kpis=["Revenue", "EPS"],
                ))

        return events

    def _get_dividend_dates_us(self, ticker: str) -> List[CatalystEvent]:
        """美股分红"""
        events = []
        try:
            import yfinance as yf
            stock = yf.Ticker(ticker)
            dividends = stock.dividends
            if dividends is not None and len(dividends) > 0:
                # 最近分红历史，推算下一次
                last_date = dividends.index[-1]
                next_date = last_date + timedelta(days=90)
                if next_date > datetime.now():
                    events.append(CatalystEvent(
                        date=next_date,
                        event_type="dividend",
                        title="预计分红",
                        description=f"上次分红: {dividends.iloc[-1]:.4f}",
                        impact="low",
                        probability=0.85,
                        scenario_mapping="稳定分红→防御属性",
                        related_kpis=["Dividend Yield"],
                    ))
        except Exception as e:
            logger.warning(f"US dividend fetch failed for {ticker}: {e}")
        return events

    def _get_macro_events(self) -> List[CatalystEvent]:
        """宏观节点（简化版）"""
        events = []
        now = datetime.now()

        # 国内 LPR（每月20日左右）
        for month_offset in range(3):
            target_month = now.month + month_offset
            target_year = now.year
            while target_month > 12:
                target_month -= 12
                target_year += 1
            lpr_date = datetime(target_year, target_month, 20)
            if lpr_date >= now:
                events.append(CatalystEvent(
                    date=lpr_date,
                    event_type="macro",
                    title="LPR 报价日",
                    description="贷款市场报价利率",
                    impact="medium",
                    probability=0.95,
                    scenario_mapping="降息→Bull / 加息→Bear",
                    related_kpis=["Interest Rate", "Liquidity"],
                ))

        # FOMC（简化：每6周一次）
        fomc_base = datetime(2026, 1, 28)
        for i in range(8):
            fomc_date = fomc_base + timedelta(weeks=i * 6)
            if fomc_date >= now:
                events.append(CatalystEvent(
                    date=fomc_date,
                    event_type="macro",
                    title="FOMC 议息会议",
                    description="美联储利率决议",
                    impact="high",
                    probability=0.95,
                    scenario_mapping="降息→全球流动性宽松",
                    related_kpis=["Fed Funds Rate", "USD/CNY"],
                ))

        return events

    def to_dataframe(self) -> pd.DataFrame:
        """转换为 DataFrame"""
        if not self.events:
            return pd.DataFrame()
        return pd.DataFrame([
            {
                'Date': e.date.strftime('%Y-%m-%d'),
                'Type': e.event_type,
                'Title': e.title,
                'Impact': e.impact,
                'Probability': f"{e.probability:.0%}",
                'Scenario': e.scenario_mapping,
                'Score': f"{e.importance_score:.2f}",
                'Restricted': '⚠️' if e.is_restricted_release else '',
            }
            for e in self.events
        ])


def quick_catalyst_check(ticker: str, days_ahead: int = 90) -> Dict:
    """快速催化剂检查"""
    calendar = CatalystCalendar()
    events = calendar.get_upcoming(ticker, days_ahead)

    earnings = [e for e in events if e.event_type == "earnings"]
    restricted = [e for e in events if e.is_restricted_release]

    return {
        'ticker': ticker,
        'total_events': len(events),
        'earnings_count': len(earnings),
        'restricted_release_count': len(restricted),
        'next_earnings': earnings[0].date.strftime('%Y-%m-%d') if earnings else None,
        'next_restricted': restricted[0].date.strftime('%Y-%m-%d') if restricted else None,
        'top_events': calendar.to_dataframe().head(10).to_dict('records') if not calendar.to_dataframe().empty else [],
    }


if __name__ == "__main__":
    calendar = CatalystCalendar()
    events = calendar.get_upcoming("600519.SS", days_ahead=90)
    print(f"Total events: {len(events)}")
    for e in events[:5]:
        print(f"{e.date.strftime('%Y-%m-%d')} | {e.event_type:15s} | {e.title} | impact={e.impact}")
