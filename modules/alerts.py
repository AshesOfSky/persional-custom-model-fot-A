"""
alerts.py — 预警系统
支持价格预警、指标金叉死叉预警、布林带突破预警
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Callable
from datetime import datetime, timedelta
from enum import Enum
import json
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 预警配置目录
ALERTS_DIR = Path(__file__).parent.parent / "cache" / "alerts"
ALERTS_FILE = ALERTS_DIR / "alert_rules.json"
ALERTS_HISTORY_FILE = ALERTS_DIR / "alert_history.json"


class AlertType(Enum):
    PRICE_ABOVE = "price_above"  # 价格上破
    PRICE_BELOW = "price_below"  # 价格下破
    GOLDEN_CROSS = "golden_cross"  # 金叉
    DEAD_CROSS = "dead_cross"  # 死叉
    BB_UPPER_BREAK = "bb_upper_break"  # 突破布林上轨
    BB_LOWER_BREAK = "bb_lower_break"  # 突破布林下轨
    KDJ_OVERBOUGHT = "kdj_overbought"  # KDJ超买
    KDJ_OVERSOLD = "kdj_oversold"  # KDJ超卖
    RSI_OVERBOUGHT = "rsi_overbought"  # RSI超买
    RSI_OVERSOLD = "rsi_oversold"  # RSI超卖
    CCI_OVERBOUGHT = "cci_overbought"  # CCI超买
    CCI_OVERSOLD = "cci_oversold"  # CCI超卖
    WR_OVERBOUGHT = "wr_overbought"  # WR超买
    WR_OVERSOLD = "wr_oversold"  # WR超卖
    DMI_CROSS = "dmi_cross"  # DMI交叉
    VOLUME_SPIKE = "volume_spike"  # 成交量异常
    CUSTOM = "custom"  # 自定义


class AlertStatus(Enum):
    ACTIVE = "active"
    TRIGGERED = "triggered"
    DISABLED = "disabled"
    EXPIRED = "expired"


@dataclass
class AlertRule:
    """预警规则"""
    id: str
    ticker: str
    alert_type: AlertType
    name: str
    condition: Dict
    status: AlertStatus = AlertStatus.ACTIVE
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    expires_at: Optional[str] = None
    triggered_count: int = 0
    last_triggered: Optional[str] = None
    message_template: str = ""

    def to_dict(self) -> Dict:
        return {
            'id': self.id,
            'ticker': self.ticker,
            'alert_type': self.alert_type.value,
            'name': self.name,
            'condition': self.condition,
            'status': self.status.value,
            'created_at': self.created_at,
            'expires_at': self.expires_at,
            'triggered_count': self.triggered_count,
            'last_triggered': self.last_triggered,
            'message_template': self.message_template,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'AlertRule':
        return cls(
            id=data['id'],
            ticker=data['ticker'],
            alert_type=AlertType(data['alert_type']),
            name=data['name'],
            condition=data['condition'],
            status=AlertStatus(data.get('status', 'active')),
            created_at=data.get('created_at'),
            expires_at=data.get('expires_at'),
            triggered_count=data.get('triggered_count', 0),
            last_triggered=data.get('last_triggered'),
            message_template=data.get('message_template', ''),
        )


@dataclass
class AlertEvent:
    """预警触发事件"""
    rule_id: str
    ticker: str
    alert_type: AlertType
    triggered_at: str
    message: str
    price: float
    details: Dict = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            'rule_id': self.rule_id,
            'ticker': self.ticker,
            'alert_type': self.alert_type.value,
            'triggered_at': self.triggered_at,
            'message': self.message,
            'price': self.price,
            'details': self.details,
        }


class AlertManager:
    """预警管理器"""

    def __init__(self):
        self._ensure_directories()
        self.rules: Dict[str, AlertRule] = {}
        self.history: List[AlertEvent] = []
        self._load_data()

    def _ensure_directories(self):
        """确保目录存在"""
        ALERTS_DIR.mkdir(parents=True, exist_ok=True)

    def _load_data(self):
        """加载预警规则和历史"""
        # 加载规则
        if ALERTS_FILE.exists():
            try:
                with open(ALERTS_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for rule_data in data.get('rules', []):
                        rule = AlertRule.from_dict(rule_data)
                        self.rules[rule.id] = rule
                logger.info(f"加载了 {len(self.rules)} 条预警规则")
            except Exception as e:
                logger.error(f"加载预警规则失败: {e}")

        # 加载历史
        if ALERTS_HISTORY_FILE.exists():
            try:
                with open(ALERTS_HISTORY_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.history = [AlertEvent(**h) for h in data.get('history', [])]
            except Exception as e:
                logger.error(f"加载预警历史失败: {e}")

    def _save_rules(self):
        """保存预警规则"""
        try:
            with open(ALERTS_FILE, 'w', encoding='utf-8') as f:
                json.dump({
                    'rules': [r.to_dict() for r in self.rules.values()]
                }, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存预警规则失败: {e}")

    def _save_history(self):
        """保存预警历史"""
        try:
            with open(ALERTS_HISTORY_FILE, 'w', encoding='utf-8') as f:
                json.dump({
                    'history': [h.to_dict() for h in self.history[-500:]]  # 只保留最近500条
                }, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存预警历史失败: {e}")

    def add_rule(self, ticker: str, alert_type: AlertType, name: str,
                 condition: Dict, expires_hours: int = None) -> AlertRule:
        """
        添加预警规则

        Args:
            ticker: 标的代码
            alert_type: 预警类型
            name: 预警名称
            condition: 条件字典
            expires_hours: 过期时间（小时）
        """
        import uuid

        rule_id = str(uuid.uuid4())[:8]

        expires_at = None
        if expires_hours:
            expires_at = (datetime.now() + timedelta(hours=expires_hours)).isoformat()

        # 默认消息模板
        message_templates = {
            AlertType.PRICE_ABOVE: f"{ticker} 价格上破 {{threshold}}",
            AlertType.PRICE_BELOW: f"{ticker} 价格下破 {{threshold}}",
            AlertType.GOLDEN_CROSS: f"{ticker} 出现金叉信号",
            AlertType.DEAD_CROSS: f"{ticker} 出现死叉信号",
            AlertType.BB_UPPER_BREAK: f"{ticker} 突破布林上轨",
            AlertType.BB_LOWER_BREAK: f"{ticker} 跌破布林下轨",
            AlertType.KDJ_OVERBOUGHT: f"{ticker} KDJ超买",
            AlertType.KDJ_OVERSOLD: f"{ticker} KDJ超卖",
            AlertType.VOLUME_SPIKE: f"{ticker} 成交量异常放大",
            AlertType.CUSTOM: f"{ticker} 触发预警",
        }

        rule = AlertRule(
            id=rule_id,
            ticker=ticker,
            alert_type=alert_type,
            name=name,
            condition=condition,
            expires_at=expires_at,
            message_template=message_templates.get(alert_type, "触发预警"),
        )

        self.rules[rule_id] = rule
        self._save_rules()

        logger.info(f"添加预警规则: {name} ({rule_id})")
        return rule

    def remove_rule(self, rule_id: str) -> bool:
        """删除预警规则"""
        if rule_id in self.rules:
            del self.rules[rule_id]
            self._save_rules()
            return True
        return False

    def disable_rule(self, rule_id: str) -> bool:
        """禁用预警规则"""
        if rule_id in self.rules:
            self.rules[rule_id].status = AlertStatus.DISABLED
            self._save_rules()
            return True
        return False

    def enable_rule(self, rule_id: str) -> bool:
        """启用预警规则"""
        if rule_id in self.rules:
            self.rules[rule_id].status = AlertStatus.ACTIVE
            self._save_rules()
            return True
        return False

    def check_alerts(self, ticker: str, df: pd.DataFrame) -> List[AlertEvent]:
        """
        检查所有活跃预警规则是否触发
        返回触发的预警事件列表
        """
        if df.empty:
            return []

        triggered = []
        last_row = df.iloc[-1]
        prev_row = df.iloc[-2] if len(df) > 1 else last_row
        current_price = last_row['Close']

        for rule in self.rules.values():
            # 检查是否是目标标的
            if rule.ticker != ticker:
                continue

            # 检查状态
            if rule.status != AlertStatus.ACTIVE:
                continue

            # 检查是否过期
            if rule.expires_at and datetime.now().isoformat() > rule.expires_at:
                rule.status = AlertStatus.EXPIRED
                continue

            # 检查条件
            event = self._check_rule(rule, df, last_row, prev_row, current_price)
            if event:
                triggered.append(event)
                self.history.append(event)

                # 更新规则状态
                rule.triggered_count += 1
                rule.last_triggered = datetime.now().isoformat()

        if triggered:
            self._save_rules()
            self._save_history()

        return triggered

    def _check_rule(self, rule: AlertRule, df: pd.DataFrame,
                   last_row: pd.Series, prev_row: pd.Series, current_price: float) -> Optional[AlertEvent]:
        """检查单个规则是否触发"""

        condition = rule.condition
        triggered = False
        details = {}

        if rule.alert_type == AlertType.PRICE_ABOVE:
            threshold = condition.get('threshold', 0)
            if current_price > threshold and prev_row['Close'] <= threshold:
                triggered = True
                details = {'threshold': threshold, 'previous': prev_row['Close']}

        elif rule.alert_type == AlertType.PRICE_BELOW:
            threshold = condition.get('threshold', 0)
            if current_price < threshold and prev_row['Close'] >= threshold:
                triggered = True
                details = {'threshold': threshold, 'previous': prev_row['Close']}

        elif rule.alert_type == AlertType.GOLDEN_CROSS:
            fast = condition.get('fast', 'EMA_8')
            slow = condition.get('slow', 'EMA_13')
            if fast in df.columns and slow in df.columns:
                if last_row[fast] > last_row[slow] and prev_row[fast] <= prev_row[slow]:
                    triggered = True
                    details = {'fast': fast, 'slow': slow}

        elif rule.alert_type == AlertType.DEAD_CROSS:
            fast = condition.get('fast', 'EMA_8')
            slow = condition.get('slow', 'EMA_13')
            if fast in df.columns and slow in df.columns:
                if last_row[fast] < last_row[slow] and prev_row[fast] >= prev_row[slow]:
                    triggered = True
                    details = {'fast': fast, 'slow': slow}

        elif rule.alert_type == AlertType.BB_UPPER_BREAK:
            if 'BB_upper' in df.columns:
                if current_price > last_row['BB_upper']:
                    triggered = True
                    details = {'bb_upper': last_row['BB_upper']}

        elif rule.alert_type == AlertType.BB_LOWER_BREAK:
            if 'BB_lower' in df.columns:
                if current_price < last_row['BB_lower']:
                    triggered = True
                    details = {'bb_lower': last_row['BB_lower']}

        elif rule.alert_type == AlertType.KDJ_OVERBOUGHT:
            if 'KDJ_K' in df.columns and 'KDJ_D' in df.columns:
                threshold = condition.get('threshold', 80)
                if last_row['KDJ_K'] > threshold and last_row['KDJ_D'] > threshold:
                    triggered = True
                    details = {'k': last_row['KDJ_K'], 'd': last_row['KDJ_D']}

        elif rule.alert_type == AlertType.KDJ_OVERSOLD:
            if 'KDJ_K' in df.columns and 'KDJ_D' in df.columns:
                threshold = condition.get('threshold', 20)
                if last_row['KDJ_K'] < threshold and last_row['KDJ_D'] < threshold:
                    triggered = True
                    details = {'k': last_row['KDJ_K'], 'd': last_row['KDJ_D']}

        elif rule.alert_type == AlertType.VOLUME_SPIKE:
            if 'Volume' in df.columns and 'Vol_MA_20' in df.columns:
                multiplier = condition.get('multiplier', 2.0)
                if last_row['Volume'] > last_row['Vol_MA_20'] * multiplier:
                    triggered = True
                    details = {'volume': last_row['Volume'], 'ma20': last_row['Vol_MA_20']}

        elif rule.alert_type == AlertType.RSI_OVERBOUGHT:
            if 'RSI' in df.columns:
                threshold = condition.get('threshold', 70)
                if last_row['RSI'] > threshold:
                    triggered = True
                    details = {'rsi': last_row['RSI']}

        elif rule.alert_type == AlertType.RSI_OVERSOLD:
            if 'RSI' in df.columns:
                threshold = condition.get('threshold', 30)
                if last_row['RSI'] < threshold:
                    triggered = True
                    details = {'rsi': last_row['RSI']}

        elif rule.alert_type == AlertType.CCI_OVERBOUGHT:
            if 'CCI' in df.columns:
                if last_row['CCI'] > 100:
                    triggered = True
                    details = {'cci': last_row['CCI']}

        elif rule.alert_type == AlertType.CCI_OVERSOLD:
            if 'CCI' in df.columns:
                if last_row['CCI'] < -100:
                    triggered = True
                    details = {'cci': last_row['CCI']}

        elif rule.alert_type == AlertType.WR_OVERBOUGHT:
            if 'WR' in df.columns:
                if last_row['WR'] > -20:
                    triggered = True
                    details = {'wr': last_row['WR']}

        elif rule.alert_type == AlertType.WR_OVERSOLD:
            if 'WR' in df.columns:
                if last_row['WR'] < -80:
                    triggered = True
                    details = {'wr': last_row['WR']}

        elif rule.alert_type == AlertType.DMI_CROSS:
            if 'DI_plus' in df.columns and 'DI_minus' in df.columns:
                cross_type = condition.get('cross_type', 'golden')
                if cross_type == 'golden':
                    if last_row['DI_plus'] > last_row['DI_minus'] and prev_row['DI_plus'] <= prev_row['DI_minus']:
                        triggered = True
                        details = {'di_plus': last_row['DI_plus'], 'di_minus': last_row['DI_minus']}
                else:
                    if last_row['DI_plus'] < last_row['DI_minus'] and prev_row['DI_plus'] >= prev_row['DI_minus']:
                        triggered = True
                        details = {'di_plus': last_row['DI_plus'], 'di_minus': last_row['DI_minus']}

        if triggered:
            message = rule.message_template.format(**details, **condition)
            return AlertEvent(
                rule_id=rule.id,
                ticker=rule.ticker,
                alert_type=rule.alert_type,
                triggered_at=datetime.now().isoformat(),
                message=message,
                price=current_price,
                details=details
            )

        return None

    def get_active_rules(self, ticker: str = None) -> List[AlertRule]:
        """获取活跃的预警规则"""
        rules = [r for r in self.rules.values() if r.status == AlertStatus.ACTIVE]
        if ticker:
            rules = [r for r in rules if r.ticker == ticker]
        return rules

    def get_triggered_history(self, ticker: str = None, limit: int = 50) -> List[AlertEvent]:
        """获取预警触发历史"""
        history = self.history
        if ticker:
            history = [h for h in history if h.ticker == ticker]
        return history[-limit:]

    def clear_history(self):
        """清空预警历史"""
        self.history = []
        self._save_history()

    def get_stats(self) -> Dict:
        """获取预警统计"""
        return {
            'total_rules': len(self.rules),
            'active_rules': len([r for r in self.rules.values() if r.status == AlertStatus.ACTIVE]),
            'triggered_rules': len([r for r in self.rules.values() if r.triggered_count > 0]),
            'total_triggers': len(self.history),
            'recent_triggers': len([h for h in self.history
                                   if datetime.fromisoformat(h.triggered_at) > datetime.now() - timedelta(days=1)]),
        }


# 便捷函数
_alert_manager = None


def get_alert_manager() -> AlertManager:
    """获取预警管理器实例（单例）"""
    global _alert_manager
    if _alert_manager is None:
        _alert_manager = AlertManager()
    return _alert_manager


def add_price_alert(ticker: str, price: float, direction: str = "above",
                   name: str = None, expires_hours: int = None) -> AlertRule:
    """
    添加价格预警

    Args:
        ticker: 标的代码
        price: 目标价格
        direction: "above" 或 "below"
        name: 预警名称
        expires_hours: 过期时间
    """
    alert_type = AlertType.PRICE_ABOVE if direction == "above" else AlertType.PRICE_BELOW
    name = name or f"价格{direction}{price}"

    return get_alert_manager().add_rule(
        ticker=ticker,
        alert_type=alert_type,
        name=name,
        condition={'threshold': price},
        expires_hours=expires_hours
    )


def add_ma_cross_alert(ticker: str, fast: str = "EMA_8", slow: str = "EMA_21",
                      cross_type: str = "golden", name: str = None) -> AlertRule:
    """添加均线交叉预警"""
    alert_type = AlertType.GOLDEN_CROSS if cross_type == "golden" else AlertType.DEAD_CROSS
    name = name or f"{fast}/{slow} {'金叉' if cross_type == 'golden' else '死叉'}"

    return get_alert_manager().add_rule(
        ticker=ticker,
        alert_type=alert_type,
        name=name,
        condition={'fast': fast, 'slow': slow}
    )


def add_bb_break_alert(ticker: str, direction: str = "upper", name: str = None) -> AlertRule:
    """添加布林带突破预警"""
    alert_type = AlertType.BB_UPPER_BREAK if direction == "upper" else AlertType.BB_LOWER_BREAK
    name = name or f"布林带{direction}突破"

    return get_alert_manager().add_rule(
        ticker=ticker,
        alert_type=alert_type,
        name=name,
        condition={}
    )


def add_kdj_alert(ticker: str, condition: str = "oversold", threshold: int = None) -> AlertRule:
    """添加KDJ预警"""
    if condition == "oversold":
        alert_type = AlertType.KDJ_OVERSOLD
        threshold = threshold or 20
        name = f"KDJ超卖(<{threshold})"
    else:
        alert_type = AlertType.KDJ_OVERBOUGHT
        threshold = threshold or 80
        name = f"KDJ超买(>{threshold})"

    return get_alert_manager().add_rule(
        ticker=ticker,
        alert_type=alert_type,
        name=name,
        condition={'threshold': threshold}
    )


def check_alerts(ticker: str, df: pd.DataFrame) -> List[AlertEvent]:
    """检查预警"""
    return get_alert_manager().check_alerts(ticker, df)


def get_active_alerts(ticker: str = None) -> List[AlertRule]:
    """获取活跃预警"""
    return get_alert_manager().get_active_rules(ticker)


def remove_alert(rule_id: str) -> bool:
    """删除预警"""
    return get_alert_manager().remove_rule(rule_id)


def get_alert_stats() -> Dict:
    """获取预警统计"""
    return get_alert_manager().get_stats()


def add_rsi_alert(ticker: str, condition: str = "oversold", threshold: int = None) -> AlertRule:
    """添加RSI预警"""
    if condition == "oversold":
        alert_type = AlertType.RSI_OVERSOLD
        threshold = threshold or 30
        name = f"RSI超卖(<{threshold})"
    else:
        alert_type = AlertType.RSI_OVERBOUGHT
        threshold = threshold or 70
        name = f"RSI超买(>{threshold})"

    return get_alert_manager().add_rule(
        ticker=ticker,
        alert_type=alert_type,
        name=name,
        condition={'threshold': threshold}
    )


def add_cci_alert(ticker: str, condition: str = "oversold") -> AlertRule:
    """添加CCI预警"""
    if condition == "oversold":
        alert_type = AlertType.CCI_OVERSOLD
        name = "CCI超卖(<-100)"
    else:
        alert_type = AlertType.CCI_OVERBOUGHT
        name = "CCI超买(>100)"

    return get_alert_manager().add_rule(
        ticker=ticker,
        alert_type=alert_type,
        name=name,
        condition={}
    )


def add_wr_alert(ticker: str, condition: str = "oversold") -> AlertRule:
    """添加WR预警"""
    if condition == "oversold":
        alert_type = AlertType.WR_OVERSOLD
        name = "WR超卖(<-80)"
    else:
        alert_type = AlertType.WR_OVERBOUGHT
        name = "WR超买(>-20)"

    return get_alert_manager().add_rule(
        ticker=ticker,
        alert_type=alert_type,
        name=name,
        condition={}
    )


def add_dmi_cross_alert(ticker: str, cross_type: str = "golden", name: str = None) -> AlertRule:
    """添加DMI交叉预警"""
    name = name or f"DMI{'金叉' if cross_type == 'golden' else '死叉'}"

    return get_alert_manager().add_rule(
        ticker=ticker,
        alert_type=AlertType.DMI_CROSS,
        name=name,
        condition={'cross_type': cross_type}
    )


def add_volume_spike_alert(ticker: str, multiplier: float = 2.0) -> AlertRule:
    """添加成交量异常预警"""
    return get_alert_manager().add_rule(
        ticker=ticker,
        alert_type=AlertType.VOLUME_SPIKE,
        name=f"成交量放大({multiplier}倍)",
        condition={'multiplier': multiplier}
    )
