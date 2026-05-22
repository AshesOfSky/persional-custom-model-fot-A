# 预警监控系统详细设计

> 优先级: Sprint 5 (P2)
> 模块路径: `modules/alerts_v2.py` (升级现有 `alerts.py`)

---

## 1. 功能概述

预警监控系统实时监控市场数据，当满足预设条件时触发告警。升级后支持技术面、基本面、宏观多维度的复杂规则配置。

### 1.1 核心能力
- 技术面预警（EMA交叉、价格突破、波动率异常）
- 基本面预警（财报超预期/低于预期、评级变动）
- 宏观预警（利率变动、汇率突破、商品价格）
- 多渠道推送（邮件、钉钉/企业微信、短信）
- 实时监控循环（后台任务）

### 1.2 预警类型

| 预警类别 | 触发条件示例 |
|----------|-------------|
| 技术面 | EMA金叉/死叉、价格突破支撑/阻力、成交量异动 |
| 基本面 | PE低于历史10%分位、ROE连续两季下滑 |
| 事件 | 财报发布、股权质押风险、大股东减持 |
| 宏观 | 10年期国债收益率突破阈值、汇率波动>2% |
| 组合 | 个股仓位超过阈值、组合回撤超过5% |

---

## 2. 架构设计

### 2.1 系统架构

```
┌─────────────────────────────────────────────────────────────────┐
│                      数据源层                                    │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────┐    │
│  │ 行情数据 │  │ 财务数据 │  │ 新闻公告 │  │ 宏观数据     │    │
│  │ (实时)   │  │ (定期)   │  │ (事件)   │  │ (API)        │    │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └──────┬───────┘    │
└───────┼─────────────┼─────────────┼───────────────┼────────────┘
        │             │             │               │
        └─────────────┴─────────────┴───────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│                    规则引擎层 (Rule Engine)                       │
│  ┌─────────────────┐  ┌───────────────┐  ┌───────────────────┐  │
│  │ AlertRule       │  │ Condition     │  │ Action            │  │
│  │ - name          │  │ - type        │  │ - channel         │  │
│  │ - conditions    │  │ - operator    │  │ - template        │  │
│  │ - actions       │  │ - threshold   │  │ - retry           │  │
│  │ - cooldown      │  │ - duration    │  │                   │  │
│  └────────┬────────┘  └───────┬───────┘  └─────────┬─────────┘  │
│           │                   │                    │            │
│           └───────────────────┴────────────────────┘            │
│                               │                                 │
│                    ┌──────────┴──────────┐                      │
│                    │   RuleEvaluator     │                      │
│                    │   (规则求值器)       │                      │
│                    └──────────┬──────────┘                      │
└───────────────────────────────┼─────────────────────────────────┘
                                │
                ┌───────────────┼───────────────┐
                │               │               │
                ▼               ▼               ▼
┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
│  触发记录存储    │ │  通知队列       │ │  监控面板       │
│  (SQLite/Redis) │ │  (Redis/Rabbit) │ │  (Streamlit)    │
└─────────────────┘ └────────┬────────┘ └─────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                    通知渠道层                                    │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────┐    │
│  │ 邮件     │  │ 钉钉     │  │ 企业微信 │  │ 短信         │    │
│  │ (SMTP)   │  │ (Webhook)│  │ (Webhook)│  │ (阿里云)     │    │
│  └──────────┘  └──────────┘  └──────────┘  └──────────────┘    │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 类图

```python
class AlertSystem:
    """预警系统主类"""

    def __init__(self, config: AlertConfig):
        self.rules: Dict[str, AlertRule] = {}
        self.rule_engine = RuleEngine()
        self.notification_manager = NotificationManager(config.channels)
        self.trigger_store = TriggerStore()
        self.is_running = False

    def add_rule(self, rule: AlertRule) -> str:
        """添加预警规则"""
        rule_id = str(uuid.uuid4())
        self.rules[rule_id] = rule
        return rule_id

    def remove_rule(self, rule_id: str) -> bool:
        """移除预警规则"""
        if rule_id in self.rules:
            del self.rules[rule_id]
            return True
        return False

    async def check_all(self, data: Dict) -> List[AlertTrigger]:
        """检查所有规则"""
        triggers = []
        for rule_id, rule in self.rules.items():
            if await self._should_check(rule):
                trigger = await self.rule_engine.evaluate(rule, data)
                if trigger:
                    triggers.append(trigger)
                    await self._handle_trigger(trigger, rule)
        return triggers

    async def _handle_trigger(self, trigger: AlertTrigger, rule: AlertRule):
        """处理预警触发"""
        # 记录触发
        await self.trigger_store.record(trigger)

        # 发送通知
        for action in rule.actions:
            await self.notification_manager.send(action, trigger)

        # 更新最后触发时间
        rule.last_triggered = datetime.now()

    async def start_monitoring(self, interval: int = 60):
        """启动实时监控"""
        self.is_running = True
        while self.is_running:
            try:
                # 获取最新数据
                data = await self._fetch_latest_data()

                # 检查规则
                triggers = await self.check_all(data)

                if triggers:
                    logger.info(f"触发 {len(triggers)} 条预警")

            except Exception as e:
                logger.error(f"监控循环错误: {e}")

            await asyncio.sleep(interval)

    def stop_monitoring(self):
        """停止监控"""
        self.is_running = False


