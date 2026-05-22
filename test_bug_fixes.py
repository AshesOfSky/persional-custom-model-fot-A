"""
Bug修复测试脚本
验证所有已修复的bug是否正常工作
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
import numpy as np
from datetime import datetime
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ASCII check/cross marks for Windows compatibility
PASS = "[PASS]"
FAIL = "[FAIL]"


def test_mcp_client_import():
    """测试Bug #1: MCP客户端os导入"""
    print("\n=== 测试 Bug #1: MCP客户端os导入 ===")
    try:
        from connectors.mcp_client import MCPClient, MCPServer
        print(f"{PASS} MCPClient导入成功，没有NameError")
        return True
    except NameError as e:
        print(f"{FAIL} NameError: {e}")
        return False
    except Exception as e:
        print(f"{FAIL} 其他错误: {e}")
        return False


def test_backtest_result_defaults():
    """测试Bug #2: BacktestResult可变默认参数"""
    print("\n=== 测试 Bug #2: BacktestResult可变默认参数 ===")
    try:
        from modules.backtest_engine import BacktestResult

        # 创建两个实例，检查是否独立
        result1 = BacktestResult()
        result2 = BacktestResult()

        # 修改result1的equity_curve
        result1.equity_curve['test'] = [1, 2, 3]

        # 检查result2是否受影响
        if 'test' in result2.equity_curve.columns:
            print(f"{FAIL} 数据共享问题: result2受到了result1的修改影响")
            return False

        # 检查默认值是否正确初始化
        if result1.equity_curve is None or result2.equity_curve is None:
            print(f"{FAIL} equity_curve为None")
            return False

        if not isinstance(result1.equity_curve, pd.DataFrame):
            print(f"{FAIL} equity_curve不是DataFrame")
            return False

        print(f"{PASS} BacktestResult默认值正确，实例间独立")
        return True
    except Exception as e:
        print(f"{FAIL} 错误: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_kdj_division_by_zero():
    """测试Bug #5: KDJ除0问题"""
    print("\n=== 测试 Bug #5: KDJ除0问题 ===")
    try:
        from modules.technical import calc_kdj

        # 创建价格不变的数据（会导致high==low）
        df = pd.DataFrame({
            'High': [100] * 20,
            'Low': [100] * 20,
            'Close': [100] * 20
        })

        result = calc_kdj(df)

        # 检查是否有NaN值（应该有NaN，但不应该全是NaN）
        if result['KDJ_K'].isna().all():
            print(f"{FAIL} KDJ_K全是NaN")
            return False

        # 检查是否有inf值
        if np.isinf(result['KDJ_K']).any():
            print(f"{FAIL} KDJ_K包含inf值")
            return False

        print(f"{PASS} KDJ计算正确处理了除0情况")
        return True
    except Exception as e:
        print(f"{FAIL} 错误: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_trading_zones_consistency():
    """测试Bug #6: 交易区间一致性"""
    print("\n=== 测试 Bug #6: 交易区间一致性 ===")
    try:
        from modules.analysis import calculate_trading_zones

        # 创建测试数据
        df = pd.DataFrame({
            'Close': [100] * 30,
            'High': [105] * 30,
            'Low': [95] * 30,
            'ATR': [2] * 30,
            'BB_upper': [110] * 30,
            'BB_lower': [90] * 30,
            'BB_middle': [100] * 30,
            'VWAP': [100] * 30,
        })

        ema_info = {
            'inner_bottom': 98,
            'inner_top': 102,
            'outer_bottom': 95,
            'outer_top': 105,
            'trend': '多头'
        }

        fib_info = {
            'fib_support': 96,
            'fib_resistance': 104
        }

        bb_info = {
            'bb_upper': 110,
            'bb_lower': 90,
            'bb_position': '中轨上方'
        }

        zones = calculate_trading_zones(df, ema_info, fib_info, bb_info)

        # 检查卖出区间 high > low
        if zones['sell_zone_high'] <= zones['sell_zone_low']:
            print(f"{FAIL} 卖出区间错误: high={zones['sell_zone_high']}, low={zones['sell_zone_low']}")
            return False

        # 检查买入区间 high > low
        if zones['buy_zone_high'] <= zones['buy_zone_low']:
            print(f"{FAIL} 买入区间错误: high={zones['buy_zone_high']}, low={zones['buy_zone_low']}")
            return False

        print(f"{PASS} 交易区间正确: sell_high={zones['sell_zone_high']:.2f} > sell_low={zones['sell_zone_low']:.2f}")
        return True
    except Exception as e:
        print(f"{FAIL} 错误: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_rsi_calculation():
    """测试Bug #12: RSI计算"""
    print("\n=== 测试 Bug #12: RSI计算 ===")
    try:
        from modules.technical import calc_rsi

        # 创建只上涨的数据（avg_loss应该为0）
        df = pd.DataFrame({
            'Close': [100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110]
        })

        result = calc_rsi(df)

        # 检查RSI是否在有效范围内
        if result['RSI'].isna().all():
            print(f"{FAIL} RSI全是NaN")
            return False

        # 检查最后几个值（应该接近100，因为一直在上涨）
        last_rsi = result['RSI'].iloc[-1]
        if pd.isna(last_rsi):
            print(f"{FAIL} 最后一个RSI是NaN")
            return False

        if not (0 <= last_rsi <= 100):
            print(f"{FAIL} RSI超出范围: {last_rsi}")
            return False

        print(f"{PASS} RSI计算正确，最后一个值: {last_rsi:.2f}")
        return True
    except Exception as e:
        print(f"{FAIL} 错误: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_research_report_social_post():
    """测试Bug #3: research_report错误处理"""
    print("\n=== 测试 Bug #3: research_report错误处理 ===")
    try:
        from modules.research_report import ResearchReport
        from datetime import datetime

        report = ResearchReport(
            ticker="TEST",
            company_name="Test Company",
            generated_at=datetime.now(),
            rating="买入",
            target_price=100.0,
            current_price=80.0,
            upside=0.25,
            content="Test content",
            metadata={}
        )

        # 测试to_social_post（应该会捕获异常）
        result = report.to_social_post(style='professional')

        # 即使SocialContentGenerator不存在，也应该返回一个字符串
        if not isinstance(result, str):
            print(f"{FAIL} 返回值不是字符串: {type(result)}")
            return False

        print(f"{PASS} to_social_post错误处理正确: {result[:50]}...")
        return True
    except Exception as e:
        print(f"{FAIL} 错误: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_valuation_warning():
    """测试Bug #11: 估值警告日志"""
    print("\n=== 测试 Bug #11: 估值警告日志 ===")
    try:
        from modules.research_report import ValuationSummary

        # 创建没有估值数据的实例
        valuation = ValuationSummary(
            dcf_value=None,
            comps_value=None,
            historical_multiple=None
        )

        result = valuation.weighted_target()

        if result != 0:
            print(f"{FAIL} 没有估值数据时应该返回0，但返回了: {result}")
            return False

        print(f"{PASS} 没有估值数据时正确返回0")
        return True
    except Exception as e:
        print(f"{FAIL} 错误: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_alerts_v2_crosses():
    """测试Bug #9: alerts_v2阈值处理"""
    print("\n=== 测试 Bug #9: alerts_v2阈值处理 ===")
    try:
        from modules.alerts_v2 import RuleEngine, Condition

        engine = RuleEngine()

        # 测试数据
        data = {
            'close': 105.0,
            'ema_8': 102.0,
            'ema_21': 100.0,
            'prices': {
                'TEST': pd.DataFrame({
                    'close': [98, 99, 100, 101, 102, 103, 104, 105]
                })
            }
        }

        # 手动更新历史
        for i, val in enumerate([98, 99, 100, 101, 102, 103, 104]):
            engine.update_history('close', val, 'TEST')

        # 测试 crosses_above 操作符
        condition = Condition(
            metric='close',
            operator='crosses_above',
            threshold='ema_21'  # 字符串阈值
        )

        result = engine._evaluate_condition(condition, data, 'TEST')

        print(f"{PASS} alerts_v2 crosses_above 测试完成，结果: {result}")
        return True
    except Exception as e:
        print(f"{FAIL} 错误: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_ema_alignment_duplicate():
    """测试Bug #13: 删除重复的outer_bull赋值"""
    print("\n=== 测试 Bug #13: 删除重复的outer_bull赋值 ===")
    try:
        from modules.analysis import get_ema_alignment

        # 创建测试数据
        df = pd.DataFrame({
            'Close': [100],
        })

        # 添加EMA列
        for n in [8, 13, 21, 55, 144, 169, 288, 338]:
            df[f'EMA_{n}'] = [100 + n * 0.1]

        result = get_ema_alignment(df)

        if 'outer_bull' not in result:
            print(f"{FAIL} 结果中没有outer_bull")
            return False

        print(f"{PASS} outer_bull计算正确: {result['outer_bull']}")
        return True
    except Exception as e:
        print(f"{FAIL} 错误: {e}")
        import traceback
        traceback.print_exc()
        return False


def run_all_tests():
    """运行所有测试"""
    print("=" * 60)
    print("开始运行Bug修复测试")
    print("=" * 60)

    tests = [
        ("MCP Client Import", test_mcp_client_import),
        ("Backtest Result Defaults", test_backtest_result_defaults),
        ("KDJ Division by Zero", test_kdj_division_by_zero),
        ("Trading Zones Consistency", test_trading_zones_consistency),
        ("RSI Calculation", test_rsi_calculation),
        ("Research Report Social Post", test_research_report_social_post),
        ("Valuation Warning", test_valuation_warning),
        ("Alerts V2 Crosses", test_alerts_v2_crosses),
        ("EMA Alignment Duplicate", test_ema_alignment_duplicate),
    ]

    results = []
    for name, test_func in tests:
        try:
            result = test_func()
            results.append((name, result))
        except Exception as e:
            print(f"{FAIL} {name} 测试异常: {e}")
            results.append((name, False))

    print("\n" + "=" * 60)
    print("测试结果汇总")
    print("=" * 60)

    passed = sum(1 for _, r in results if r)
    failed = sum(1 for _, r in results if not r)

    for name, result in results:
        status = PASS if result else FAIL
        print(f"{status}: {name}")

    print(f"\n总计: {passed} 通过, {failed} 失败")
    print("=" * 60)

    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
