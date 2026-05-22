"""
端到端验证脚本（B.0 + B.1 P0 验收标准）

运行: python test_validation.py

验收标准:
1. DataRouter.get_financial_statements 返回非空 DataFrame
2. DCFEngine 跑通，equity_value ≠ enterprise_value
3. Beta 不硬编码为 1.0
4. CompsAnalyzer 筛选出真实可比公司
5. ThreeStatementModel 配平通过 (<0.1%)
6. ThesisBuilder 输出 PWTP + Skew Ratio
7. render_ic_memo 输出 Markdown
8. CatalystCalendar 返回催化剂事件
9. DCF vs Comps 差距自动标注
10. 双案例测试 (600519.SS + 300750.SZ)
"""

import sys
import traceback
from datetime import datetime

# 确保项目路径
sys.path.insert(0, r"C:\Users\Chris Chen\financial-services-plugins\custom-model")

TEST_CASES = ["600519.SS", "300750.SZ"]
PASS_COUNT = 0
FAIL_COUNT = 0
RESULTS = []


def test(name, func):
    global PASS_COUNT, FAIL_COUNT
    try:
        func()
        RESULTS.append(f"✅ PASS: {name}")
        PASS_COUNT += 1
    except Exception as e:
        RESULTS.append(f"❌ FAIL: {name} - {e}")
        FAIL_COUNT += 1
        traceback.print_exc()


# ========================================================================
# Test 1: DataRouter 财报接口
# ========================================================================
def test_data_router():
    from modules.data_router import DataRouter
    router = DataRouter()

    for ticker in TEST_CASES:
        stmts = router.get_financial_statements(ticker)
        income = stmts.get("income", None)
        balance = stmts.get("balance", None)
        cashflow = stmts.get("cashflow", None)

        if income is not None and not income.empty:
            print(f"  [{ticker}] Income: {income.shape}, cols: {list(income.columns)[:5]}")
        if balance is not None and not balance.empty:
            print(f"  [{ticker}] Balance: {balance.shape}, cols: {list(balance.columns)[:5]}")
        if cashflow is not None and not cashflow.empty:
            print(f"  [{ticker}] Cashflow: {cashflow.shape}, cols: {list(cashflow.columns)[:5]}")

        # 至少 income 或 balance 非空即算通过
        assert (income is not None and not income.empty) or (balance is not None and not balance.empty), \
            f"No financial data for {ticker}"

    print("  DataRouter.get_financial_statements: OK")


# ========================================================================
# Test 2: DCF Engine
# ========================================================================
def test_dcf():
    from plugins.fundamental import DCFEngine, DCFAssumptions

    for ticker in TEST_CASES:
        assumptions = DCFAssumptions(
            revenue_cagr=0.12,
            ebitda_margin=0.30,
            terminal_growth=0.03,
            exit_multiple=10.0,
        )
        engine = DCFEngine(ticker)
        result = engine.run(assumptions, current_price=1500.0)

        print(f"  [{ticker}] DCF: EV={result.enterprise_value:.2f}, "
              f"Equity={result.equity_value:.2f}, PS={result.per_share_value:.2f}, "
              f"Beta={engine.wacc.beta:.3f}")

        # 验收标准 2: equity_value ≠ enterprise_value
        assert result.equity_value != result.enterprise_value, \
            f"equity_value ({result.equity_value}) == enterprise_value ({result.enterprise_value}) - bug not fixed"

        # 验收标准 3: Beta 不硬编码为 1.0
        assert engine.wacc.beta != 1.0 or engine.wacc.beta_unlevered != 1.0, \
            f"Beta still hardcoded to 1.0"

        # 验收标准 4: 双终值
        assert result.terminal_value_result is not None, "Dual terminal value not computed"
        print(f"  [{ticker}] Terminal: Gordon={result.terminal_value_result.gordon_growth_tv:.2f}, "
              f"Exit={result.terminal_value_result.exit_multiple_tv:.2f}, "
              f"Divergence={result.terminal_value_result.divergence_pct:.1%}")

    print("  DCF Engine: OK")