@dataclass
class AlertRule:
    """预警规则"""
    id: str
    name: str
    description: str
    enabled: bool = True
    conditions: List[Condition] = field(default_factory=list)
    actions: List[Action] = field(default_factory=list)
    cooldown_minutes: int = 60  # 冷却时间
    last_triggered: Optional[datetime] = None
    created_at: datetime = field(default_factory=datetime.now)

    def should_check(self) -> bool:
        """检查是否已过冷却时间"""
        if not self.last_triggered:
            return True
        cooldown = timedelta(minutes=self.cooldown_minutes)
        return datetime.now() - self.last_triggered > cooldown


@dataclass
class Condition:
    """条件定义"""
    metric: str           # 指标名称
    operator: str         # >, <, ==, >=, <=, crosses_above, crosses_below
    threshold: float      # 阈值
    duration: int = 1     # 持续时间（分钟）


@dataclass
class Action:
    """动作定义"""
    channel: str          # email / dingtalk / wechat / sms
    template: str         # 消息模板
    recipients: List[str] # 接收人列表
    priority: str = "normal"  # low / normal / high / urgent


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


class RuleEngine:
    """规则引擎"""

    async def evaluate(self, rule: AlertRule, data: Dict) -> Optional[AlertTrigger]:
        """评估规则"""
        if not rule.enabled or not rule.should_check():
            return None

        # 评估所有条件
        condition_results = []
        for condition in rule.conditions:
            result = self._evaluate_condition(condition, data)
            condition_results.append(result)

        # 所有条件都满足才触发
        if all(condition_results):
            return AlertTrigger(
                id=str(uuid.uuid4()),
                rule_id=rule.id,
                rule_name=rule.name,
                triggered_at=datetime.now(),
                metric=rule.conditions[0].metric,
                current_value=self._get_metric_value(rule.conditions[0].metric, data),
                threshold=rule.conditions[0].threshold,
                message=self._generate_message(rule, data),
            )

        return None

    def _evaluate_condition(self, condition: Condition, data: Dict) -> bool:
        """评估单个条件"""
        current_value = self._get_metric_value(condition.metric, data)

        operators = {
            '>': lambda x, y: x > y,
            '<': lambda x, y: x < y,
            '>=': lambda x, y: x >= y,
            '<=': lambda x, y: x <= y,
            '==': lambda x, y: x == y,
            'crosses_above': lambda x, y: self._check_crosses_above(condition.metric, y, data),
            'crosses_below': lambda x, y: self._check_crosses_below(condition.metric, y, data),
        }

        op_func = operators.get(condition.operator)
        if op_func:
            return op_func(current_value, condition.threshold)
        return False


class NotificationManager:
    """通知管理器"""

    def __init__(self, channel_configs: Dict[str, Dict]):
        self.channels: Dict[str, NotificationChannel] = {
            'email': EmailChannel(channel_configs.get('email')),
            'dingtalk': DingTalkChannel(channel_configs.get('dingtalk')),
            'wechat': WeChatChannel(channel_configs.get('wechat')),
            'sms': SMSChannel(channel_configs.get('sms')),
        }

    async def send(self, action: Action, trigger: AlertTrigger):
        """发送通知"""
        channel = self.channels.get(action.channel)
        if channel:
            message = self._format_message(action.template, trigger)
            await channel.send(message, action.recipients, action.priority)


