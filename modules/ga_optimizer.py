"""
ga_optimizer.py — 遗传算法(GA)参数优化器

用于搜索策略最优参数组合，替代穷举网格搜索。
特点:
  - 支持整数+浮点混合参数空间
  - 锦标赛选择 + 均匀交叉 + 高斯变异
  - 精英保留策略
  - 支持任意回测目标函数(Sharpe/收益/Calmar等)
  - 可集成到 Walk-Forward 框架中
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Tuple, Callable
from dataclasses import dataclass, field
import logging
import random

logger = logging.getLogger(__name__)


# ── 参数空间定义 ────────────────────────────────────────────────────────

@dataclass
class ParamGene:
    """单个参数的基因定义"""
    name: str
    low: float
    high: float
    dtype: str = "float"   # "int" or "float"
    step: Optional[float] = None  # 离散步长，如 1 for integers

    def random_value(self) -> float:
        if self.dtype == "int":
            return float(random.randint(int(self.low), int(self.high)))
        if self.step:
            steps = int((self.high - self.low) / self.step)
            return self.low + random.randint(0, steps) * self.step
        return random.uniform(self.low, self.high)

    def clip(self, val: float) -> float:
        val = max(self.low, min(self.high, val))
        if self.dtype == "int":
            val = float(round(val))
        elif self.step:
            val = self.low + round((val - self.low) / self.step) * self.step
        return val


# ── 预置参数空间 ────────────────────────────────────────────────────────

MACD_GENES: List[ParamGene] = [
    ParamGene("macd_fast",   5,  20, dtype="int"),
    ParamGene("macd_slow",  15,  50, dtype="int"),
    ParamGene("macd_signal", 5,  15, dtype="int"),
    ParamGene("stop_loss_pct",   0.02, 0.10, step=0.01),
    ParamGene("take_profit_pct", 0.05, 0.25, step=0.01),
]

EMA_GENES: List[ParamGene] = [
    ParamGene("stop_loss_pct",   0.02, 0.10, step=0.01),
    ParamGene("take_profit_pct", 0.05, 0.25, step=0.01),
]

BOLLINGER_GENES: List[ParamGene] = [
    ParamGene("bb_period", 10, 30, dtype="int"),
    ParamGene("bb_std",    1.0, 3.0, step=0.1),
    ParamGene("stop_loss_pct", 0.02, 0.08, step=0.01),
]

RSI_GENES: List[ParamGene] = [
    ParamGene("oversold",  15, 40, dtype="int"),
    ParamGene("overbought", 60, 90, dtype="int"),
    ParamGene("stop_loss_pct", 0.02, 0.08, step=0.01),
]

KDJ_GENES: List[ParamGene] = [
    ParamGene("oversold",  10, 35, dtype="int"),
    ParamGene("overbought", 65, 90, dtype="int"),
    ParamGene("stop_loss_pct", 0.02, 0.08, step=0.01),
]

STRATEGY_GENES: Dict[str, List[ParamGene]] = {
    "macd": MACD_GENES,
    "ema": EMA_GENES,
    "bollinger": BOLLINGER_GENES,
    "rsi": RSI_GENES,
    "kdj": KDJ_GENES,
}


# ── 个体 & 种群 ────────────────────────────────────────────────────────

@dataclass
class Individual:
    """一个参数组合（个体）"""
    genes: Dict[str, float] = field(default_factory=dict)
    fitness: float = -999.0
    trades: int = 0

    def to_params(self) -> Dict:
        """转为回测可用参数字典"""
        result = {}
        for k, v in self.genes.items():
            if k in ("macd_fast", "macd_slow", "macd_signal"):
                result[k] = int(v)
            elif k in ("oversold", "overbought", "bb_period"):
                result[k] = int(v)
            else:
                result[k] = round(v, 4)
        return result


def _create_random_individual(gene_defs: List[ParamGene]) -> Individual:
    genes = {}
    for g in gene_defs:
        genes[g.name] = g.random_value()
    # MACD 约束: fast < slow
    if "macd_fast" in genes and "macd_slow" in genes:
        if genes["macd_fast"] >= genes["macd_slow"]:
            genes["macd_fast"], genes["macd_slow"] = (
                min(genes["macd_fast"], genes["macd_slow"]),
                max(genes["macd_fast"], genes["macd_slow"]) + 1,
            )
    return Individual(genes=genes)


# ── 遗传操作 ────────────────────────────────────────────────────────────

def _tournament_select(pop: List[Individual], k: int = 3) -> Individual:
    """锦标赛选择：随机取 k 个，返回最优"""
    contestants = random.sample(pop, min(k, len(pop)))
    return max(contestants, key=lambda ind: ind.fitness)


def _crossover(p1: Individual, p2: Individual, gene_defs: List[ParamGene]) -> Tuple[Individual, Individual]:
    """均匀交叉"""
    c1_genes, c2_genes = {}, {}
    for g in gene_defs:
        if random.random() < 0.5:
            c1_genes[g.name] = p1.genes[g.name]
            c2_genes[g.name] = p2.genes[g.name]
        else:
            c1_genes[g.name] = p2.genes[g.name]
            c2_genes[g.name] = p1.genes[g.name]

    # MACD 约束修正
    for genes in (c1_genes, c2_genes):
        if "macd_fast" in genes and "macd_slow" in genes:
            if genes["macd_fast"] >= genes["macd_slow"]:
                genes["macd_slow"] = genes["macd_fast"] + 2

    return Individual(genes=c1_genes), Individual(genes=c2_genes)


def _mutate(ind: Individual, gene_defs: List[ParamGene], mutation_rate: float = 0.2) -> Individual:
    """高斯变异"""
    new_genes = dict(ind.genes)
    gene_map = {g.name: g for g in gene_defs}
    for name, val in new_genes.items():
        if random.random() < mutation_rate:
            g = gene_map[name]
            spread = (g.high - g.low) * 0.15  # 变异幅度为范围的15%
            new_val = val + random.gauss(0, spread)
            new_genes[name] = g.clip(new_val)

    # MACD 约束修正
    if "macd_fast" in new_genes and "macd_slow" in new_genes:
        if new_genes["macd_fast"] >= new_genes["macd_slow"]:
            new_genes["macd_slow"] = new_genes["macd_fast"] + 2
            slow_gene = gene_map.get("macd_slow")
            if slow_gene:
                new_genes["macd_slow"] = slow_gene.clip(new_genes["macd_slow"])

    return Individual(genes=new_genes)


# ── 适应度评估 ────────────────────────────────────────────────────────

def _evaluate_fitness(
    ind: Individual,
    df: pd.DataFrame,
    strategy_type: str,
    ticker: str = "",
    use_atr_stop: bool = True,
    board: str = "其他",
    market_cap_yi: Optional[float] = None,
    objective: str = "sharpe",
) -> Individual:
    """用回测引擎评估个体适应度"""
    from .backtest import run_backtest

    params = ind.to_params()
    try:
        result = run_backtest(
            df.copy(),
            strategy_type=strategy_type,
            ticker=ticker,
            use_atr_stop=use_atr_stop,
            board=board,
            market_cap_yi=market_cap_yi,
            **params,
        )

        ind.trades = result.total_trades

        # 交易次数太少 → 惩罚
        if result.total_trades < 3:
            ind.fitness = -999.0
            return ind

        if objective == "sharpe":
            sr = result.sharpe_ratio
            ind.fitness = sr if np.isfinite(sr) else -999.0
        elif objective == "return":
            ind.fitness = result.total_return
        elif objective == "calmar":
            dd = abs(result.max_drawdown_pct) if result.max_drawdown_pct != 0 else 1e-6
            ann_ret = result.total_return  # 简化
            ind.fitness = ann_ret / dd
        else:
            ind.fitness = result.sharpe_ratio if np.isfinite(result.sharpe_ratio) else -999.0

    except Exception as e:
        logger.debug(f"GA evaluate failed: {e}")
        ind.fitness = -999.0

    return ind


# ── GA 主循环 ────────────────────────────────────────────────────────

@dataclass
class GAResult:
    """遗传算法结果"""
    best_params: Dict
    best_fitness: float
    best_trades: int
    generations: int
    population_size: int
    fitness_history: List[float] = field(default_factory=list)  # 每代最优
    avg_fitness_history: List[float] = field(default_factory=list)  # 每代平均
    convergence_gen: int = 0  # 收敛代数
    total_evaluations: int = 0


def run_ga_optimization(
    df: pd.DataFrame,
    strategy_type: str = "macd",
    ticker: str = "",
    population_size: int = 30,
    generations: int = 20,
    elite_count: int = 3,
    crossover_rate: float = 0.8,
    mutation_rate: float = 0.2,
    tournament_k: int = 3,
    objective: str = "sharpe",
    use_atr_stop: bool = True,
    board: str = "其他",
    market_cap_yi: Optional[float] = None,
    custom_genes: Optional[List[ParamGene]] = None,
    progress_callback: Optional[Callable] = None,
) -> GAResult:
    """
    运行遗传算法优化。

    参数:
        df:               OHLCV DataFrame
        strategy_type:    策略类型
        population_size:  种群大小 (默认30)
        generations:      进化代数 (默认20)
        elite_count:      精英保留数 (默认3)
        crossover_rate:   交叉概率 (默认0.8)
        mutation_rate:    变异概率 (默认0.2)
        tournament_k:     锦标赛选择K值
        objective:        优化目标 (sharpe/return/calmar)
        custom_genes:     自定义参数空间
        progress_callback: fn(pct, msg) 进度回调

    返回: GAResult
    """
    gene_defs = custom_genes or STRATEGY_GENES.get(strategy_type.lower(), [])
    if not gene_defs:
        raise ValueError(f"策略 {strategy_type} 无参数空间定义")

    # ── 初始化种群 ──
    population = [_create_random_individual(gene_defs) for _ in range(population_size)]

    fitness_history = []
    avg_fitness_history = []
    best_ever = Individual(fitness=-999.0)
    total_evals = 0
    convergence_gen = 0
    stagnation = 0  # 停滞计数

    for gen in range(generations):
        if progress_callback:
            progress_callback((gen + 1) / generations * 100, f"第 {gen+1}/{generations} 代")

        # ── 评估适应度 ──
        for ind in population:
            if ind.fitness == -999.0:  # 只评估未评估过的
                _evaluate_fitness(
                    ind, df, strategy_type, ticker,
                    use_atr_stop, board, market_cap_yi, objective,
                )
                total_evals += 1

        # ── 排序 ──
        population.sort(key=lambda x: x.fitness, reverse=True)

        gen_best = population[0].fitness
        valid_fitness = [ind.fitness for ind in population if ind.fitness > -999.0]
        gen_avg = float(np.mean(valid_fitness)) if valid_fitness else -999.0

        fitness_history.append(gen_best)
        avg_fitness_history.append(gen_avg)

        # 更新全局最优
        if population[0].fitness > best_ever.fitness:
            best_ever = Individual(
                genes=dict(population[0].genes),
                fitness=population[0].fitness,
                trades=population[0].trades,
            )
            stagnation = 0
            convergence_gen = gen
        else:
            stagnation += 1

        logger.info(
            f"GA Gen {gen+1}: best={gen_best:.3f} avg={gen_avg:.3f} "
            f"stag={stagnation}"
        )

        # 早停：连续5代无改善
        if stagnation >= 5:
            logger.info(f"GA early stop at generation {gen+1}")
            break

        # ── 生成下一代 ──
        next_pop = []

        # 精英保留
        elites = population[:elite_count]
        for e in elites:
            next_pop.append(Individual(genes=dict(e.genes), fitness=e.fitness, trades=e.trades))

        # 填充剩余
        while len(next_pop) < population_size:
            p1 = _tournament_select(population, tournament_k)
            p2 = _tournament_select(population, tournament_k)

            if random.random() < crossover_rate:
                c1, c2 = _crossover(p1, p2, gene_defs)
            else:
                c1 = Individual(genes=dict(p1.genes))
                c2 = Individual(genes=dict(p2.genes))

            c1 = _mutate(c1, gene_defs, mutation_rate)
            c2 = _mutate(c2, gene_defs, mutation_rate)

            # 新个体需要重新评估
            c1.fitness = -999.0
            c2.fitness = -999.0

            next_pop.append(c1)
            if len(next_pop) < population_size:
                next_pop.append(c2)

        population = next_pop

    return GAResult(
        best_params=best_ever.to_params(),
        best_fitness=round(best_ever.fitness, 4),
        best_trades=best_ever.trades,
        generations=len(fitness_history),
        population_size=population_size,
        fitness_history=fitness_history,
        avg_fitness_history=avg_fitness_history,
        convergence_gen=convergence_gen,
        total_evaluations=total_evals,
    )


# ── 便捷函数：快速 GA 优化 ────────────────────────────────────────────

def quick_ga_optimize(
    df: pd.DataFrame,
    strategy_type: str = "macd",
    ticker: str = "",
    progress_callback: Optional[Callable] = None,
) -> Dict:
    """
    快速 GA 优化，返回结构化字典（供 Streamlit 使用）。
    自动根据数据量调整种群/代数。
    """
    n = len(df)

    if n < 100:
        return {"success": False, "error": f"数据量不足: {n}根K线，GA至少需要100根"}

    # 根据数据量调整参数
    if n >= 500:
        pop, gens = 30, 20
    elif n >= 300:
        pop, gens = 20, 15
    else:
        pop, gens = 15, 10

    try:
        result = run_ga_optimization(
            df, strategy_type=strategy_type, ticker=ticker,
            population_size=pop, generations=gens,
            progress_callback=progress_callback,
        )

        return {
            "success": True,
            "best_params": result.best_params,
            "best_fitness": result.best_fitness,
            "best_trades": result.best_trades,
            "generations": result.generations,
            "population_size": result.population_size,
            "fitness_history": result.fitness_history,
            "avg_fitness_history": result.avg_fitness_history,
            "convergence_gen": result.convergence_gen,
            "total_evaluations": result.total_evaluations,
            "strategy_type": strategy_type,
        }

    except Exception as e:
        return {"success": False, "error": str(e)}


# ── GA vs Grid 对比 ────────────────────────────────────────────────

def compare_ga_vs_grid(
    df: pd.DataFrame,
    strategy_type: str = "macd",
    ticker: str = "",
    progress_callback: Optional[Callable] = None,
) -> Dict:
    """
    GA 和网格搜索的结果对比（评估效率差异）。
    """
    from .walk_forward import _grid_search, DEFAULT_PARAM_GRIDS

    results = {}

    # 1. GA
    ga_result = quick_ga_optimize(df, strategy_type, ticker, progress_callback)
    results["ga"] = ga_result

    # 2. Grid Search
    param_grid = DEFAULT_PARAM_GRIDS.get(strategy_type.lower(), {})
    if param_grid:
        try:
            best_params, best_sharpe = _grid_search(df, strategy_type, param_grid, ticker)
            # 计算网格组合数
            from itertools import product
            combos = 1
            for v in param_grid.values():
                combos *= len(v)

            results["grid"] = {
                "best_params": best_params,
                "best_sharpe": round(best_sharpe, 4),
                "total_evaluations": combos,
            }
        except Exception as e:
            results["grid"] = {"error": str(e)}

    return results