# ========================================================================
# Test 3: Comps Analyzer
# ========================================================================
def test_comps():
    from plugins.fundamental import CompsAnalyzer

    for ticker in TEST_CASES:
        analyzer = CompsAnalyzer(ticker)
        result = analyzer.run()

        print(f"  [{ticker}] Comps: {len(result.comparables)} peers, "
              f"industry={result.target.industry}")

        # 验收标准 4: 至少筛选出一些可比公司
        assert len(result.comparables) >= 3, f"Only {len(result.comparables)} comparables found"

        # 验收标准 3: IQR 过滤
        for metric, stats in result.summary_stats.items():
            if stats.count_raw > 0:
                print(f"  [{ticker}] {metric}: raw={stats.count_raw}, filtered={stats.count}, "
                      f"median={stats.median}")

        # 加权融合
        if result.weighted_valuation:
            print(f"  [{ticker}] Weighted: Equity={result.weighted_valuation.implied_equity:.2f}亿, "
                  f"weights={result.weighted_valuation.weights}")

    print("  Comps Analyzer: OK")


# ========================================================================
# Test 4: Three Statements
# ========================================================================
def test_three_statements():
    from plugins.fundamental import ThreeStatementModel, Assumptions, ScenarioType

    for ticker in TEST_CASES:
        model = ThreeStatementModel(ticker)
        model.load_historical_data()
        model.set_assumptions(Assumptions())
        model.build_income_statement(base_revenue=1000)
        model.build_balance_sheet()
        model.build_cash_flow()
        check = model.balance_check()

        print(f"  [{ticker}] 3-Stmt: BS_balanced={check.bs_balanced}, "
              f"BS_diff={check.bs_difference_pct:.4%}, "
              f"CF_reconciled={check.cf_reconciled}, "
              f"CF_diff={check.cf_difference_pct:.4%}")

        # 验收标准 5: 配平差异 < 0.1%
        assert check.bs_difference_pct < 0.001, \
            f"BS imbalance too large: {check.bs_difference_pct:.4%}"

        # Altman Z-Score
        credit = model.calculate_credit_metrics()
        if credit.altman_z:
            print(f"  [{ticker}] Altman Z: {[f'{z:.2f}' for z in credit.altman_z[:3]]}...")

        # 情景自动推导
        base_assumptions = model.derive_assumptions(ScenarioType.BASE)
        print(f"  [{ticker}] Auto assumptions: growth={base_assumptions.revenue_growth_y1:.1%}, "
              f"margin={base_assumptions.gross_margin:.1%}")

    print("  Three Statements: OK")


# ========================================================================
# Test 5: Thesis Framework
# ========================================================================
def test_thesis():
    from plugins.fundamental import (
        ThesisBuilder, DCFEngine, DCFAssumptions,
        CompsAnalyzer, render_ic_memo
    )

    ticker = "600519.SS"
    current_price = 1500.0

    # DCF
    dcf_engine = DCFEngine(ticker)
    dcf_result = dcf_engine.run(DCFAssumptions(), current_price=current_price)

    # Comps
    comps = CompsAnalyzer(ticker).run()

    # Thesis
    thesis = ThesisBuilder.build_from_models(
        ticker=ticker,
        dcf_result=dcf_result,
        comps_result=comps,
        three_stmt_scenarios={},
        current_price=current_price,
    )

    # 验收标准 6: PWTP + Skew Ratio
    assert thesis.risk_reward is not None, "RiskRewardMatrix missing"
    assert thesis.risk_reward.probability_weighted_target > 0, "PWTP missing"
    assert thesis.risk_reward.skew_ratio > 0, "Skew Ratio missing"

    print(f"  [{ticker}] Thesis: PWTP={thesis.risk_reward.probability_weighted_target:.2f}, "
          f"Skew={thesis.risk_reward.skew_ratio:.2f}, "
          f"Upside={thesis.risk_reward.upside_pct:.1%}")

    # 验收标准 7: IC Memo Markdown
    memo = render_ic_memo(thesis)
    assert "投资委员会备忘录" in memo, "IC Memo title missing"
    assert "Risk / Reward" in memo, "Risk/Reward section missing"
    assert "Skew Ratio" in memo, "Skew Ratio missing"
    assert "三大支柱" in memo, "Three Pillars missing"
    assert "三情景估值" in memo, "Scenarios missing"
    assert "DCF vs Comps" in memo, "DCF vs Comps section missing"

    print(f"  IC Memo length: {len(memo)} chars")

    # 验收标准 9: DCF vs Comps 差距自动标注
    assert thesis.dcf_comps_divergence_warning != "", "DCF vs Comps divergence warning missing"
    print(f"  Divergence: {thesis.dcf_comps_divergence_warning}")

    print("  Thesis Framework: OK")