class EmailChannel:
    """邮件通知渠道"""

    def __init__(self, config: Dict):
        self.smtp_host = config.get('smtp_host')
        self.smtp_port = config.get('smtp_port')
        self.username = config.get('username')
        self.password = config.get('password')

    async def send(self, message: str, recipients: List[str], priority: str):
        """发送邮件"""
        subject = f"[股票预警] {message['rule_name']}"
        body = self._format_email_body(message)

        # 使用aiosmtp发送
        await self._send_email(recipients, subject, body)


class DingTalkChannel:
    """钉钉通知渠道"""

    def __init__(self, config: Dict):
        self.webhook_url = config.get('webhook_url')
        self.secret = config.get('secret')

    async def send(self, message: Dict, recipients: List[str], priority: str):
        """发送钉钉消息"""
        payload = {
            "msgtype": "markdown",
            "markdown": {
                "title": f"股票预警: {message['rule_name']}",
                "text": self._format_dingtalk_message(message),
            },
            "at": {
                "atUserIds": recipients,
            }
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(self.webhook_url, json=payload) as resp:
                return resp.status == 200
```

---

## 3. 预警规则定义

### 3.1 预设规则模板

```python
# 预设规则模板库
PRESET_RULES = {
    # 技术面规则
    'ema_golden_cross': {
        'name': 'EMA金叉',
        'description': '短期EMA上穿长期EMA',
        'conditions': [
            {'metric': 'ema_8', 'operator': 'crosses_above', 'threshold': 'ema_21'},
        ],
        'category': 'technical',
    },

    'price_breakout': {
        'name': '价格突破',
        'description': '股价突破前期高点',
        'conditions': [
            {'metric': 'close', 'operator': '>', 'threshold': 'resistance_level'},
            {'metric': 'volume', 'operator': '>', 'threshold': 'volume_ma_20'},
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

    # 基本面规则
    'pe_low': {
        'name': 'PE处于低位',
        'description': 'PE低于历史10%分位',
        'conditions': [
            {'metric': 'pe_ttm', 'operator': '<', 'threshold': 'pe_10th_percentile'},
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

    # 事件规则
    'earnings_announcement': {
        'name': '财报发布提醒',
        'description': '财报发布前1天提醒',
        'conditions': [
            {'metric': 'days_to_earnings', 'operator': '==', 'threshold': 1},
        ],
        'category': 'event',
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
```

### 3.2 自定义规则创建

```python
def create_custom_rule(
    name: str,
    metric: str,
    operator: str,
    threshold: float,
    channels: List[str],
    cooldown: int = 60,
) -> AlertRule:
    """
    创建自定义预警规则

    示例:
    rule = create_custom_rule(
        name="股价突破100元",
        metric="close",
        operator=">",
        threshold=100.0,
        channels=["email", "dingtalk"],
        cooldown=120,
    )
    """
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
    )
```

---

## 4. 实时监控实现

### 4.1 监控循环

```python
async def monitor_loop(self):
    """实时监控主循环"""
    while self.is_running:
        start_time = time.time()

        try:
            # 1. 获取自选股列表
            watchlist = self.get_watchlist()

            # 2. 批量获取行情数据
            price_data = await self.data_cache.batch_get_prices(watchlist)

            # 3. 获取基本面数据（每小时一次）
            if self._should_refresh_fundamental():
                fundamental_data = await self.data_cache.batch_get_fundamentals(watchlist)
            else:
                fundamental_data = self._cached_fundamental_data

            # 4. 合并数据
            data = {
                'prices': price_data,
                'fundamentals': fundamental_data,
                'timestamp': datetime.now(),
            }

            # 5. 检查所有规则
            triggers = await self.check_all(data)

            # 6. 更新监控面板
            await self.update_dashboard(triggers)

        except Exception as e:
            logger.error(f"监控循环异常: {e}")
            await self.send_admin_alert(f"监控循环异常: {str(e)}")

        # 控制频率（默认30秒）
        elapsed = time.time() - start_time
        sleep_time = max(0, 30 - elapsed)
        await asyncio.sleep(sleep_time)
```

### 4.2 批量数据获取优化

```python
async def batch_get_prices(self, tickers: List[str]) -> Dict[str, pd.DataFrame]:
    """
    批量获取价格数据（优化版）

    策略:
    1. 优先从缓存获取
    2. 缓存未命中的批量请求
    3. 更新缓存
    """
    results = {}
    missing = []

    # 批量查询缓存
    for ticker in tickers:
        cached = await self.l1_cache.get(f"price:{ticker}")
        if cached and not cached.is_expired():
            results[ticker] = cached.data
        else:
            missing.append(ticker)

    # 批量获取缺失数据
    if missing:
        batch_data = await self.data_source.batch_fetch(missing)

        # 更新缓存
        for ticker, data in batch_data.items():
            await self.l1_cache.set(f"price:{ticker}", data, ttl=30)
            results[ticker] = data

    return results
```

---

## 5. 接口定义

### 5.1 Python API

```python
from modules.alerts_v2 import AlertSystem, AlertRule, Condition, Action

# 初始化预警系统
alert_system = AlertSystem(config={
    'channels': {
        'email': {'smtp_host': 'smtp.gmail.com', ...},
        'dingtalk': {'webhook_url': 'https://oapi.dingtalk.com/...'},
    }
})

# 添加EMA金叉预警
rule = AlertRule(
    name="600519 EMA金叉",
    conditions=[
        Condition(metric="ema_8", operator="crosses_above", threshold="ema_21"),
    ],
    actions=[
        Action(channel="dingtalk", template="ema_cross", recipients=["user123"]),
    ],
    cooldown_minutes=120,
)
rule_id = alert_system.add_rule(rule)

# 添加价格突破预警
rule2 = AlertRule(
    name="茅台突破2000元",
    conditions=[
        Condition(metric="close", operator=">", threshold=2000.0),
    ],
    actions=[
        Action(channel="email", template="price_alert", recipients=["user@example.com"]),
    ],
)
alert_system.add_rule(rule2)

# 启动监控（后台任务）
asyncio.create_task(alert_system.start_monitoring(interval=60))

# 手动检查
triggers = await alert_system.check_all(current_data)
for trigger in triggers:
    print(f"触发: {trigger.rule_name} - {trigger.message}")

# 停止监控
alert_system.stop_monitoring()

# 查看触发历史
history = await alert_system.trigger_store.get_history(
    rule_id=rule_id,
    start_date=datetime.now() - timedelta(days=7),
)
```

### 5.2 Streamlit集成

```python
def render_alerts_page():
    st.header("预警监控")

    # 预警规则管理
    with st.expander("预警规则"):
        # 添加新规则
        col1, col2 = st.columns(2)
        with col1:
            rule_name = st.text_input("规则名称")
            ticker = st.text_input("股票代码")
        with col2:
            metric = st.selectbox("指标", ["close", "ema_8", "ema_21", "pe_ttm", "volume"])
            operator = st.selectbox("条件", [">", "<", ">=", "<=", "crosses_above", "crosses_below"])
            threshold = st.number_input("阈值")

        if st.button("添加规则"):
            rule = create_custom_rule(rule_name, metric, operator, threshold, ["email"])
            alert_system.add_rule(rule)
            st.success("规则添加成功")

        # 显示现有规则
        st.subheader("现有规则")
        for rule_id, rule in alert_system.rules.items():
            col1, col2 = st.columns([3, 1])
            col1.write(f"{rule.name} - {'启用' if rule.enabled else '禁用'}")
            if col2.button("删除", key=f"del_{rule_id}"):
                alert_system.remove_rule(rule_id)
                st.experimental_rerun()

    # 触发历史
    st.subheader("预警历史")
    history = alert_system.trigger_store.get_recent(limit=50)
    st.dataframe(history)

    # 实时监控面板
    st.subheader("实时监控")
    if st.button("启动监控"):
        st.session_state['monitoring'] = True
        asyncio.create_task(alert_system.start_monitoring())

    if st.button("停止监控"):
        st.session_state['monitoring'] = False
        alert_system.stop_monitoring()

    if st.session_state.get('monitoring'):
        st.info("监控运行中...")
        # 实时显示触发记录
        placeholder = st.empty()
        while st.session_state['monitoring']:
            recent = alert_system.trigger_store.get_recent(limit=5)
            placeholder.dataframe(recent)
            time.sleep(5)
```

---

*文档版本: v1.0*
*创建日期: 2026-03-05*
