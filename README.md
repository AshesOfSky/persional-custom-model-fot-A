# Market Strategy Terminal · 行情与策略分析

从个人研究系统拆出的独立行情终端。仅包含行情截图对应的页面和必要计算依赖，初始自选为空，默认展示平安银行这一公共示例证券。

## 保留功能

- 证券搜索，自选增删、分组、置顶、排序及按需报价。
- 原生报价、时间与数据来源说明，以及手动“拉取最新”。
- 多周期 K 线，EMA 内外隧道、BOLL、VWAP、VOL、MACD、KDJ。
- 已完成 K 线买卖信号、支撑压力，以及临时日 K 上的预买/预卖置信度。
- 斐波那契、江恩、艾略特波浪、缠论技术叠加。
- 右侧报价、分析、基本面面板；行情分析页 PDF 导出。

置信度沿用原信号证据分数，`0–100` 分不代表交易获利概率。临时日 K 与正式完成日 K 分开，源时间不明或报价未通过原有质量检查时不生成可用的盘中预估。

后台监控及其入口、交易、选股扫描、实验、组合、回测、预警、复盘、板块蒸馏和参数管理页面不在本仓库范围内。`monitor_snapshot_*` 等底层文件只作为手动报价刷新所需的缓存、并发和配额依赖保留，不启动后台任务或发送通知。

## 模块分工

| 路径 | 职责 |
| --- | --- |
| `apps/web/src/pages/market/` | 行情页、图表、指标开关、报价与分析面板 |
| `apps/web/src/components/layout/` | 搜索与空白起步的自选管理 |
| `apps/web/src/components/export/` | 仅行情分析页的 PDF 导出 |
| `apps/api/routes/` | 行情、分析、基本面、证券、自选 API |
| `packages/custom_model/application/` | 数据质量、来源约束、分析与实时预估服务 |
| `packages/custom_model/infrastructure/providers/` | 行情和财务数据适配器 |
| `packages/custom_model/structures/` | 技术结构计算与共享映射 |
| `modules/` | 原指标、信号置信度、支撑压力、形态及风险计算 |
| `calendar/cn-cash/` | 从交易所公开公告重新取得的 2026 交易日历与原始公告 |
| `tests/` | 使用合成输入与临时数据库的回归测试 |

## 本地运行

需要 Python 3.11+、Node.js 22.12+。已在 Windows / Python 3.14 / Node.js 24 验证。仅绑定本机回环地址。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python run.py
```

另开终端：

```powershell
cd apps/web
npm ci
npm run dev
```

访问 [本机终端](http://127.0.0.1:7100)，API 文档在 [本机 API](http://127.0.0.1:8010/docs)。

若原系统已占用默认端口，使用 `python run.py --port 14297`，前端启动前设置 `$env:CUSTOM_MODEL_SHARED_API_PORT='14297'`，然后 `npm run dev -- --port 14298 --strictPort`。

## 数据配置

`.env` 中的 `HITHINK_FINANCE_API_KEY` 为可选项，需要使用者自行提供有权限的 HiThink/AICubes 密钥；也支持 `AICUBES_API_KEY` 环境变量。Windows 上保留读取当前用户同名环境变量的原适配逻辑。仓库不提供任何密钥或授权凭据。

有密钥时优先使用对应日线、基本面与搜索接口；无密钥时使用 BaoStock、Eastmoney 等公开适配器。手动“拉取最新”保留原 AICubes 快照刷新路径，无密钥时明确返回不可用，常规原生报价仍可独立请求。上游网络、权限、时间周期和证券覆盖范围会影响可用性，失败时显示原因，不制造 K 线或估值字段。

附带日历只覆盖 2026 年。可以执行 `python scripts/capture-cn-calendar.py` 从公开的交易所年度公告重新捕获 2026 日历；该脚本不能自动生成下一年度日历。

运行后，自选、报价缓存和刷新状态写入本仓库的 `runtime/`。它们与 `.env`、截图、PDF、日志、数据库、构建目录一样已被 Git 排除。原个人持仓、自选名单、交易记录、本机路径和原项目历史没有复制到本版本文件中。

## 验证

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q
cd apps/web
npm run lint
npm run build
```

真实行情浏览器冒烟检查需先启动前后端，安装 `npx playwright install chromium`，然后在仓库根目录运行 `node scripts/smoke-browser.cjs`。也可设置 `PLAYWRIGHT_CHANNEL=msedge` 使用已安装的 Edge。`TERMINAL_URL` 可指定测试地址。输出写入 Git 忽略的 `artifacts/`，此检查会读取公开示例证券的实时行情。

版本拆分与隐私检查记录见 [交付验证记录](docs/VALIDATION.md)。
