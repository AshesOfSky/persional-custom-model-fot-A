"""
social_content.py — 专业金融分析内容生成器
将技术分析指标转换为专业分析师风格的推文
参考结构：核心问题→底层逻辑→关键位→跟踪要点→风险提示
"""

from typing import Dict, List, Optional
from datetime import datetime
import random


class ProfessionalAnalysisGenerator:
    """专业金融分析推文生成器"""

    # 专业术语库
    TECHNICAL_TERMS = {
        "fibonacci": ["斐波那契", "黄金分割", "0.618位", "0.382位", "0.5位"],
        "wave": ["C浪", "调整浪", "主趋势", "次级趋势", "时间换空间"],
        "structure": ["结构位", "关键位", "分水岭", "多空分界", "趋势转折点"],
        "momentum": ["动量", "量能", "资金流入", "资金出逃", "筹码分布"],
        "pattern": ["头肩顶", "双底", "旗形整理", "三角形收敛", "箱体震荡"],
    }

    # 标题模板 — 突出关键价位
    TITLE_TEMPLATES = {
        "bullish": [
            "【技术解析】{name}关键支撑{support}，能否企稳反弹？",
            "【结构分析】{name}测试{support}结构位，多头机会浮现",
            "【趋势研判】{name}0.618回撤位{support}成多空分水岭",
            "【策略追踪】{name}回踩关键区间，关注{support}防守",
        ],
        "bearish": [
            "【风险预警】{name}{resistance}承压明显，调整风险加大",
            "【技术解析】{name}测试{resistance}阻力，C浪风险待释放",
            "【结构分析】{name}关键位{resistance}失守，趋势转空？",
            "【趋势研判】{name}0.618阻力{resistance}成短期天花板",
        ],
        "neutral": [
            "【技术解析】{name}区间整理，关注{support}-{resistance}突破",
            "【结构分析】{name}多空博弈关键期，方向选择在即",
            "【策略追踪】{name}时间换空间，等待趋势明朗",
        ]
    }

    # Emoji使用克制，体现专业
    INDICATORS = {
        "support": "📍",
        "resistance": "⛔",
        "target": "🎯",
        "trend_up": "↗️",
        "trend_down": "↘️",
        "warning": "⚠️",
        "key": "🔑",
        "time": "⏰",
        "chart": "📊",
        "money": "💰",
    }

    def __init__(self, ticker: str, name: str, analysis: Dict, df_info: Dict, info_analysis: Optional[Dict] = None):
        self.ticker = ticker
        self.name = name
        self.analysis = analysis
        self.df_info = df_info
        self.info_analysis = info_analysis or {}
        self.trend = analysis.get("trend", "震荡")

        # 提取关键价格
        self.current_price = df_info.get("current_price", 0)
        self.buy_zone_low = analysis.get("buy_zone_low", 0)
        self.buy_zone_high = analysis.get("buy_zone_high", 0)
        self.stop_loss = analysis.get("stop_loss", 0)
        self.take_profit_1 = analysis.get("take_profit_1", 0)
        self.take_profit_2 = analysis.get("take_profit_2", 0)
        self.take_profit_3 = analysis.get("take_profit_3", 0)

    def _format_price(self, price: float) -> str:
        """格式化价格显示"""
        if price >= 1000:
            return f"{price:.0f}"
        elif price >= 100:
            return f"{price:.1f}"
        else:
            return f"{price:.2f}"

    def _generate_title(self) -> str:
        """生成专业标题 - 突出关键价位"""
        if self.trend == "多头":
            templates = self.TITLE_TEMPLATES["bullish"]
            key_level = self._format_price(self.buy_zone_low) if self.buy_zone_low else "关键支撑"
        elif self.trend == "空头":
            templates = self.TITLE_TEMPLATES["bearish"]
            key_level = self._format_price(self.analysis.get("sell_zone_high", 0)) if self.analysis.get("sell_zone_high") else "关键阻力"
        else:
            templates = self.TITLE_TEMPLATES["neutral"]
            key_level = f"{self._format_price(self.buy_zone_low)}-{self._format_price(self.analysis.get('sell_zone_high', 0))}"

        return random.choice(templates).format(
            name=self.name,
            support=self._format_price(self.buy_zone_low) if self.buy_zone_low else "关键位",
            resistance=self._format_price(self.analysis.get("sell_zone_high", 0)) if self.analysis.get("sell_zone_high") else "关键位"
        )

    def _generate_core_question(self) -> str:
        """核心问题/定位 —— 开门见山提出核心矛盾"""
        lines = [f"\n{self.INDICATORS['key']} 【核心问题】"]

        if self.trend == "多头":
            question = f"当前{self.name}处于调整末端还是下跌中继？{self._format_price(self.buy_zone_low)}一线能否形成有效支撑，成为多头反攻的跳板？"
        elif self.trend == "空头":
            resistance = self._format_price(self.analysis.get("sell_zone_high", 0))
            question = f"{self.name}在{resistance}附近遇阻回落，是短期调整还是趋势反转的开始？"
        else:
            question = f"{self.name}维持区间震荡格局，多空双方围绕{self._format_price(self.current_price)}展开博弈，方向选择何时到来？"

        lines.append(question)
        return "\n".join(lines)

    def _generate_underlying_logic(self) -> str:
        """底层逻辑 —— 技术面深度解析"""
        lines = [f"\n{self.INDICATORS['chart']} 【底层逻辑】"]

        # 趋势结构分析
        trend_strength = self.analysis.get("trend_strength_level", "中性")
        if self.trend == "多头":
            lines.append(f"1. 趋势结构：{self.name}中长期维持上升通道，当前处于{self.TECHNICAL_TERMS['wave'][4]}阶段。")
        elif self.trend == "空头":
            lines.append(f"1. 趋势结构：{self.name}跌破关键均线支撑，短期进入{self.TECHNICAL_TERMS['wave'][0]}调整，需警惕风险释放。")
        else:
            lines.append(f"1. 趋势结构：{self.name}维持箱体震荡，尚未形成明确方向，{self.INDICATORS['key']}关注区间突破。")

        # 斐波那契分析
        fib_info = self.analysis.get("fib_0.618")
        if fib_info:
            if self.trend == "多头":
                lines.append(f"2. 斐波那契：价格回踩0.618回撤位({self._format_price(self.buy_zone_low)}附近)，该位置是多头最后的防线。")
            else:
                lines.append(f"2. 斐波那契：反弹受阻于0.618回撤位({self._format_price(self.analysis.get('sell_zone_high', 0))})，形成强阻力。")

        # EMA隧道分析
        if self.analysis.get("inner_bull"):
            lines.append(f"3. 均线系统：EMA内隧道维持多头排列，{self._format_price(self.analysis.get('inner_bottom', 0))}构成动态支撑。")
        elif self.analysis.get("inner_bear"):
            lines.append(f"3. 均线系统：EMA隧道空头排列，{self._format_price(self.analysis.get('inner_top', 0))}形成动态压力。")

        # 布林带分析
        bb_position = self.analysis.get("bb_position", "")
        if "下轨" in bb_position and self.trend == "多头":
            lines.append(f"4. 布林带：价格触及布林带下轨，超卖迹象显现，技术性反弹概率增大。")
        elif "上轨" in bb_position and self.trend == "空头":
            lines.append(f"4. 布林带：价格触及布林带上轨，超买区域承压，回调风险加大。")

        # 量价分析
        vol_signal = self.analysis.get("vol_signal", "")
        if "放量上涨" in vol_signal:
            lines.append(f"5. 量能配合：近期放量上涨，资金{self.TECHNICAL_TERMS['momentum'][2]}明显，支撑反弹延续。")
        elif "放量下跌" in vol_signal:
            lines.append(f"5. 量能配合：放量下跌显示{self.TECHNICAL_TERMS['momentum'][3]}，短期仍有下探空间。")

        # KDJ指标
        kdj_signal = self.analysis.get("kdj_signal", "")
        if "买入" in kdj_signal:
            lines.append(f"6. 动能指标：KDJ金叉形成，超卖区域反弹，短期动能转强。")
        elif "卖出" in kdj_signal:
            lines.append(f"6. 动能指标：KDJ死叉确认，超买区域回落，动能转弱。")

        return "\n".join(lines)

    def _generate_key_levels(self) -> str:
        """关键位分析 —— 核心支撑阻力位"""
        lines = [f"\n{self.INDICATORS['target']} 【关键位分析】"]

        # 支撑位
        supports = []
        if self.buy_zone_low:
            supports.append(f"强支撑：{self._format_price(self.buy_zone_low)}（斐波那契0.618位）")
        if self.analysis.get("inner_bottom"):
            supports.append(f"动态支撑：{self._format_price(self.analysis['inner_bottom'])}（EMA隧道下轨）")
        if self.analysis.get("bb_lower"):
            supports.append(f"技术支撑：{self._format_price(self.analysis['bb_lower'])}（布林带下轨）")

        if supports:
            lines.append("【支撑区间】")
            lines.extend([f"  {self.INDICATORS['support']} {s}" for s in supports[:2]])

        # 阻力位
        resistances = []
        sell_high = self.analysis.get("sell_zone_high")
        if sell_high:
            resistances.append(f"强阻力：{self._format_price(sell_high)}（前期高点）")
        if self.analysis.get("inner_top"):
            resistances.append(f"动态阻力：{self._format_price(self.analysis['inner_top'])}（EMA隧道上轨）")
        if self.analysis.get("bb_upper"):
            resistances.append(f"技术阻力：{self._format_price(self.analysis['bb_upper'])}（布林带上轨）")

        if resistances:
            lines.append("\n【压力区间】")
            lines.extend([f"  {self.INDICATORS['resistance']} {r}" for r in resistances[:2]])

        # 目标位
        lines.append("\n【目标测算】")
        if self.take_profit_1:
            lines.append(f"  {self.INDICATORS['target']} 第一目标：{self._format_price(self.take_profit_1)}")
        if self.take_profit_2:
            lines.append(f"  {self.INDICATORS['target']} 第二目标：{self._format_price(self.take_profit_2)}")
        if self.take_profit_3:
            lines.append(f"  {self.INDICATORS['target']} 第三目标：{self._format_price(self.take_profit_3)}")

        # 防守位
        if self.stop_loss:
            lines.append(f"\n  {self.INDICATORS['warning']} 止损防守：{self._format_price(self.stop_loss)}（跌破离场）")

        return "\n".join(lines)

    def _generate_tracking_points(self) -> str:
        """跟踪要点 —— 后续关注的关键信号"""
        lines = [f"\n{self.INDICATORS['time']} 【跟踪要点】"]

        tracking_items = []

        if self.trend == "多头":
            tracking_items = [
                f"1. 关注{self._format_price(self.buy_zone_low)}支撑有效性，若企稳可考虑轻仓试多",
                f"2. 突破{self._format_price(self.analysis.get('inner_top', 0))}确认反弹延续",
                f"3. 量能配合：反弹需放量确认，无量上涨谨防假突破",
                f"4. KDJ指标维持金叉状态，若死叉需重新评估",
            ]
        elif self.trend == "空头":
            tracking_items = [
                f"1. 关注{self._format_price(self.analysis.get('sell_zone_high', 0))}阻力压制，反弹受阻可高空",
                f"2. 跌破{self._format_price(self.stop_loss)}确认空头延续",
                f"3. 量能观察：下跌放量显示抛压，缩量反弹力度有限",
                f"4. 若形成底背离结构，警惕趋势反转",
            ]
        else:
            tracking_items = [
                f"1. 等待方向选择：突破{self._format_price(self.analysis.get('sell_zone_high', 0))}看多，跌破{self._format_price(self.buy_zone_low)}看空",
                f"2. 区间高抛低吸，严格控制止损",
                f"3. 关注MACD是否形成金叉/死叉信号",
                f"4. 布林带收口后的突破方向",
            ]

        lines.extend(tracking_items)
        return "\n".join(lines)

    def _generate_strategy_summary(self) -> str:
        """策略总结 —— 简洁明了的操作建议"""
        lines = [f"\n{self.INDICATORS['money']} 【策略总结】"]

        action = self.analysis.get("action", "观望")
        confidence = self.analysis.get("confidence_score", 50)

        if action == "进攻":
            lines.append(f"操作方向：{self.INDICATORS['trend_up']} 偏多思路")
            lines.append(f"入场区间：{self._format_price(self.buy_zone_low)} - {self._format_price(self.buy_zone_high)}")
            lines.append(f"止损设置：{self._format_price(self.stop_loss)}（风险控制在合理范围）")
            if self.take_profit_1:
                lines.append(f"目标位：第一目标{self._format_price(self.take_profit_1)}，第二目标{self._format_price(self.take_profit_2)}")
        elif action == "防守":
            lines.append(f"操作方向：{self.INDICATORS['trend_down']} 偏空思路")
            lines.append(f"关键阻力：{self._format_price(self.analysis.get('sell_zone_high', 0))}（该位置下方承压做空）")
            lines.append(f"止损设置：{self._format_price(self.analysis.get('sell_zone_high', 0) * 1.02 if self.analysis.get('sell_zone_high') else 0)}")
        else:
            lines.append(f"操作方向：{self.INDICATORS['time']} 观望等待")
            lines.append(f"当前处于震荡整理阶段，建议等待方向明朗后再行介入")

        lines.append(f"\n信号置信度：{confidence}%（基于多指标共振分析）")

        return "\n".join(lines)

    def _generate_risk_warning(self) -> str:
        """风险提示 —— 标准化免责声明"""
        lines = [f"\n{self.INDICATORS['warning']} 【风险提示】"]

        warnings = [
            "1. 以上分析基于技术分析方法，仅供参考，不构成投资建议",
            "2. 市场有风险，投资需谨慎，请根据自身情况独立判断",
            "3. 技术分析存在局限性，无法预测突发消息面影响",
            "4. 建议严格控制仓位，设置止损，做好风险管理",
        ]

        lines.extend(warnings)
        lines.append(f"\n分析时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}")
        lines.append(f"分析标的：{self.name}({self.ticker})")

        return "\n".join(lines)

    def _generate_fundamental_note(self) -> str:
        """基本面备注（如果有信息面分析数据）"""
        if not self.info_analysis:
            return ""

        lines = [f"\n📋 【基本面备注】"]

        fundamental = self.info_analysis.get("fundamental", {})
        if fundamental.get("rating"):
            lines.append(f"基本面评级：{fundamental['rating']}（评分：{fundamental.get('overall_score', 0):.0f}/100）")

        capital = self.info_analysis.get("capital_flow", {})
        if capital.get("flow_rating"):
            lines.append(f"资金流向：{capital['flow_rating']}")

        sentiment = self.info_analysis.get("sentiment", {})
        if sentiment.get("sentiment"):
            lines.append(f"市场情绪：{sentiment['sentiment']}")

        if len(lines) > 1:
            return "\n".join(lines)
        return ""

    def generate_full_analysis(self) -> str:
        """生成完整的专业分析文章"""
        sections = [
            self._generate_title(),
            "",
            self._generate_core_question(),
            self._generate_underlying_logic(),
            self._generate_key_levels(),
            self._generate_strategy_summary(),
            self._generate_tracking_points(),
            self._generate_fundamental_note(),
            "",
            "—" * 40,
            self._generate_risk_warning(),
        ]

        return "\n".join(sections)

    def generate_compact_summary(self) -> str:
        """生成简洁版摘要（适合快速阅读）"""
        chg_pct = self.df_info.get("chg_pct", 0)
        trend_emoji = self.INDICATORS['trend_up'] if chg_pct >= 0 else self.INDICATORS['trend_down']

        lines = [
            f"{trend_emoji} {self.name}({self.ticker})技术速递",
            "",
            f"当前价格：{self._format_price(self.current_price)}（{chg_pct:+.2%}）",
            f"趋势判断：{self.trend}（{self.analysis.get('trend_strength_level', '中性')}）",
            f"操作建议：{self.analysis.get('action', '观望')}",
            "",
            f"【关键位】",
            f"  支撑：{self._format_price(self.buy_zone_low)} | 阻力：{self._format_price(self.analysis.get('sell_zone_high', 0))}",
        ]

        if self.take_profit_1:
            lines.append(f"  目标：{self._format_price(self.take_profit_1)}")

        lines.extend([
            "",
            f"【核心逻辑】",
            self.analysis.get("advice", "持续关注技术面变化"),
            "",
            f"⚠️ 仅供参考，不构成投资建议",
        ])

        return "\n".join(lines)

    def generate_xiaohongshu_post(self) -> str:
        """生成小红书风格的专业分析（略有简化，适合社媒）"""
        chg_pct = self.df_info.get("chg_pct", 0)

        # 标题
        if self.trend == "多头":
            title = f"🔥 {self.name}关键位{self._format_price(self.buy_zone_low)}，企稳上车？"
        elif self.trend == "空头":
            title = f"⚠️ {self.name}{self._format_price(self.analysis.get('sell_zone_high', 0))}承压，该跑吗？"
        else:
            title = f"📊 {self.name}震荡整理，方向在哪？"

        lines = [
            title,
            "",
            f"家人们，{self.name}最新技术分析来了！👇",
            "",
            f"📍 核心问题",
            f"当前{self._format_price(self.current_price)}，是{self.trend}格局，关键看{'支撑' if self.trend == '多头' else '阻力'}位得失",
            "",
            f"📈 技术逻辑",
        ]

        # 简化逻辑说明
        if self.trend == "多头":
            lines.append(f"✅ 斐波那契0.618位{self._format_price(self.buy_zone_low)}形成支撑")
            if self.analysis.get("inner_bull"):
                lines.append(f"✅ EMA隧道多头排列，趋势 intact")
        elif self.trend == "空头":
            lines.append(f"❌ 0.618回撤位{self._format_price(self.analysis.get('sell_zone_high', 0))}受阻回落")
            if self.analysis.get("inner_bear"):
                lines.append(f"❌ EMA空头排列，压力明显")

        # 共振信号
        resonance = self.analysis.get("resonance", "")
        if "买入" in resonance:
            lines.append(f"💚 多指标共振：{resonance}")
        elif "卖出" in resonance:
            lines.append(f"❌ 多指标共振：{resonance}")

        lines.extend([
            "",
            f"🎯 关键价位",
            f"支撑位：{self._format_price(self.buy_zone_low)}",
            f"阻力位：{self._format_price(self.analysis.get('sell_zone_high', 0))}",
        ])

        if self.stop_loss:
            lines.append(f"防守位：{self._format_price(self.stop_loss)}")
        if self.take_profit_1:
            lines.append(f"目标位：{self._format_price(self.take_profit_1)}")

        lines.extend([
            "",
            f"💡 操作建议：{self.analysis.get('action', '观望')}",
            f"置信度：{self.analysis.get('confidence_score', 50)}%",
            "",
            f"⏰ 后续跟踪：",
            f"• 关注{self._format_price(self.buy_zone_low if self.trend == '多头' else self.analysis.get('sell_zone_high', 0))}位得失",
            f"• 量能配合是关键",
            f"• 严格止损，控制风险",
            "",
            f"⚠️ 风险提示：以上仅供参考，不构成投资建议！",
            f"股市有风险，入市需谨慎！",
            "",
            f"#{self.ticker} #股票分析 #技术分析 #投资理财",
            f"💬 你看好{self.name}吗？评论区聊聊～",
        ])

        return "\n".join(lines)


# 便捷函数
def generate_professional_analysis(ticker: str, name: str, analysis: Dict, df_info: Dict, info_analysis: Optional[Dict] = None) -> str:
    """生成专业分析文章"""
    generator = ProfessionalAnalysisGenerator(ticker, name, analysis, df_info, info_analysis)
    return generator.generate_full_analysis()


def generate_compact_summary(ticker: str, name: str, analysis: Dict, df_info: Dict) -> str:
    """生成简洁摘要"""
    generator = ProfessionalAnalysisGenerator(ticker, name, analysis, df_info)
    return generator.generate_compact_summary()


def generate_xiaohongshu_post(ticker: str, name: str, analysis: Dict, df_info: Dict) -> str:
    """生成小红书风格推文"""
    generator = ProfessionalAnalysisGenerator(ticker, name, analysis, df_info)
    return generator.generate_xiaohongshu_post()
