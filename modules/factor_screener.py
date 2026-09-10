import akshare as ak

import logging

logger = logging.getLogger(__name__)


def get_northbound_score(ticker: str) -> float:
    """
    北向资金因子：5 日个股北向净流入 + 20 日趋势
    返回 -1.0..+1.0 标量
    """
    try:
        code = str(ticker).upper().replace(".SS", "").replace(".SZ", "").replace(".BJ", "")
        df = ak.stock_hsgt_individual_em(stock=code)
        if df is None or df.empty or "持股市值" not in df.columns:
            return 0.0
        df = df.head(25).copy()
        # 持股市值差分作为净流入近似
        net_flow = df["持股市值"].astype(float).diff(-1).fillna(0)
        net_5d = float(net_flow.head(5).sum())
        net_20d = float(net_flow.head(20).sum())
        score = 0.0
        if net_5d > 1e8:
            score += 0.6
        elif net_5d < -1e8:
            score -= 0.6
        # 20 日趋势加成
        if net_20d > 0 and net_5d > 0:
            score += 0.4
        elif net_20d < 0 and net_5d < 0:
            score -= 0.4
        return max(-1.0, min(1.0, score))
    except Exception as e:
        logger.debug(f"北向资金因子计算失败 {ticker}: {e}")
        return 0.0