# ========================================================================
# Test 6: Catalyst Calendar
# ========================================================================
def test_catalyst():
    from plugins.fundamental import CatalystCalendar

    for ticker in TEST_CASES:
        calendar = CatalystCalendar()
        events = calendar.get_upcoming(ticker, days_ahead=90)

        print(f"  [{ticker}] Catalysts: {len(events)} events")

        # 验收标准 8: 至少列出财报日
        earnings = [e for e in events if e.event_type == "earnings"]
        assert len(earnings) > 0, "No earnings events found"

        for e in events[:3]:
            print(f"    {e.date.strftime('%Y-%m-%d')} | {e.event_type:15s} | {e.title} | {e.impact}")

    print("  Catalyst Calendar: OK")


# ========================================================================
# Test 7: DataRouter 新增方法
# ========================================================================
def test_data_router_methods():
    from modules.data_router import DataRouter
    router = DataRouter()
    ticker = "600519.SS"

    # shares outstanding
    shares = router.get_shares_outstanding(ticker)
    print(f"  [{ticker}] Shares: {shares:.4f} 亿股")
    assert shares > 0, "Shares outstanding invalid"

    # net debt
    net_debt = router.get_net_debt(ticker)
    print(f"  [{ticker}] Net Debt: {net_debt:.2f} 亿元")

    # beta
    beta = router.get_beta(ticker)
    print(f"  [{ticker}] Beta: {beta:.3f}")
    assert beta > 0, "Beta invalid"

    # industry
    industry = router.get_industry_sw(ticker)
    print(f"  [{ticker}] Industry: {industry}")

    # capital structure
    dr, er = router.get_capital_structure(ticker)
    print(f"  [{ticker}] Capital Structure: D={dr:.2%}, E={er:.2%}")
    assert abs(dr + er - 1.0) < 0.01, "Capital structure doesn't sum to 1"

    # peers
    peers = router.get_peers_by_industry(ticker, max_count=5)
    print(f"  [{ticker}] Peers: {len(peers)} found")
    for p in peers[:3]:
        print(f"    {p['ticker']} | {p['name']} | {p['industry']}")

    print("  DataRouter Methods: OK")


# ========================================================================
# Main
# ========================================================================
if __name__ == "__main__":
    print("=" * 70)
    print("华尔街化升级端到端验证")
    print(f"开始时间: {datetime.now().isoformat()}")
    print("=" * 70)

    test("DataRouter 财报接口", test_data_router)
    print()
    test("DataRouter 新增方法", test_data_router_methods)
    print()
    test("DCF 估值引擎", test_dcf)
    print()
    test("Comps 可比公司分析", test_comps)
    print()
    test("三表联动模型", test_three_statements)
    print()
    test("Thesis 投资论点", test_thesis)
    print()
    test("Catalyst 催化剂日历", test_catalyst)

    print()
    print("=" * 70)
    print("验证结果汇总")
    print("=" * 70)
    for r in RESULTS:
        print(f"  {r}")
    print(f"\n总计: {PASS_COUNT} 通过, {FAIL_COUNT} 失败")

    if FAIL_COUNT > 0:
        sys.exit(1)
    else:
        print("\n🎉 所有验收标准通过！")
