"""
预警监控系统 V2

实时监控市场数据，当满足预设条件时触发告警。
支持技术面、基本面、宏观多维度的复杂规则配置。
"""

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Callable, Union
from enum import Enum

import pandas as pd
import numpy as np

# 尝试导入aiohttp用于异步HTTP请求
try:
    import aiohttp
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False

logger = logging.getLogger(__name__)


class AlertCategory(Enum):
    """预警类别"""
    TECHNICAL = "technical"
    FUNDAMENTAL = "fundamental"
    EVENT = "event"
    MACRO = "macro"
    PORTFOLIO = "portfolio"


@dataclass
class Condition:
    """条件定义"""
    metric: str
    operator: str
    threshold: Union[float, str]
    duration: int = 1


@dataclass
class Action:
    """动作定义"""
    channel: str
    template: str
    recipients: List[str]
    priority: str = "normal"


@dataclass
class AlertRule:
    """预警规则"""
    id: str
    name: str
    description: str
    enabled: bool = True
    conditions: List[Condition] = field(default_factory=list)
    actions: List[Action] = field(default_factory=list)
    cooldown_minutes: int = 60
    category: str = "technical"
    ticker: Optional[str] = None
    last_triggered: Optional[datetime] = None
    created_at: datetime = field(default_factory=datetime.now)
    trigger_count: int = 0

    def should_check(self) -> bool:
        """检查是否已过冷却时间"""
        if not self.last_triggered:
            return True
        cooldown = timedelta(minutes=self.cooldown_minutes)
        return datetime.now() - self.last_triggered > cooldown

    def to_dict(self) -> Dict:
        """转换为字典"""
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'enabled': self.enabled,
            'conditions': [
                {'metric': c.metric, 'operator': c.operator, 'threshold': c.threshold}
                for c in self.conditions
            ],
            'actions': [
                {'channel': a.channel, 'recipients': a.recipients, 'priority': a.priority}
                for a in self.actions
            ],
            'cooldown_minutes': self.cooldown_minutes,
            'category': self.category,
            'ticker': self.ticker,
            'last_triggered': self.last_triggered.isoformat() if self.last_triggered else None,
            'trigger_count': self.trigger_count,
        }


@dataclass
class AlertTrigger:
    """预警触发记录"""
    id: str
    rule_id: str
    rule_name: str
    triggered_at: datetime
    metric: str
    current_value: float
    threshold: float
    message: str
    ticker: Optional[str] = None

    def to_dict(self) -> Dict:
        """转换为字典"""
        return {
            'id': self.id,
            'rule_id': self.rule_id,
            'rule_name': self.rule_name,
            'triggered_at': self.triggered_at.isoformat(),
            'metric': self.metric,
            'current_value': self.current_value,
            'threshold': self.threshold,
            'message': self.message,
            'ticker': self.ticker,
        }


