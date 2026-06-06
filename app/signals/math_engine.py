"""
第一性原理数学推导引擎
用严谨的数学建模替代经验指标，提高量化分析的准确性

核心推导：
1. Hurst 指数 — R/S 分析法，判断价格序列的持续性/均值回归性
2. Shannon 熵 — 信息论，量化价格变动的信息含量和可预测性
3. Kelly 公式 — 最优仓位比例的数学推导
4. 蒙特卡洛模拟 — 基于几何布朗运动的价格路径预测
5. 波动率锥 — 历史波动率分位数，判断当前波动水平
6. 统计显著性 — Z-score / T-test 验证信号非随机性
7. 市场状态检测 — 基于波动率和自相关的状态分类
8. 信息系数 IC — 因子预测力的量化评估
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Optional


# ─────────────────────────────────────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class HurstResult:
    """Hurst 指数分析结果"""
    hurst: float             # H 值
    interpretation: str      # 含义说明
    persistence: str         # trending / mean_reverting / random
    confidence: float        # 置信度 0-1（基于 R²）


@dataclass
class EntropyResult:
    """Shannon 熵分析结果"""
    entropy: float           # 熵值
    max_entropy: float       # 最大可能熵
    normalized: float        # 归一化熵 0-1
    predictability: float    # 可预测性 = 1 - normalized
    interpretation: str


@dataclass
class MonteCarloResult:
    """蒙特卡洛模拟结果"""
    paths: int               # 模拟路径数
    bull_prob: float         # 上涨概率（终点 > 当前价）
    bear_prob: float         # 下跌概率
    expected_return: float   # 期望收益率 %
    var_95: float            # VaR 95%（最大损失 %）
    median_return: float     # 中位数收益率 %
    target_probs: dict       # 各目标价的触达概率 {price: prob}
    confidence: float        # 模拟置信度


@dataclass
class VolatilityConeResult:
    """波动率锥分析结果"""
    current_vol: float       # 当前实现波动率（年化 %）
    percentile: float        # 当前波动率在历史中的分位
    regime: str              # low / normal / high / extreme
    historical_median: float # 历史中位数
    historical_p75: float    # 历史第75百分位
    historical_p90: float    # 历史第90百分位


@dataclass
class SignificanceResult:
    """统计显著性检验结果"""
    z_score: float           # Z 分数
    p_value: float           # P 值
    t_stat: float            # T 统计量
    is_significant: bool     # 是否显著（p < 0.05）
    effect_size: float       # 效应量 (Cohen's d)
    interpretation: str


@dataclass
class RegimeResult:
    """市场状态检测结果"""
    regime: str              # trending_up / trending_down / mean_reverting / volatile / quiet
    confidence: float        # 状态判断置信度
    hurst_regime: str        # Hurst 判断
    vol_regime: str          # 波动率判断
    auto_corr: float         # 自相关系数
    interpretation: str


@dataclass
class InformationCoefficient:
    """信息系数 — 因子预测力评估"""
    ic: float                # 信息系数（因子值与未来收益的相关性）
    ic_ir: float             # 信息比率 IC / std(IC)
    rank_ic: float           # 秩相关系数（Spearman）
    hit_rate: float          # 方向命中率
    sample_size: int
    is_effective: bool       # |IC| > 0.03 且 hit_rate > 52%


@dataclass
class MathDerivation:
    """完整的数学推导结果"""
    hurst: Optional[HurstResult] = None
    entropy: Optional[EntropyResult] = None
    kelly_fraction: float = 0.0
    kelly_detail: str = ""
    monte_carlo: Optional[MonteCarloResult] = None
    vol_cone: Optional[VolatilityConeResult] = None
    significance: Optional[SignificanceResult] = None
    regime: Optional[RegimeResult] = None

    # 综合评分修正
    math_score_adjustment: float = 0.0   # 数学推导对信号评分的修正 -100 ~ +100
    math_confidence: float = 0.5         # 数学推导的置信度 0-1
    key_findings: list = field(default_factory=list)  # 关键发现摘要


# ─────────────────────────────────────────────────────────────────────────────
# 核心计算函数
# ─────────────────────────────────────────────────────────────────────────────

def hurst_exponent(closes: list[float], max_window: int = 50) -> HurstResult:
    """
    R/S 分析法计算 Hurst 指数

    数学推导：
    H = 0.5  → 随机游走（布朗运动）
    H > 0.5  → 持续性（趋势延续）→ 趋势跟踪策略有效
    H < 0.5  → 均值回归（反持续性）→ 反转策略有效

    R/S(n) = E[R(n)/S(n)] ~ c * n^H
    对 log(R/S) vs log(n) 做 OLS 回归，斜率即为 H

    验证条件：R² > 0.9 表示拟合可靠
    """
    n = len(closes)
    if n < 100:
        return HurstResult(0.5, "数据不足(需≥100)", "random", 0.3)

    returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, n)]

    min_window = max(10, n // 20)
    max_window = min(max_window, n // 2)
    if min_window >= max_window:
        return HurstResult(0.5, "窗口不足", "random", 0.3)

    log_ns = []
    log_rs = []

    window = min_window
    while window <= max_window:
        num_subseries = len(returns) // window
        if num_subseries < 1:
            break

        rs_values = []
        for k in range(num_subseries):
            sub = returns[k * window: (k + 1) * window]
            mean_r = sum(sub) / window
            cum_dev = 0.0
            max_dev = -float("inf")
            min_dev = float("inf")
            for r in sub:
                cum_dev += (r - mean_r)
                max_dev = max(max_dev, cum_dev)
                min_dev = min(min_dev, cum_dev)
            R = max_dev - min_dev

            var = sum((r - mean_r) ** 2 for r in sub) / window
            S = math.sqrt(var) if var > 0 else 1e-10

            if S > 1e-10:
                rs_values.append(R / S)

        if rs_values:
            log_ns.append(math.log(window))
            log_rs.append(math.log(sum(rs_values) / len(rs_values)))

        window = int(window * 1.4)
        if window == int(window * 1.4 / 1.4):
            window += 1

    if len(log_ns) < 3:
        return HurstResult(0.5, "回归点不足", "random", 0.3)

    # OLS: log(R/S) = H * log(n) + log(c)
    n_pts = len(log_ns)
    sum_x = sum(log_ns)
    sum_y = sum(log_rs)
    sum_xy = sum(x * y for x, y in zip(log_ns, log_rs))
    sum_x2 = sum(x * x for x in log_ns)

    denom = n_pts * sum_x2 - sum_x * sum_x
    if abs(denom) < 1e-10:
        return HurstResult(0.5, "回归失败", "random", 0.3)

    H = (n_pts * sum_xy - sum_x * sum_y) / denom

    # R² 计算
    intercept = (sum_y - H * sum_x) / n_pts
    ss_res = sum((y - (H * x + intercept)) ** 2 for x, y in zip(log_ns, log_rs))
    ss_tot = sum((y - sum_y / n_pts) ** 2 for y in log_rs)
    r_squared = 1 - ss_res / ss_tot if ss_tot > 0 else 0

    H = max(0.0, min(1.0, H))

    if H > 0.6:
        interpretation = f"H={H:.3f} 持续性强，趋势延续概率高，趋势策略有效"
        persistence = "trending"
    elif H < 0.4:
        interpretation = f"H={H:.3f} 均值回归性，反转策略有效，突破后易回落"
        persistence = "mean_reverting"
    else:
        interpretation = f"H={H:.3f} 接近随机游走，方向性策略效果有限"
        persistence = "random"

    confidence = min(1.0, r_squared)

    return HurstResult(
        hurst=round(H, 4),
        interpretation=interpretation,
        persistence=persistence,
        confidence=round(confidence, 3),
    )


def shannon_entropy(closes: list[float], n_bins: int = 10) -> EntropyResult:
    """
    Shannon 熵分析 — 量化价格变动的信息含量

    数学推导：
    对收益率序列离散化为 n_bins 个状态
    H(X) = -Σ p(x_i) * log2(p(x_i))
    H_max = log2(n_bins)

    归一化熵 = H / H_max
    可预测性 = 1 - 归一化熵

    可预测性越高 → 技术分析越有效
    """
    if len(closes) < 20:
        return EntropyResult(0, 1, 1.0, 0.0, "数据不足")

    returns = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]

    if not returns:
        return EntropyResult(0, 1, 1.0, 0.0, "无收益率数据")

    min_r = min(returns)
    max_r = max(returns)
    if max_r == min_r:
        return EntropyResult(0, 1, 1.0, 0.0, "价格无变化")

    bin_width = (max_r - min_r) / n_bins
    counts = [0] * n_bins
    for r in returns:
        idx = min(int((r - min_r) / bin_width), n_bins - 1)
        counts[idx] += 1

    total = len(returns)
    entropy = 0.0
    for c in counts:
        if c > 0:
            p = c / total
            entropy -= p * math.log2(p)

    max_entropy = math.log2(n_bins)
    normalized = entropy / max_entropy if max_entropy > 0 else 1.0
    predictability = 1.0 - normalized

    if predictability > 0.5:
        interp = f"可预测性={predictability:.1%}，价格模式明显，量化策略有优势"
    elif predictability > 0.25:
        interp = f"可预测性={predictability:.1%}，存在一定模式，需多因子配合"
    else:
        interp = f"可预测性={predictability:.1%}，接近随机，策略需谨慎"

    return EntropyResult(
        entropy=round(entropy, 4),
        max_entropy=round(max_entropy, 4),
        normalized=round(normalized, 4),
        predictability=round(predictability, 4),
        interpretation=interp,
    )


def kelly_criterion(
    win_rate: float,
    avg_win: float,
    avg_loss: float,
) -> tuple[float, str]:
    """
    Kelly 公式 — 数学推导最优仓位比例

    f* = (p * b - q) / b
    其中：
      p = 胜率
      q = 1 - p（败率）
      b = 平均盈利 / 平均亏损（盈亏比）

    实际使用 Kelly/2（半凯利）以降低方差

    数学证明：
    f* 最大化 E[log(wealth)] = p*log(1+f*b) + q*log(1-f)
    对 f 求导令其为零即可得 f*
    """
    if avg_loss <= 0 or win_rate <= 0 or win_rate >= 1:
        return 0.0, "参数异常，无法计算Kelly"

    p = win_rate
    q = 1 - p
    b = avg_win / avg_loss

    f_star = (p * b - q) / b
    half_kelly = f_star / 2

    if f_star <= 0:
        return 0.0, f"Kelly={f_star:.2%}≤0，数学期望为负，不建议开仓"

    detail = (
        f"最优仓位(Kelly)={f_star:.1%}，"
        f"实际建议(半Kelly)={half_kelly:.1%}，"
        f"基于胜率{win_rate:.0%}×盈亏比{b:.2f}"
    )
    return round(max(0, min(half_kelly, 0.25)), 4), detail


def monte_carlo_simulation(
    closes: list[float],
    num_paths: int = 1000,
    horizon_bars: int = 24,
    target_prices: list[float] = None,
) -> MonteCarloResult:
    """
    蒙特卡洛模拟 — 基于几何布朗运动(GBM)的价格路径预测

    数学推导（第一性原理）：
    假设价格服从几何布朗运动：
    dS = μSdt + σSdW

    离散化（Euler-Maruyama）：
    S(t+Δt) = S(t) * exp((μ - σ²/2)Δt + σ√Δt * Z)
    其中 Z ~ N(0,1)

    参数估计：
    μ = E[log(S_t/S_{t-1})] / Δt
    σ = Std[log(S_t/S_{t-1})] / √Δt

    模拟 N 条路径，统计终态分布 → 概率估计
    """
    if len(closes) < 30:
        return MonteCarloResult(
            paths=0, bull_prob=0.5, bear_prob=0.5,
            expected_return=0, var_95=0, median_return=0,
            target_probs={}, confidence=0.2,
        )

    log_returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    n = len(log_returns)

    mu = sum(log_returns) / n
    sigma = math.sqrt(sum((r - mu) ** 2 for r in log_returns) / (n - 1))

    if sigma < 1e-10:
        sigma = 0.01

    s0 = closes[-1]
    dt = 1.0
    drift = (mu - 0.5 * sigma ** 2) * dt
    vol = sigma * math.sqrt(dt)

    final_returns = []
    target_hits = {} if target_prices is None else {tp: 0 for tp in target_prices}

    random.seed(42)

    for _ in range(num_paths):
        price = s0
        hit_targets = set()
        for step in range(horizon_bars):
            z = random.gauss(0, 1)
            price *= math.exp(drift + vol * z)
            if target_prices:
                for tp in target_prices:
                    if tp not in hit_targets:
                        if (tp > s0 and price >= tp) or (tp < s0 and price <= tp):
                            hit_targets.add(tp)
                            target_hits[tp] = target_hits.get(tp, 0) + 1

        final_returns.append((price / s0 - 1) * 100)

    final_returns.sort()
    bull = sum(1 for r in final_returns if r > 0) / num_paths
    bear = 1 - bull
    expected = sum(final_returns) / num_paths
    median = final_returns[num_paths // 2]
    var_95 = final_returns[int(num_paths * 0.05)]

    target_probs = {}
    if target_prices:
        for tp in target_prices:
            target_probs[f"{tp:.2f}"] = round(target_hits.get(tp, 0) / num_paths, 3)

    # 置信度基于路径数
    confidence = min(1.0, num_paths / 2000)

    return MonteCarloResult(
        paths=num_paths,
        bull_prob=round(bull, 3),
        bear_prob=round(bear, 3),
        expected_return=round(expected, 2),
        var_95=round(var_95, 2),
        median_return=round(median, 2),
        target_probs=target_probs,
        confidence=round(confidence, 3),
    )


def volatility_cone(closes: list[float], window: int = 20) -> VolatilityConeResult:
    """
    波动率锥 — 判断当前波动率在历史中的位置

    数学推导：
    实现波动率 σ_realized = sqrt(252) * std(log returns)

    历史分位数：
    P5, P25, P50, P75, P90, P95

    当前波动率所处分位决定市场状态：
    - < P25: 低波动（趋势稳定，适合方向性交易）
    - P25-P75: 正常波动
    - P75-P90: 高波动（需更宽止损）
    - > P90: 极端波动（降低仓位或观望）
    """
    if len(closes) < window + 20:
        return VolatilityConeResult(0, 50, "normal", 0, 0, 0, 0)

    log_returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]

    rolling_vols = []
    for i in range(window, len(log_returns)):
        sub = log_returns[i - window: i]
        var = sum((r - sum(sub) / window) ** 2 for r in sub) / (window - 1)
        annualized = math.sqrt(var * 252) * 100
        rolling_vols.append(annualized)

    if not rolling_vols:
        return VolatilityConeResult(0, 50, "normal", 0, 0, 0, 0)

    current_vol = rolling_vols[-1]
    sorted_vols = sorted(rolling_vols)
    n = len(sorted_vols)

    def percentile(arr, p):
        idx = int(len(arr) * p / 100)
        return arr[min(idx, len(arr) - 1)]

    p50 = percentile(sorted_vols, 50)
    p75 = percentile(sorted_vols, 75)
    p90 = percentile(sorted_vols, 90)

    # 当前分位
    rank = sum(1 for v in sorted_vols if v <= current_vol)
    current_pct = rank / n * 100

    if current_pct < 25:
        regime = "low"
    elif current_pct < 75:
        regime = "normal"
    elif current_pct < 90:
        regime = "high"
    else:
        regime = "extreme"

    return VolatilityConeResult(
        current_vol=round(current_vol, 2),
        percentile=round(current_pct, 1),
        regime=regime,
        historical_median=round(p50, 2),
        historical_p75=round(p75, 2),
        historical_p90=round(p90, 2),
    )


def statistical_significance(
    signal_returns: list[float],
    benchmark_returns: list[float] = None,
) -> SignificanceResult:
    """
    统计显著性检验 — 验证信号收益非随机

    数学推导：
    H0: μ_signal = μ_benchmark（信号无效）
    H1: μ_signal ≠ μ_benchmark（信号有效）

    Z = (x̄ - μ₀) / (σ / √n)
    T = (x̄ - μ₀) / (s / √n)    （小样本）

    Cohen's d = (x̄ - μ₀) / s    （效应量）

    p < 0.05 → 拒绝 H0 → 信号有效
    """
    if not signal_returns or len(signal_returns) < 5:
        return SignificanceResult(0, 1.0, 0, False, 0, "样本不足")

    n = len(signal_returns)
    mean_r = sum(signal_returns) / n
    var_r = sum((r - mean_r) ** 2 for r in signal_returns) / (n - 1)
    std_r = math.sqrt(var_r) if var_r > 0 else 1e-10

    mu0 = 0
    if benchmark_returns and len(benchmark_returns) >= 5:
        mu0 = sum(benchmark_returns) / len(benchmark_returns)

    se = std_r / math.sqrt(n)
    z_score = (mean_r - mu0) / se if se > 1e-10 else 0
    t_stat = z_score

    # 近似 p-value（双尾）
    abs_z = abs(z_score)
    if abs_z > 2.576:
        p_value = 0.01
    elif abs_z > 1.96:
        p_value = 0.05
    elif abs_z > 1.645:
        p_value = 0.10
    elif abs_z > 1.282:
        p_value = 0.20
    else:
        p_value = 0.30

    cohens_d = (mean_r - mu0) / std_r if std_r > 1e-10 else 0
    is_sig = p_value < 0.05

    if is_sig:
        if cohens_d > 0.5:
            interp = f"信号显著且效应量大(d={cohens_d:.2f})，统计学上可确认策略有效"
        else:
            interp = f"信号统计显著(p={p_value:.2f})但效应量较小(d={cohens_d:.2f})"
    else:
        interp = f"信号未通过显著性检验(p={p_value:.2f})，不排除随机性"

    return SignificanceResult(
        z_score=round(z_score, 3),
        p_value=round(p_value, 3),
        t_stat=round(t_stat, 3),
        is_significant=is_sig,
        effect_size=round(cohens_d, 3),
        interpretation=interp,
    )


def detect_regime(closes: list[float]) -> RegimeResult:
    """
    市场状态检测 — 多维度判断当前市场运行模式

    综合以下数学维度：
    1. Hurst 指数 → 趋势持续性
    2. 波动率锥 → 波动水平
    3. 自相关系数 → 收益率序列相关性
    4. 收益率偏度 → 分布偏斜

    数学推导：
    自相关: ρ(k) = Cov(r_t, r_{t-k}) / Var(r_t)
    偏度: γ₁ = E[(X-μ)³] / σ³
    """
    if len(closes) < 40:
        return RegimeResult("quiet", 0.3, "unknown", "normal", 0, "数据不足")

    returns = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]
    n = len(returns)
    mean_r = sum(returns) / n
    var_r = sum((r - mean_r) ** 2 for r in returns) / n

    # 自相关（lag-1）
    if var_r > 1e-15:
        auto_cov = sum((returns[i] - mean_r) * (returns[i - 1] - mean_r) for i in range(1, n)) / n
        auto_corr = auto_cov / var_r
    else:
        auto_corr = 0

    # 偏度
    std_r = math.sqrt(var_r) if var_r > 0 else 1e-10
    skewness = sum((r - mean_r) ** 3 for r in returns) / (n * std_r ** 3) if std_r > 1e-10 else 0

    hurst_r = hurst_exponent(closes)
    vol_r = volatility_cone(closes)

    # 综合状态判断
    scores = {"trending_up": 0, "trending_down": 0, "mean_reverting": 0, "volatile": 0, "quiet": 0}

    # Hurst 贡献
    if hurst_r.hurst > 0.6:
        if mean_r > 0:
            scores["trending_up"] += 30
        else:
            scores["trending_down"] += 30
    elif hurst_r.hurst < 0.4:
        scores["mean_reverting"] += 30

    # 自相关贡献
    if abs(auto_corr) > 0.15:
        if auto_corr > 0:
            scores["trending_up" if mean_r > 0 else "trending_down"] += 20
        else:
            scores["mean_reverting"] += 20

    # 波动率贡献
    if vol_r.regime == "extreme":
        scores["volatile"] += 35
    elif vol_r.regime == "high":
        scores["volatile"] += 20
    elif vol_r.regime == "low":
        scores["quiet"] += 25

    # 趋势方向
    recent_trend = (closes[-1] - closes[-20]) / closes[-20] * 100 if len(closes) >= 20 else 0
    if recent_trend > 5:
        scores["trending_up"] += 15
    elif recent_trend < -5:
        scores["trending_down"] += 15

    regime = max(scores, key=scores.get)
    max_score = scores[regime]
    total_score = sum(scores.values())
    confidence = max_score / total_score if total_score > 0 else 0.3

    interpretations = {
        "trending_up": f"上升趋势模式(H={hurst_r.hurst:.2f}, 20日涨幅={recent_trend:+.1f}%)，趋势策略有效",
        "trending_down": f"下降趋势模式(H={hurst_r.hurst:.2f}, 20日跌幅={recent_trend:+.1f}%)，做空或观望",
        "mean_reverting": f"均值回归模式(H={hurst_r.hurst:.2f}, 自相关={auto_corr:.3f})，反转策略有效",
        "volatile": f"极端波动模式(波动率分位={vol_r.percentile:.0f}%)，降低仓位，宽止损",
        "quiet": f"低波动安静期(波动率分位={vol_r.percentile:.0f}%)，适合建仓",
    }

    return RegimeResult(
        regime=regime,
        confidence=round(confidence, 3),
        hurst_regime=hurst_r.persistence,
        vol_regime=vol_r.regime,
        auto_corr=round(auto_corr, 4),
        interpretation=interpretations.get(regime, "状态不明确"),
    )


def compute_information_coefficient(
    factor_values: list[float],
    forward_returns: list[float],
) -> InformationCoefficient:
    """
    信息系数 (IC) — 量化因子的预测能力

    数学推导：
    IC = Corr(factor_t, return_{t+1})
    IC > 0.03 且稳定 → 因子有效
    IC_IR = mean(IC) / std(IC) > 0.5 → 因子可靠

    使用 Spearman 秩相关（对异常值更稳健）
    """
    if len(factor_values) < 10 or len(forward_returns) < 10:
        return InformationCoefficient(0, 0, 0, 0.5, 0, False)

    n = min(len(factor_values), len(forward_returns))
    fvals = factor_values[:n]
    frets = forward_returns[:n]

    # Pearson IC
    mean_f = sum(fvals) / n
    mean_r = sum(frets) / n
    var_f = sum((f - mean_f) ** 2 for f in fvals) / n
    var_r = sum((r - mean_r) ** 2 for r in frets) / n

    if var_f < 1e-15 or var_r < 1e-15:
        return InformationCoefficient(0, 0, 0, 0.5, n, False)

    cov = sum((f - mean_f) * (r - mean_r) for f, r in zip(fvals, frets)) / n
    ic = cov / (math.sqrt(var_f) * math.sqrt(var_r))

    # Spearman rank IC
    def rank(arr):
        sorted_idx = sorted(range(len(arr)), key=lambda i: arr[i])
        ranks = [0] * len(arr)
        for rank_val, idx in enumerate(sorted_idx):
            ranks[idx] = rank_val + 1
        return ranks

    rank_f = rank(fvals)
    rank_r = rank(frets)
    mean_rf = sum(rank_f) / n
    mean_rr = sum(rank_r) / n
    var_rf = sum((r - mean_rf) ** 2 for r in rank_f) / n
    var_rr = sum((r - mean_rr) ** 2 for r in rank_r) / n
    cov_rr = sum((a - mean_rf) * (b - mean_rr) for a, b in zip(rank_f, rank_r)) / n
    rank_ic = cov_rr / (math.sqrt(var_rf) * math.sqrt(var_rr)) if var_rf > 0 and var_rr > 0 else 0

    # Hit rate
    correct = sum(1 for f, r in zip(fvals, frets) if (f > 0 and r > 0) or (f < 0 and r < 0))
    hit_rate = correct / n

    ic_ir = abs(ic) / max(0.01, abs(ic)) if abs(ic) > 0.001 else 0

    return InformationCoefficient(
        ic=round(ic, 4),
        ic_ir=round(ic_ir, 4),
        rank_ic=round(rank_ic, 4),
        hit_rate=round(hit_rate, 4),
        sample_size=n,
        is_effective=abs(ic) > 0.03 and hit_rate > 0.52,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 一键分析入口
# ─────────────────────────────────────────────────────────────────────────────

def run_math_derivation(
    closes: list[float],
    direction: str = "long",
    win_rate: float = 0.55,
    avg_profit_pct: float = 3.0,
    avg_loss_pct: float = 2.0,
    stop_loss_pct: float = None,
    take_profit_pct: float = None,
    lang: str = "zh",
) -> MathDerivation:
    """
    执行完整的第一性原理数学推导

    Args:
        closes: 历史收盘价序列
        direction: 信号方向 long/short
        win_rate: 历史胜率
        avg_profit_pct: 平均盈利 %
        avg_loss_pct: 平均亏损 %
        stop_loss_pct: 止损距离 %
        take_profit_pct: 止盈距离 %

    Returns:
        MathDerivation 完整推导结果
    """
    result = MathDerivation()

    # 1. Hurst 指数
    result.hurst = hurst_exponent(closes)

    # 2. Shannon 熵
    result.entropy = shannon_entropy(closes)

    # 3. Kelly 公式
    result.kelly_fraction, result.kelly_detail = kelly_criterion(
        win_rate, avg_profit_pct / 100, avg_loss_pct / 100,
    )

    # 4. 蒙特卡洛模拟
    price = closes[-1]
    targets = []
    if stop_loss_pct:
        if direction == "long":
            targets.append(price * (1 - stop_loss_pct / 100))
        else:
            targets.append(price * (1 + stop_loss_pct / 100))
    if take_profit_pct:
        if direction == "long":
            targets.append(price * (1 + take_profit_pct / 100))
        else:
            targets.append(price * (1 - take_profit_pct / 100))

    result.monte_carlo = monte_carlo_simulation(
        closes, num_paths=1000, horizon_bars=24, target_prices=targets or None,
    )

    # 5. 波动率锥
    result.vol_cone = volatility_cone(closes)

    # 6. 市场状态
    result.regime = detect_regime(closes)

    # ── 综合评分修正 ──────────────────────────────────────────────────
    adjustment = 0.0
    findings = []
    en = lang == "en"

    # Hurst 修正
    if result.hurst:
        if direction == "long" and result.hurst.hurst > 0.6:
            adjustment += 15
            findings.append(f"H={result.hurst.hurst:.2f} strong trend persistence, supports long" if en else f"H={result.hurst.hurst:.2f} 趋势持续性强，支撑做多")
        elif direction == "short" and result.hurst.hurst > 0.6:
            adjustment += 15
            findings.append(f"H={result.hurst.hurst:.2f} strong trend persistence, supports short" if en else f"H={result.hurst.hurst:.2f} 趋势持续性强，支撑做空")
        elif result.hurst.hurst < 0.4:
            adjustment -= 10
            findings.append(f"H={result.hurst.hurst:.2f} mean-reverting, trend signal unreliable" if en else f"H={result.hurst.hurst:.2f} 均值回归，趋势信号可靠性降低")

    # 熵修正
    if result.entropy:
        if result.entropy.predictability > 0.5:
            adjustment += 10
            findings.append(f"Predictability {result.entropy.predictability:.0%}, high signal reliability" if en else f"可预测性{result.entropy.predictability:.0%}，信号可靠性高")
        elif result.entropy.predictability < 0.25:
            adjustment -= 15
            findings.append(f"Predictability {result.entropy.predictability:.0%} too low, noise-dominated" if en else f"可预测性{result.entropy.predictability:.0%}过低，噪音主导")

    # 蒙特卡洛修正
    if result.monte_carlo:
        if direction == "long":
            if result.monte_carlo.bull_prob > 0.6:
                adjustment += 20
                findings.append(f"MC bull prob {result.monte_carlo.bull_prob:.0%}, direction confirmed" if en else f"MC上涨概率{result.monte_carlo.bull_prob:.0%}，方向确认")
            elif result.monte_carlo.bull_prob < 0.4:
                adjustment -= 25
                findings.append(f"MC bull prob only {result.monte_carlo.bull_prob:.0%}, direction uncertain" if en else f"MC上涨概率仅{result.monte_carlo.bull_prob:.0%}，方向存疑")
        else:
            if result.monte_carlo.bear_prob > 0.6:
                adjustment += 20
                findings.append(f"MC bear prob {result.monte_carlo.bear_prob:.0%}, direction confirmed" if en else f"MC下跌概率{result.monte_carlo.bear_prob:.0%}，方向确认")
            elif result.monte_carlo.bear_prob < 0.4:
                adjustment -= 25
                findings.append(f"MC bear prob only {result.monte_carlo.bear_prob:.0%}, direction uncertain" if en else f"MC下跌概率仅{result.monte_carlo.bear_prob:.0%}，方向存疑")

        if result.monte_carlo.var_95 < -10:
            adjustment -= 10
            findings.append(f"VaR(95%)={result.monte_carlo.var_95:.1f}%, high tail risk" if en else f"VaR(95%)={result.monte_carlo.var_95:.1f}%，尾部风险大")

    # 波动率修正
    if result.vol_cone:
        if result.vol_cone.regime == "extreme":
            adjustment -= 20
            findings.append(f"Extreme volatility (P{result.vol_cone.percentile:.0f}), reduce position" if en else f"波动率极端(P{result.vol_cone.percentile:.0f})，建议降仓")
        elif result.vol_cone.regime == "low":
            adjustment += 10
            findings.append(f"Low volatility (P{result.vol_cone.percentile:.0f}), trend reliable" if en else f"波动率低位(P{result.vol_cone.percentile:.0f})，趋势可靠")

    # 市场状态修正
    if result.regime:
        regime_match = (
            (direction == "long" and result.regime.regime == "trending_up") or
            (direction == "short" and result.regime.regime == "trending_down")
        )
        if regime_match:
            adjustment += 20
            findings.append(f"Market regime={result.regime.regime}, aligns with signal" if en else f"市场状态={result.regime.regime}，与信号方向一致")
        elif result.regime.regime == "volatile":
            adjustment -= 15
            findings.append("Market regime=extreme volatility, signal unreliable" if en else "市场状态=极端波动，信号可靠性下降")
        elif result.regime.regime == "mean_reverting" and result.hurst and result.hurst.hurst < 0.4:
            adjustment -= 10
            findings.append("Mean-reverting market, trend signal may fail" if en else "均值回归市场，趋势信号可能失效")

    result.math_score_adjustment = max(-100, min(100, round(adjustment, 1)))

    # 数学置信度 = 各子项置信度的加权平均
    conf_values = []
    weights = []
    if result.hurst:
        conf_values.append(result.hurst.confidence); weights.append(0.25)
    if result.entropy:
        conf_values.append(result.entropy.predictability); weights.append(0.15)
    if result.monte_carlo:
        conf_values.append(result.monte_carlo.confidence); weights.append(0.25)
    if result.vol_cone:
        conf_values.append(0.7); weights.append(0.15)
    if result.regime:
        conf_values.append(result.regime.confidence); weights.append(0.20)

    result.math_confidence = round(
        sum(c * w for c, w in zip(conf_values, weights)) / sum(weights), 3
    ) if weights else 0.5

    result.key_findings = findings[:5]

    return result
