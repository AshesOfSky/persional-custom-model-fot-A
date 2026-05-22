# Custom-Model 股票分析系统开发路线图

> 参考 Anthropic Financial Services Plugins 架构设计
> 创建日期: 2026-03-05
> 版本: v1.0

---

## 一、现有能力盘点

| 层级 | 功能模块 | 状态 |
|------|----------|------|
| **数据接入** | 多市场OHLCV、财报公告、宏观资金流向 | ✅ 完善 |
| **技术面分析** | EMA多周期趋势、多时间框架 | ✅ 完善 |
| **基本面分析** | 财务健康度、估值指标 | ⚠️ 基础 |
| **舆情分析** | 新闻、社媒推文生成 | ✅ 完善 |
| **插件系统** | data_ingestion, technical_analysis | ⚠️ 框架初建 |

---

## 二、功能开发完整清单

### 阶段一：核心金融建模能力（优先级：高）

#### 1.1 DCF 估值模型插件
- **模块**: `plugins/fundamental/dcf_model.py`
- **功能**: WACC计算、FCF预测、终值计算、敏感性分析
- **输出**: 专业Excel模型（蓝/黑/绿配色）

#### 1.2 可比公司分析插件
- **模块**: `plugins/fundamental/comps_analysis.py`
- **功能**: 可比公司筛选、交易倍数计算、统计摘要、可视化
- **输出**: Comps表格（Excel + 图表）

#### 1.3 财务三表联动模型
- **模块**: `plugins/fundamental/three_statements.py`
- **功能**: IS/BS/CF联动、驱动假设、配平检查、信用指标
- **输出**: 三表模型 + 三种情景分析

### 阶段二：高级分析与策略（优先级：高）

#### 2.1 量化策略回测增强
- **模块**: `modules/backtest.py` 升级
- **功能**: 多因子模型、事件驱动回测、风险指标

#### 2.2 行业轮动分析
- **模块**: `modules/sector_rotation.py`
- **功能**: 行业相对强弱、资金流向热力图、美林时钟

#### 2.3 投资组合风险分析
- **模块**: `modules/portfolio_risk.py`
- **功能**: VaR计算、相关性矩阵、组合优化

### 阶段三：研报与内容生成（优先级：中）

#### 3.1 研报生成引擎
- **模块**: `modules/research_report.py`
- **功能**: 投资摘要、盈利预测、目标价推导、催化剂日历
- **输出**: Markdown / Word文档

#### 3.2 市场情绪分析增强
- **模块**: `modules/sentiment_analysis.py`
- **功能**: 情绪时间序列、社交媒体聚合、背离检测

### 阶段四：专业工作流（优先级：中）

#### 4.1 并购分析模块
- **模块**: `modules/merger_analysis.py`
- **功能**: 溢价分析、稀释/增厚、协同效应

#### 4.2 PE投资分析
- **模块**: `modules/pe_analysis.py`
- **功能**: IRR/MOIC、敏感性分析、回报归因

#### 4.3 预警监控系统升级
- **模块**: `modules/alerts.py` 升级
- **功能**: 技术面/基本面/宏观预警、多渠道推送

### 阶段五：数据基础设施（优先级：高）

#### 5.1 MCP连接器架构
- **文件**: `.mcp.json`
- **功能**: 标准化数据源接入（akshare/yfinance/tushare）

#### 5.2 统一数据缓存层
- **模块**: `modules/data_cache.py` 升级
- **功能**: 时序数据库、数据版本、增量更新

#### 5.3 数据质量监控
- **模块**: `modules/data_quality.py`
- **功能**: 完整性检查、异常检测、跨源校验

### 阶段六：用户界面与体验（优先级：中）

#### 6.1 Streamlit界面重构
- **页面**: 仪表盘、个股分析、估值建模、策略回测、设置

#### 6.2 API接口层
- **模块**: `api/` 目录
- **功能**: FastAPI架构、RESTful接口

---

## 三、技术架构演进

### 目标架构

```
┌─────────────────────────────────────────────────────┐
│                    app.py (UI层)                     │
│              Streamlit / FastAPI / CLI              │
├─────────────────────────────────────────────────────┤
│                  commands/ (指令层)                  │
│    /analyze, /valuate, /backtest, /alert, /report   │
├─────────────────────────────────────────────────────┤
│                   skills/ (技能层)                   │
│   DCF │ Comps │ 3-Statements │ Backtest │ Research  │
├─────────────────────────────────────────────────────┤
│                  plugins/ (插件层)                   │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐            │
│  │data_ingestion│ │fundamental │ │technical   │     │
│  ├──────────┤ ├──────────┤ ├──────────┤            │
│  │valuation │ │sentiment │ │risk      │             │
│  └──────────┘ └──────────┘ └──────────┘            │
├─────────────────────────────────────────────────────┤
│                 connectors/ (连接层)                 │
│  akshare │ yfinance │ tushare │ mcp_servers...      │
├─────────────────────────────────────────────────────┤
│                  storage/ (存储层)                   │
│  SQLite/PostgreSQL │ Cache │ File System            │
└─────────────────────────────────────────────────────┘
```

---

## 四、实现优先级与里程碑

### Sprint 1（2-3周）：核心估值能力
- [ ] DCF模型插件（单品种）
- [ ] 可比公司分析（A股行业）
- [ ] 三表联动框架（简化版）

### Sprint 2（2-3周）：数据架构升级
- [ ] MCP连接器规范设计
- [ ] 数据缓存层升级（支持增量）
- [ ] 数据质量监控系统

### Sprint 3（2周）：回测与策略
- [ ] 回测框架重构（多因子支持）
- [ ] EMA策略与回测联动
- [ ] 风险指标计算

### Sprint 4（2周）：研报与内容
- [ ] 研报生成引擎（Markdown）
- [ ] 投资摘要自动提取
- [ ] 与现有推文功能整合

### Sprint 5（2周）：预警与监控
- [ ] 预警规则引擎
- [ ] 多渠道推送
- [ ] 实时监控循环

### Sprint 6（持续）：专业模块
- [ ] 并购分析（事件驱动）
- [ ] PE投资回报分析
- [ ] 组合优化

---

## 五、技术选型

| 组件 | 推荐方案 |
|------|----------|
| Excel导出 | xlsxwriter + openpyxl |
| 时序数据库 | TimescaleDB (PostgreSQL扩展) |
| 缓存 | Redis / diskcache |
| API框架 | FastAPI |
| 回测 | backtrader / 自建 |
| 任务队列 | Celery + Redis |
| 监控 | prometheus + grafana |

---

## 六、详细设计文档索引

| 模块 | 文档路径 | 状态 |
|------|----------|------|
| DCF Model | `docs/design/01_dcf_model.md` | ✅ 已完成 |
| Comps Analysis | `docs/design/02_comps_analysis.md` | ✅ 已完成 |
| Three Statements | `docs/design/03_three_statements.md` | ✅ 已完成 |
| Data Cache | `docs/design/04_data_cache.md` | ✅ 已完成 |
| Backtest Engine | `docs/design/05_backtest_engine.md` | ✅ 已完成 |
| Research Report | `docs/design/06_research_report.md` | ✅ 已完成 |
| Alert System | `docs/design/07_alert_system.md` | ✅ 已完成 |
| MCP Connectors | `docs/design/08_mcp_connectors.md` | ✅ 已完成 |

---

*本文档随开发进度持续更新*