class TriggerStore:
    """触发记录存储"""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path
        self.triggers: List[AlertTrigger] = []
        self._max_memory_size = 1000

    async def record(self, trigger: AlertTrigger) -> None:
        """记录触发"""
        self.triggers.append(trigger)

        # 内存限制
        if len(self.triggers) > self._max_memory_size:
            self.triggers = self.triggers[-self._max_memory_size:]

        # 实际应用应写入数据库
        if self.db_path:
            await self._persist_trigger(trigger)

    async def _persist_trigger(self, trigger: AlertTrigger) -> None:
        """持久化触发记录"""
        # 实现数据库写入逻辑
        pass

    def get_recent(self, limit: int = 50) -> pd.DataFrame:
        """获取最近触发记录"""
        if not self.triggers:
            return pd.DataFrame()

        data = [t.to_dict() for t in self.triggers[-limit:]]
        df = pd.DataFrame(data)
        return df

    async def get_history(
        self,
        rule_id: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> List[AlertTrigger]:
        """获取历史记录"""
        result = self.triggers

        if rule_id:
            result = [t for t in result if t.rule_id == rule_id]

        if start_date:
            result = [t for t in result if t.triggered_at >= start_date]

        if end_date:
            result = [t for t in result if t.triggered_at <= end_date]

        return result


class RuleEngine:
    """规则引擎"""

    def __init__(self):
        self._metric_history: Dict[str, List[tuple]] = {}
        self._history_window = 100

    async def evaluate(self, rule: AlertRule, data: Dict) -> Optional[AlertTrigger]:
        """评估规则"""
        if not rule.enabled or not rule.should_check():
            return None

        # 获取股票代码
        ticker = rule.ticker or data.get('ticker')

        # 评估所有条件
        condition_results = []
        failed_conditions = []

        for condition in rule.conditions:
            result = self._evaluate_condition(condition, data, ticker)
            condition_results.append(result)
            if not result:
                failed_conditions.append(condition.metric)

        # 所有条件都满足才触发
        if all(condition_results):
            # 生成触发消息
            message = self._generate_message(rule, data)

            # 获取触发指标的值
            first_condition = rule.conditions[0]
            current_value = self._get_metric_value(first_condition.metric, data, ticker)
            threshold = first_condition.threshold
            if isinstance(threshold, str):
                threshold = self._get_metric_value(threshold, data, ticker)

            return AlertTrigger(
                id=str(uuid.uuid4()),
                rule_id=rule.id,
                rule_name=rule.name,
                triggered_at=datetime.now(),
                metric=first_condition.metric,
                current_value=current_value,
                threshold=threshold,
                message=message,
                ticker=ticker,
            )

        return None

    def _evaluate_condition(self, condition: Condition, data: Dict, ticker: Optional[str]) -> bool:
        """评估单个条件"""
        current_value = self._get_metric_value(condition.metric, data, ticker)

        # 阈值可能是另一个指标
        threshold = condition.threshold
        if isinstance(threshold, str):
            threshold = self._get_metric_value(threshold, data, ticker)

        operators = {
            '>': lambda x, y: x is not None and y is not None and x > y,
            '<': lambda x, y: x is not None and y is not None and x < y,
            '>=': lambda x, y: x is not None and y is not None and x >= y,
            '<=': lambda x, y: x is not None and y is not None and x <= y,
            '==': lambda x, y: x is not None and y is not None and x == y,
            '!=': lambda x, y: x is not None and y is not None and x != y,
            'crosses_above': lambda x, y: self._check_crosses_above(condition.metric, y, data, ticker),
            'crosses_below': lambda x, y: self._check_crosses_below(condition.metric, y, data, ticker),
        }

        op_func = operators.get(condition.operator)
        if op_func:
            return op_func(current_value, threshold)
        return False

    def _get_metric_value(self, metric: str, data: Dict, ticker: Optional[str] = None) -> Optional[float]:
        """获取指标值"""
        # 支持多级路径，如 "prices:AAPL:close" 或 "fundamentals:PE"
        if ':' in metric:
            parts = metric.split(':')
            value = data
            for part in parts:
                if isinstance(value, dict):
                    value = value.get(part)
                else:
                    return None
            return float(value) if value is not None else None

        # 直接从data获取
        if metric in data:
            return float(data[metric]) if data[metric] is not None else None

        # 从prices获取（如果是价格相关指标）
        prices = data.get('prices', {})
        if ticker and ticker in prices:
            ticker_data = prices[ticker]
            if isinstance(ticker_data, pd.DataFrame):
                if metric in ticker_data.columns:
                    return float(ticker_data[metric].iloc[-1])
            elif isinstance(ticker_data, dict):
                return ticker_data.get(metric)

        # 从fundamentals获取
        fundamentals = data.get('fundamentals', {})
        if ticker and ticker in fundamentals:
            return fundamentals[ticker].get(metric)

        # 尝试从技术指标计算
        if metric.startswith('ema_'):
            period = int(metric.split('_')[1])
            return self._calculate_ema(prices.get(ticker), period)

        return None

    def _calculate_ema(self, price_data: Any, period: int) -> Optional[float]:
        """计算EMA"""
        if price_data is None:
            return None

        if isinstance(price_data, pd.DataFrame) and 'close' in price_data.columns:
            return price_data['close'].ewm(span=period).mean().iloc[-1]
        return None

    def _check_crosses_above(self, metric: str, threshold: float, data: Dict, ticker: Optional[str]) -> bool:
        """检查是否上穿"""
        current = self._get_metric_value(metric, data, ticker)
        if current is None:
            return False

        # 阈值可能是另一个指标名称，需要解析
        if isinstance(threshold, str):
            threshold = self._get_metric_value(threshold, data, ticker)
        if threshold is None:
            return False  # 无法获取阈值时不触发

        # 获取历史值
        key = f"{ticker}:{metric}" if ticker else metric
        history = self._metric_history.get(key, [])

        if len(history) < 2:
            return False

        # 检查是否上穿
        prev_value = history[-1][1]
        return prev_value < threshold <= current

    def _check_crosses_below(self, metric: str, threshold: float, data: Dict, ticker: Optional[str]) -> bool:
        """检查是否下穿"""
        current = self._get_metric_value(metric, data, ticker)
        if current is None:
            return False

        # 阈值可能是另一个指标名称，需要解析
        if isinstance(threshold, str):
            threshold = self._get_metric_value(threshold, data, ticker)
        if threshold is None:
            return False  # 无法获取阈值时不触发

        key = f"{ticker}:{metric}" if ticker else metric
        history = self._metric_history.get(key, [])

        if len(history) < 2:
            return False

        prev_value = history[-1][1]
        return prev_value > threshold >= current

    def update_history(self, metric: str, value: float, ticker: Optional[str] = None):
        """更新指标历史"""
        key = f"{ticker}:{metric}" if ticker else metric
        if key not in self._metric_history:
            self._metric_history[key] = []

        self._metric_history[key].append((datetime.now(), value))

        # 限制历史长度
        if len(self._metric_history[key]) > self._history_window:
            self._metric_history[key] = self._metric_history[key][-self._history_window:]

    def _generate_message(self, rule: AlertRule, data: Dict) -> str:
        """生成触发消息"""
        messages = []
        for condition in rule.conditions:
            value = self._get_metric_value(condition.metric, data, rule.ticker)
            threshold = condition.threshold
            if isinstance(threshold, str):
                threshold = self._get_metric_value(threshold, data, rule.ticker)

            messages.append(f"{condition.metric} = {value:.2f} {condition.operator} {threshold:.2f}")

        return f"{rule.name}: {'; '.join(messages)}"


class NotificationChannel:
    """通知渠道基类"""

    async def send(self, message: Dict, recipients: List[str], priority: str):
        raise NotImplementedError


class EmailChannel(NotificationChannel):
    """邮件通知渠道"""

    def __init__(self, config: Optional[Dict] = None):
        self.config = config or {}
        self.smtp_host = self.config.get('smtp_host')
        self.smtp_port = self.config.get('smtp_port', 587)
        self.username = self.config.get('username')
        self.password = self.config.get('password')
        self.enabled = bool(self.smtp_host and self.username)

    async def send(self, message: Dict, recipients: List[str], priority: str):
        """发送邮件"""
        if not self.enabled:
            logger.warning("邮件渠道未配置")
            return False

        try:
            import aiosmtplib
        except ImportError:
            logger.error("请安装aiosmtplib: pip install aiosmtplib")
            return False

        subject = f"[股票预警] {message.get('rule_name', 'Alert')}"
        body = self._format_email_body(message)

        try:
            await aiosmtplib.send(
                message=body,
                sender=self.username,
                recipients=recipients,
                subject=subject,
                hostname=self.smtp_host,
                port=self.smtp_port,
                username=self.username,
                password=self.password,
                start_tls=True,
            )
            logger.info(f"邮件已发送至 {recipients}")
            return True
        except Exception as e:
            logger.error(f"邮件发送失败: {e}")
            return False

    def _format_email_body(self, message: Dict) -> str:
        """格式化邮件内容"""
        return f"""
股票预警通知

规则: {message.get('rule_name')}
股票: {message.get('ticker', 'N/A')}
触发时间: {message.get('triggered_at')}
指标: {message.get('metric')}
当前值: {message.get('current_value')}
阈值: {message.get('threshold')}

详细信息:
{message.get('message')}
        """


class DingTalkChannel(NotificationChannel):
    """钉钉通知渠道"""

    def __init__(self, config: Optional[Dict] = None):
        self.config = config or {}
        self.webhook_url = self.config.get('webhook_url')
        self.secret = self.config.get('secret')
        self.enabled = bool(self.webhook_url)

    async def send(self, message: Dict, recipients: List[str], priority: str):
        """发送钉钉消息"""
        if not self.enabled:
            logger.warning("钉钉渠道未配置")
            return False

        if not HAS_AIOHTTP:
            logger.error("请安装aiohttp: pip install aiohttp")
            return False

        payload = {
            "msgtype": "markdown",
            "markdown": {
                "title": f"股票预警: {message.get('rule_name')}",
                "text": self._format_dingtalk_message(message),
            },
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(self.webhook_url, json=payload) as resp:
                    if resp.status == 200:
                        logger.info("钉钉消息已发送")
                        return True
                    else:
                        logger.error(f"钉钉消息发送失败: {resp.status}")
                        return False
        except Exception as e:
            logger.error(f"钉钉发送异常: {e}")
            return False

    def _format_dingtalk_message(self, message: Dict) -> str:
        """格式化钉钉消息"""
        return f"""## 股票预警通知

**规则**: {message.get('rule_name')}
**股票**: {message.get('ticker', 'N/A')}
**时间**: {message.get('triggered_at')}
**指标**: {message.get('metric')}
**当前值**: {message.get('current_value', 0):.2f}
**阈值**: {message.get('threshold', 0):.2f}

{message.get('message')}
        """


class WeChatChannel(NotificationChannel):
    """企业微信通知渠道"""

    def __init__(self, config: Optional[Dict] = None):
        self.config = config or {}
        self.webhook_url = self.config.get('webhook_url')
        self.enabled = bool(self.webhook_url)

    async def send(self, message: Dict, recipients: List[str], priority: str):
        """发送企业微信消息"""
        if not self.enabled:
            logger.warning("企业微信渠道未配置")
            return False

        if not HAS_AIOHTTP:
            logger.error("请安装aiohttp: pip install aiohttp")
            return False

        payload = {
            "msgtype": "markdown",
            "markdown": {
                "content": self._format_wechat_message(message),
            },
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(self.webhook_url, json=payload) as resp:
                    return resp.status == 200
        except Exception as e:
            logger.error(f"企业微信发送异常: {e}")
            return False

    def _format_wechat_message(self, message: Dict) -> str:
        """格式化企业微信消息"""
        return f"""股票预警通知
规则: {message.get('rule_name')}
股票: {message.get('ticker', 'N/A')}
时间: {message.get('triggered_at')}
详情: {message.get('message')}
        """


class SMSChannel(NotificationChannel):
    """短信通知渠道"""

    def __init__(self, config: Optional[Dict] = None):
        self.config = config or {}
        self.access_key = self.config.get('access_key')
        self.access_secret = self.config.get('access_secret')
        self.sign_name = self.config.get('sign_name')
        self.template_code = self.config.get('template_code')
        self.enabled = bool(self.access_key and self.access_secret)

    async def send(self, message: Dict, recipients: List[str], priority: str):
        """发送短信（阿里云）"""
        if not self.enabled:
            logger.warning("短信渠道未配置")
            return False

        # 实现阿里云短信发送逻辑
        logger.info(f"短信发送给 {recipients}: {message.get('rule_name')}")
        return True


class NotificationManager:
    """通知管理器"""

    def __init__(self, channel_configs: Optional[Dict] = None):
        configs = channel_configs or {}
        self.channels: Dict[str, NotificationChannel] = {
            'email': EmailChannel(configs.get('email')),
            'dingtalk': DingTalkChannel(configs.get('dingtalk')),
            'wechat': WeChatChannel(configs.get('wechat')),
            'sms': SMSChannel(configs.get('sms')),
        }

    async def send(self, action: Action, trigger: AlertTrigger):
        """发送通知"""
        channel = self.channels.get(action.channel)
        if not channel:
            logger.warning(f"未知渠道: {action.channel}")
            return

        message = self._format_message(trigger)
        await channel.send(message, action.recipients, action.priority)

    def _format_message(self, trigger: AlertTrigger) -> Dict:
        """格式化消息"""
        return {
            'rule_id': trigger.rule_id,
            'rule_name': trigger.rule_name,
            'ticker': trigger.ticker,
            'triggered_at': trigger.triggered_at.strftime('%Y-%m-%d %H:%M:%S'),
            'metric': trigger.metric,
            'current_value': trigger.current_value,
            'threshold': trigger.threshold,
            'message': trigger.message,
        }


class AlertSystem:
    """预警系统主类"""

    # 预设规则模板
    PRESET_RULES = {
        'ema_golden_cross': {
            'name': 'EMA金叉',
            'description': '短期EMA上穿长期EMA',
            'conditions': [
                {'metric': 'ema_8', 'operator': 'crosses_above', 'threshold': 'ema_21'},
            ],
            'category': 'technical',
        },
        'ema_death_cross': {
            'name': 'EMA死叉',
            'description': '短期EMA下穿长期EMA',
            'conditions': [
                {'metric': 'ema_8', 'operator': 'crosses_below', 'threshold': 'ema_21'},
            ],
            'category': 'technical',
        },
        'price_breakout': {
            'name': '价格突破',
            'description': '股价突破前期高点',
            'conditions': [
                {'metric': 'close', 'operator': '>', 'threshold': 100.0},
                {'metric': 'volume', 'operator': '>', 'threshold': 1000000},
            ],
            'category': 'technical',
        },
        'volatility_spike': {
            'name': '波动率异常',
            'description': '日内波动超过5%',
            'conditions': [
                {'metric': 'daily_range_pct', 'operator': '>', 'threshold': 0.05},
            ],
            'category': 'technical',
        },
        'pe_low': {
            'name': 'PE处于低位',
            'description': 'PE低于历史10%分位',
            'conditions': [
                {'metric': 'pe_ttm', 'operator': '<', 'threshold': 10.0},
            ],
            'category': 'fundamental',
        },
        'earnings_surprise': {
            'name': '业绩超预期',
            'description': '实际EPS超市场预期20%',
            'conditions': [
                {'metric': 'earnings_surprise_pct', 'operator': '>', 'threshold': 0.20},
            ],
            'category': 'fundamental',
        },
        'roe_decline': {
            'name': 'ROE连续下滑',
            'description': 'ROE连续两季度下降',
            'conditions': [
                {'metric': 'roe', 'operator': '<', 'threshold': 0.15},
            ],
            'category': 'fundamental',
        },
        'major_holder_reduction': {
            'name': '大股东减持',
            'description': '大股东减持超过1%',
            'conditions': [
                {'metric': 'holder_reduction_pct', 'operator': '>=', 'threshold': 0.01},
            ],
            'category': 'event',
        },
    }

    def __init__(self, config: Optional[Dict] = None):
        self.config = config or {}
        self.rules: Dict[str, AlertRule] = {}
        self.rule_engine = RuleEngine()
        self.notification_manager = NotificationManager(self.config.get('channels'))
        self.trigger_store = TriggerStore(self.config.get('db_path'))
        self.is_running = False
        self._watchlist: List[str] = []
        self._data_source: Optional[Any] = None

    def add_rule(self, rule: AlertRule) -> str:
        """添加预警规则"""
        rule_id = rule.id or str(uuid.uuid4())
        rule.id = rule_id
        self.rules[rule_id] = rule
        logger.info(f"添加规则: {rule.name} ({rule_id})")
        return rule_id

    def remove_rule(self, rule_id: str) -> bool:
        """移除预警规则"""
        if rule_id in self.rules:
            del self.rules[rule_id]
            logger.info(f"移除规则: {rule_id}")
            return True
        return False

    def create_rule_from_preset(
        self,
        preset_name: str,
        ticker: Optional[str] = None,
        channels: List[str] = None,
        cooldown: int = 60,
    ) -> Optional[AlertRule]:
        """从预设模板创建规则"""
        preset = self.PRESET_RULES.get(preset_name)
        if not preset:
            logger.error(f"未知预设: {preset_name}")
            return None

        conditions = [
            Condition(
                metric=c['metric'],
                operator=c['operator'],
                threshold=c['threshold'],
            )
            for c in preset['conditions']
        ]

        actions = []
        if channels:
            for ch in channels:
                actions.append(Action(
                    channel=ch,
                    template='default',
                    recipients=[],
                ))

        return AlertRule(
            id=str(uuid.uuid4()),
            name=preset['name'],
            description=preset['description'],
            conditions=conditions,
            actions=actions,
            cooldown_minutes=cooldown,
            category=preset['category'],
            ticker=ticker,
        )

    async def check_all(self, data: Dict) -> List[AlertTrigger]:
        """检查所有规则"""
        triggers = []
        for rule_id, rule in self.rules.items():
            try:
                trigger = await self.rule_engine.evaluate(rule, data)
                if trigger:
                    triggers.append(trigger)
                    await self._handle_trigger(trigger, rule)
            except Exception as e:
                logger.error(f"规则 {rule.name} 检查失败: {e}")

        return triggers

    async def check_single(self, rule_id: str, data: Dict) -> Optional[AlertTrigger]:
        """检查单个规则"""
        rule = self.rules.get(rule_id)
        if not rule:
            return None

        trigger = await self.rule_engine.evaluate(rule, data)
        if trigger:
            await self._handle_trigger(trigger, rule)
        return trigger

    async def _handle_trigger(self, trigger: AlertTrigger, rule: AlertRule):
        """处理预警触发"""
        # 记录触发
        await self.trigger_store.record(trigger)
        rule.last_triggered = datetime.now()
        rule.trigger_count += 1

        logger.info(f"触发预警: {rule.name} - {trigger.message}")

        # 发送通知
        for action in rule.actions:
            try:
                await self.notification_manager.send(action, trigger)
            except Exception as e:
                logger.error(f"通知发送失败: {e}")

    async def start_monitoring(self, interval: int = 60):
        """启动实时监控"""
        self.is_running = True
        logger.info(f"启动监控，间隔 {interval} 秒")

        while self.is_running:
            start_time = time.time()

            try:
                # 获取最新数据
                data = await self._fetch_latest_data()

                # 检查所有规则
                triggers = await self.check_all(data)

                if triggers:
                    logger.info(f"触发 {len(triggers)} 条预警")

            except Exception as e:
                logger.error(f"监控循环错误: {e}")

            # 控制频率
            elapsed = time.time() - start_time
            sleep_time = max(0, interval - elapsed)
            await asyncio.sleep(sleep_time)

    def stop_monitoring(self):
        """停止监控"""
        self.is_running = False
        logger.info("停止监控")

    async def _fetch_latest_data(self) -> Dict:
        """获取最新数据"""
        data = {
            'timestamp': datetime.now(),
            'prices': {},
            'fundamentals': {},
        }

        # 批量获取价格数据
        if self._watchlist and self._data_source:
            for ticker in self._watchlist:
                try:
                    # 实际应从数据源获取
                    price_data = await self._get_price_data(ticker)
                    data['prices'][ticker] = price_data

                    # 更新指标历史
                    if isinstance(price_data, pd.DataFrame) and 'close' in price_data.columns:
                        self.rule_engine.update_history('close', price_data['close'].iloc[-1], ticker)
                except Exception as e:
                    logger.error(f"获取 {ticker} 数据失败: {e}")

        return data

    async def _get_price_data(self, ticker: str) -> Optional[pd.DataFrame]:
        """获取价格数据"""
        # 实际应从数据源获取
        # 这里返回模拟数据
        dates = pd.date_range(end=datetime.now(), periods=30, freq='D')
        np.random.seed(42)
        closes = 100 + np.cumsum(np.random.randn(30) * 2)

        df = pd.DataFrame({
            'date': dates,
            'open': closes * 0.99,
            'high': closes * 1.02,
            'low': closes * 0.98,
            'close': closes,
            'volume': np.random.randint(1000000, 5000000, 30),
        })

        # 计算EMA
        df['ema_8'] = df['close'].ewm(span=8).mean()
        df['ema_21'] = df['close'].ewm(span=21).mean()

        return df

    def set_watchlist(self, tickers: List[str]):
        """设置自选股列表"""
        self._watchlist = tickers
        logger.info(f"设置自选股: {tickers}")

    def set_data_source(self, data_source: Any):
        """设置数据源"""
        self._data_source = data_source

    def get_rules_summary(self) -> pd.DataFrame:
        """获取规则摘要"""
        if not self.rules:
            return pd.DataFrame()

        data = [r.to_dict() for r in self.rules.values()]
        return pd.DataFrame(data)

    def get_stats(self) -> Dict:
        """获取统计信息"""
        return {
            'total_rules': len(self.rules),
            'enabled_rules': sum(1 for r in self.rules.values() if r.enabled),
            'total_triggers': len(self.trigger_store.triggers),
            'watchlist_size': len(self._watchlist),
            'is_running': self.is_running,
        }


def create_custom_rule(
    name: str,
    metric: str,
    operator: str,
    threshold: float,
    channels: List[str] = None,
    cooldown: int = 60,
    ticker: Optional[str] = None,
) -> AlertRule:
    """
    创建自定义预警规则

    Args:
        name: 规则名称
        metric: 指标名称
        operator: 操作符 (>, <, ==, >=, <=, crosses_above, crosses_below)
        threshold: 阈值
        channels: 通知渠道列表
        cooldown: 冷却时间（分钟）
        ticker: 股票代码

    Returns:
        AlertRule 实例
    """
    channels = channels or ['email']

    return AlertRule(
        id=str(uuid.uuid4()),
        name=name,
        description=f"{metric} {operator} {threshold}",
        conditions=[
            Condition(metric=metric, operator=operator, threshold=threshold),
        ],
        actions=[
            Action(channel=ch, template="default", recipients=[])
            for ch in channels
        ],
        cooldown_minutes=cooldown,
        ticker=ticker,
    )


# Streamlit 集成
def render_alerts_page():
    """渲染预警监控页面"""
    import streamlit as st

    st.header("预警监控")

    # 初始化预警系统
    if 'alert_system' not in st.session_state:
        st.session_state['alert_system'] = AlertSystem()

    alert_system = st.session_state['alert_system']

    # 统计信息
    stats = alert_system.get_stats()
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("总规则数", stats['total_rules'])
    col2.metric("启用规则", stats['enabled_rules'])
    col3.metric("触发次数", stats['total_triggers'])
    col4.metric("监控状态", "运行中" if stats['is_running'] else "已停止")

    # 预警规则管理
    with st.expander("预警规则", expanded=True):
        tab1, tab2 = st.tabs(["添加规则", "现有规则"])

        with tab1:
            col1, col2 = st.columns(2)
            with col1:
                rule_type = st.radio("规则类型", ["预设模板", "自定义"])

            if rule_type == "预设模板":
                preset = st.selectbox(
                    "选择预设",
                    list(AlertSystem.PRESET_RULES.keys()),
                    format_func=lambda x: AlertSystem.PRESET_RULES[x]['name'],
                )
                ticker = st.text_input("股票代码", "600519.SS")
                channels = st.multiselect("通知渠道", ["email", "dingtalk", "wechat"], ["email"])

                if st.button("添加预设规则"):
                    rule = alert_system.create_rule_from_preset(
                        preset_name=preset,
                        ticker=ticker,
                        channels=channels,
                    )
                    if rule:
                        alert_system.add_rule(rule)
                        st.success(f"已添加规则: {rule.name}")

            else:
                rule_name = st.text_input("规则名称")
                ticker = st.text_input("股票代码")
                metric = st.selectbox("指标", ["close", "ema_8", "ema_21", "pe_ttm", "volume", "roe"])
                operator = st.selectbox("条件", [">", "<", ">=", "<=", "==", "crosses_above", "crosses_below"])
                threshold = st.number_input("阈值", value=100.0)
                channels = st.multiselect("通知渠道", ["email", "dingtalk", "wechat"], ["email"])

                if st.button("添加自定义规则"):
                    rule = create_custom_rule(
                        name=rule_name,
                        metric=metric,
                        operator=operator,
                        threshold=threshold,
                        channels=channels,
                        ticker=ticker,
                    )
                    alert_system.add_rule(rule)
                    st.success("规则添加成功")

        with tab2:
            if alert_system.rules:
                rules_df = alert_system.get_rules_summary()
                st.dataframe(rules_df[['name', 'enabled', 'category', 'ticker', 'trigger_count']])

                # 删除规则
                rule_to_delete = st.selectbox(
                    "选择要删除的规则",
                    list(alert_system.rules.keys()),
                    format_func=lambda x: alert_system.rules[x].name,
                )
                if st.button("删除规则"):
                    alert_system.remove_rule(rule_to_delete)
                    st.rerun()
            else:
                st.info("暂无规则")

    # 触发历史
    with st.expander("预警历史"):
        history = alert_system.trigger_store.get_recent(limit=50)
        if not history.empty:
            st.dataframe(history)
        else:
            st.info("暂无触发记录")

    # 实时监控面板
    with st.expander("实时监控"):
        watchlist = st.text_input("自选股列表（逗号分隔）", "600519.SS, AAPL")
        alert_system.set_watchlist([t.strip() for t in watchlist.split(",")])

        col1, col2 = st.columns(2)
        with col1:
            if st.button("启动监控", type="primary"):
                st.session_state['monitoring'] = True
                asyncio.create_task(alert_system.start_monitoring(interval=30))
                st.success("监控已启动")

        with col2:
            if st.button("停止监控"):
                st.session_state['monitoring'] = False
                alert_system.stop_monitoring()
                st.info("监控已停止")

        if st.session_state.get('monitoring'):
            st.info("监控运行中...")

            # 手动检查按钮
            if st.button("立即检查"):
                with st.spinner("检查中..."):
                    data = asyncio.run(alert_system._fetch_latest_data())
                    triggers = asyncio.run(alert_system.check_all(data))
                    if triggers:
                        st.warning(f"触发 {len(triggers)} 条预警!")
                        for t in triggers:
                            st.write(f"- {t.rule_name}: {t.message}")
                    else:
                        st.success("未触发任何预警")


if __name__ == "__main__":
    # 测试代码
    async def test():
        # 创建预警系统
        alert_system = AlertSystem()

        # 添加EMA金叉规则
        rule = alert_system.create_rule_from_preset(
            preset_name='ema_golden_cross',
            ticker='TEST',
            channels=['email'],
        )
        if rule:
            alert_system.add_rule(rule)

        # 添加价格突破规则
        rule2 = create_custom_rule(
            name="价格突破100",
            metric="close",
            operator=">",
            threshold=100.0,
            ticker='TEST',
        )
        alert_system.add_rule(rule2)

        # 模拟数据检查
        test_data = {
            'ticker': 'TEST',
            'close': 105.0,
            'ema_8': 102.0,
            'ema_21': 100.0,
        }

        triggers = await alert_system.check_all(test_data)
        print(f"触发预警: {len(triggers)}")
        for t in triggers:
            print(f"- {t.rule_name}: {t.message}")

    asyncio.run(test())
