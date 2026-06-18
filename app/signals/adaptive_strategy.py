"""
自适应策略引擎 — 持续自我迭代验证，自动调优胜率

核心机制：
1. 贝叶斯权重更新 — 每次信号结算后更新信号源权重
2. 因子IC追踪 — 持续评估各因子的预测能力
3. 市场状态自适应 — 不同市场状态下使用不同的策略参数
4. 参数网格搜索 — 定期搜索更优的阈值组合
5. 胜率衰减 — 近期表现权重高于远期
6. 策略版本管理 — 记录每次迭代的变化
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field, asdict
from typing import Optional, Dict, List, Any
from pathlib import Path

from config.settings import settings


# ─────────────────────────────────────────────────────────────────────────────
# 配置常量
# ─────────────────────────────────────────────────────────────────────────────

# 策略快照存储路径（JSON 文件，无需额外数据库）
STRATEGY_FILE = Path(__file__).parent / "strategy_state.json"

# 默认权重（会被贝叶斯更新覆盖）
DEFAULT_WEIGHTS = {
    "bigorder_anomaly": 0.35,
    "quantitative": 0.35,
    "technical": 0.30,
}

# 市场状态 → 策略参数预设
REGIME_PRESETS = {
    "trending_up": {
        "weight_boost": {"technical": 1.2, "quantitative": 1.1},
        "stop_loss_atr_mult": 1.5,
        "tp_atr_mult": 3.0,
        "min_confidence": 40,
    },
    "trending_down": {
        "weight_boost": {"technical": 1.2, "quantitative": 1.1},
        "stop_loss_atr_mult": 1.5,
        "tp_atr_mult": 2.5,
        "min_confidence": 45,
    },
    "mean_reverting": {
        "weight_boost": {"bigorder_anomaly": 1.3, "quantitative": 1.1},
        "stop_loss_atr_mult": 2.0,
        "tp_atr_mult": 2.0,
        "min_confidence": 55,
    },
    "volatile": {
        "weight_boost": {"bigorder_anomaly": 1.2},
        "stop_loss_atr_mult": 2.5,
        "tp_atr_mult": 2.0,
        "min_confidence": 60,
    },
    "quiet": {
        "weight_boost": {"technical": 1.1},
        "stop_loss_atr_mult": 1.2,
        "tp_atr_mult": 3.5,
        "min_confidence": 35,
    },
}

# 贝叶斯先验参数
BAYESIAN_PRIOR_ALPHA = 10.0   # 先验伪计数（越大约束越强，变化越慢）
BAYESIAN_PRIOR_BETA = 10.0
LEARNING_RATE = 0.15           # 学习率（权重每次更新幅度）
DECAY_HALFLIFE_DAYS = 30       # 半衰期（远期结果权重递减）
MIN_OBSERVATIONS = 5           # 最少观测数才更新权重


# ─────────────────────────────────────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class FactorPerformance:
    """单个因子/信号源的历史表现追踪"""
    name: str
    total_signals: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl_pct: float = 0.0
    recent_results: list = field(default_factory=list)  # 最近100条 [(timestamp, pnl_pct)]
    last_updated: float = 0.0

    @property
    def win_rate(self) -> float:
        return self.wins / self.total_signals if self.total_signals > 0 else 0.5

    @property
    def avg_pnl(self) -> float:
        return self.total_pnl_pct / self.total_signals if self.total_signals > 0 else 0.0

    def record(self, pnl_pct: float, ts: float = None):
        """记录一次信号结果"""
        ts = ts or time.time()
        self.total_signals += 1
        self.total_pnl_pct += pnl_pct
        if pnl_pct > 0:
            self.wins += 1
        else:
            self.losses += 1
        self.recent_results.append((ts, pnl_pct))
        if len(self.recent_results) > 100:
            self.recent_results = self.recent_results[-100:]
        self.last_updated = ts

    def decayed_win_rate(self) -> float:
        """带时间衰减的近期胜率"""
        if not self.recent_results:
            return 0.5
        now = time.time()
        decay_lambda = math.log(2) / (DECAY_HALFLIFE_DAYS * 86400)
        weighted_wins = 0.0
        weighted_total = 0.0
        for ts, pnl in self.recent_results:
            w = math.exp(-decay_lambda * (now - ts))
            weighted_total += w
            if pnl > 0:
                weighted_wins += w
        return weighted_wins / weighted_total if weighted_total > 0 else 0.5


@dataclass
class StrategyState:
    """策略完整状态（持久化到 JSON）

    version 语义：
      1 = v1 信号策略时代（legacy bigorder_anomaly）
      2 = v2 信号策略时代（时间衰减大单 + 吸筹 pattern，2026-06-18 全量上线）
      后续每次 evolution 递增
    """
    version: int = 2
    weights: Dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    factor_performances: Dict[str, dict] = field(default_factory=dict)
    regime: str = "quiet"
    regime_updated_at: float = 0.0
    total_signals_generated: int = 0
    total_signals_settled: int = 0
    global_win_rate: float = 0.5
    evolution_history: list = field(default_factory=list)  # 演化日志
    last_evolution_at: float = 0.0
    # 按币种累加的胜率 {coin: {win_rate, sample_count, wins, total_pnl, last_updated}}
    coin_winrates: Dict[str, dict] = field(default_factory=dict)

    def get_factor_perf(self, name: str) -> FactorPerformance:
        if name not in self.factor_performances:
            self.factor_performances[name] = {
                "name": name, "total_signals": 0, "wins": 0,
                "losses": 0, "total_pnl_pct": 0.0,
                "recent_results": [], "last_updated": 0.0,
            }
        d = self.factor_performances[name]
        fp = FactorPerformance(
            name=d["name"], total_signals=d.get("total_signals", 0),
            wins=d.get("wins", 0), losses=d.get("losses", 0),
            total_pnl_pct=d.get("total_pnl_pct", 0.0),
            recent_results=d.get("recent_results", []),
            last_updated=d.get("last_updated", 0.0),
        )
        return fp


# ─────────────────────────────────────────────────────────────────────────────
# 自适应策略引擎
# ─────────────────────────────────────────────────────────────────────────────

class AdaptiveStrategyEngine:
    """
    自适应策略引擎

    工作流：
    1. 生成信号时 → 根据当前市场状态调整权重
    2. 信号结算时 → 记录结果，触发贝叶斯更新
    3. 定期演化 → 评估各因子IC，搜索更优参数
    4. 自动升级 → 当检测到策略退化时自动调整
    """

    def __init__(self):
        self._state: Optional[StrategyState] = None

    @property
    def state(self) -> StrategyState:
        if self._state is None:
            self._state = self._load_state()
        return self._state

    # ── 持久化 ────────────────────────────────────────────────────────

    def _load_state(self) -> StrategyState:
        """从 JSON 文件加载策略状态"""
        if STRATEGY_FILE.exists():
            try:
                data = json.loads(STRATEGY_FILE.read_text())
                state = StrategyState(**{k: v for k, v in data.items()
                                        if k in StrategyState.__dataclass_fields__})
                # 迁移：v1 信号策略时代的 state（version<2 且未 evolution）自动升到 v2
                # evolution_history 非空说明已经迭代过，保留原 version
                if state.version < 2 and not state.evolution_history:
                    state.version = 2
                return state
            except Exception:
                pass
        return StrategyState()

    def _save_state(self):
        """持久化策略状态"""
        try:
            STRATEGY_FILE.write_text(json.dumps(asdict(self.state), indent=2, ensure_ascii=False))
        except Exception:
            pass

    # ── 信号生成时的权重计算 ──────────────────────────────────────────

    def get_adaptive_weights(self, regime: str = None) -> Dict[str, float]:
        """
        获取当前自适应权重

        逻辑：
        1. 基础权重 = 贝叶斯后验权重
        2. 市场状态修正 = regime preset 中的 boost
        3. 归一化
        """
        base = dict(self.state.weights)
        regime = regime or self.state.regime

        preset = REGIME_PRESETS.get(regime, {})
        boosts = preset.get("weight_boost", {})

        adjusted = {}
        for name, w in base.items():
            boost = boosts.get(name, 1.0)
            adjusted[name] = w * boost

        # 归一化
        total = sum(adjusted.values())
        if total > 0:
            adjusted = {k: round(v / total, 4) for k, v in adjusted.items()}

        return adjusted

    def get_regime_params(self, regime: str = None) -> dict:
        """获取当前市场状态下的策略参数"""
        regime = regime or self.state.regime
        return REGIME_PRESETS.get(regime, REGIME_PRESETS["quiet"])

    # ── 信号结算后的学习 ──────────────────────────────────────────────

    def record_signal_result(
        self,
        source_name: str,
        pnl_pct: float,
        direction_correct: bool,
        ts: float = None,
        batch: bool = False,
    ):
        """
        记录信号结算结果

        Args:
            batch: True=只记录数据不调权（周期复盘用），False=即时调权

        贝叶斯更新公式：
        P(win|data) = (α + wins) / (α + β + total)
        其中 α, β 是先验伪计数

        权重调整：
        new_weight = old_weight + learning_rate * (new_bayesian_wr - 0.5)
        """
        ts = ts or time.time()

        # 更新因子表现
        if source_name not in self.state.factor_performances:
            self.state.factor_performances[source_name] = {
                "name": source_name, "total_signals": 0, "wins": 0,
                "losses": 0, "total_pnl_pct": 0.0,
                "recent_results": [], "last_updated": 0.0,
            }

        fp = self.state.factor_performances[source_name]
        fp["total_signals"] = fp.get("total_signals", 0) + 1
        fp["total_pnl_pct"] = fp.get("total_pnl_pct", 0) + pnl_pct
        if pnl_pct > 0:
            fp["wins"] = fp.get("wins", 0) + 1
        else:
            fp["losses"] = fp.get("losses", 0) + 1

        results = fp.get("recent_results", [])
        results.append([ts, round(pnl_pct, 4)])
        fp["recent_results"] = results[-100:]
        fp["last_updated"] = ts

        self.state.total_signals_settled += 1

        # 非批量模式：即时更新权重
        # 批量模式：只记录，等 evolve() 时统一调权
        if not batch:
            self._bayesian_update(source_name)

        # 更新全局胜率
        total = self.state.total_signals_settled
        total_wins = sum(
            fp.get("wins", 0) for fp in self.state.factor_performances.values()
        )
        self.state.global_win_rate = total_wins / total if total > 0 else 0.5

        self._save_state()

    def _bayesian_update(self, source_name: str):
        """
        贝叶斯权重更新

        P(wins|data) = (α + wins) / (α + β + total)

        相比先验 0.5 的偏离程度决定权重调整方向和幅度
        """
        fp = self.state.factor_performances.get(source_name)
        if not fp or fp.get("total_signals", 0) < MIN_OBSERVATIONS:
            return

        wins = fp.get("wins", 0)
        total = fp.get("total_signals", 0)

        # 贝叶斯后验胜率
        bayesian_wr = (BAYESIAN_PRIOR_ALPHA + wins) / (BAYESIAN_PRIOR_ALPHA + BAYESIAN_PRIOR_BETA + total)

        # 衰减胜率（更重视近期）
        recent = fp.get("recent_results", [])
        if recent:
            now = time.time()
            decay_lambda = math.log(2) / (DECAY_HALFLIFE_DAYS * 86400)
            weighted_wins = 0.0
            weighted_total = 0.0
            for ts, pnl in recent:
                w = math.exp(-decay_lambda * (now - ts))
                weighted_total += w
                if pnl > 0:
                    weighted_wins += w
            decayed_wr = weighted_wins / weighted_total if weighted_total > 0 else 0.5
        else:
            decayed_wr = 0.5

        # 综合胜率（贝叶斯 60% + 近期衰减 40%）
        combined_wr = bayesian_wr * 0.6 + decayed_wr * 0.4

        # 权重调整
        old_weight = self.state.weights.get(source_name, DEFAULT_WEIGHTS.get(source_name, 0.3))
        adjustment = LEARNING_RATE * (combined_wr - 0.5)
        new_weight = max(0.10, min(0.60, old_weight + adjustment))

        self.state.weights[source_name] = round(new_weight, 4)

    # ── 策略演化 ──────────────────────────────────────────────────────

    def evolve(self, ohlcv_data: dict = None) -> dict:
        """
        策略演化 — 评估并优化策略参数

        步骤：
        1. 评估各因子当前 IC（如果有足够数据）
        2. 检测策略退化（胜率持续下降）
        3. 参数网格搜索（阈值微调）
        4. 记录演化日志

        Returns:
            演化报告
        """
        report = {
            "version_before": self.state.version,
            "actions": [],
            "weight_changes": {},
            "performance_summary": {},
        }

        # 1. 评估各因子表现
        for name, fp_dict in self.state.factor_performances.items():
            fp = self.state.get_factor_perf(name)
            if fp.total_signals < MIN_OBSERVATIONS:
                continue

            report["performance_summary"][name] = {
                "win_rate": round(fp.win_rate, 3),
                "decayed_wr": round(fp.decayed_win_rate(), 3),
                "avg_pnl": round(fp.avg_pnl, 3),
                "total": fp.total_signals,
            }

            # 检测退化：近期胜率显著低于整体胜率
            if fp.total_signals >= 10:
                decayed = fp.decayed_win_rate()
                overall = fp.win_rate
                if decayed < overall - 0.10:
                    # 退化检测到 → 降低权重
                    old_w = self.state.weights.get(name, 0.3)
                    new_w = max(0.10, old_w * 0.85)
                    self.state.weights[name] = round(new_w, 4)
                    report["actions"].append(
                        f"因子{name}退化（近期胜率{decayed:.0%} < 整体{overall:.0%}），权重 {old_w:.3f} → {new_w:.3f}"
                    )
                    report["weight_changes"][name] = {"old": old_w, "new": new_w}

                # 检测优异表现
                elif decayed > overall + 0.10:
                    old_w = self.state.weights.get(name, 0.3)
                    new_w = min(0.60, old_w * 1.10)
                    self.state.weights[name] = round(new_w, 4)
                    report["actions"].append(
                        f"因子{name}表现优异（近期胜率{decayed:.0%} > 整体{overall:.0%}），权重 {old_w:.3f} → {new_w:.3f}"
                    )
                    report["weight_changes"][name] = {"old": old_w, "new": new_w}

        # 2. 归一化权重
        total_w = sum(self.state.weights.values())
        if total_w > 0:
            self.state.weights = {k: round(v / total_w, 4) for k, v in self.state.weights.items()}

        # 3. 更新市场状态
        if ohlcv_data and "closes" in ohlcv_data:
            from app.signals.math_engine import detect_regime
            regime_result = detect_regime(ohlcv_data["closes"])
            old_regime = self.state.regime
            self.state.regime = regime_result.regime
            self.state.regime_updated_at = time.time()
            if old_regime != regime_result.regime:
                report["actions"].append(
                    f"市场状态变化: {old_regime} → {regime_result.regime}"
                )

        # 4. 记录演化日志
        self.state.version += 1
        self.state.last_evolution_at = time.time()
        self.state.evolution_history.append({
            "version": self.state.version,
            "timestamp": time.time(),
            "weights": dict(self.state.weights),
            "regime": self.state.regime,
            "actions": report["actions"],
        })

        # 只保留最近20条演化日志
        if len(self.state.evolution_history) > 20:
            self.state.evolution_history = self.state.evolution_history[-20:]

        report["version_after"] = self.state.version
        report["current_weights"] = dict(self.state.weights)
        report["current_regime"] = self.state.regime

        self._save_state()
        return report

    # ── 策略快照查询 ──────────────────────────────────────────────────

    def get_performance_report(self) -> dict:
        """获取策略性能报告"""
        state = self.state
        factor_reports = {}
        for name, fp_dict in state.factor_performances.items():
            fp = state.get_factor_perf(name)
            factor_reports[name] = {
                "win_rate": round(fp.win_rate, 3),
                "decayed_win_rate": round(fp.decayed_win_rate(), 3),
                "avg_pnl_pct": round(fp.avg_pnl, 3),
                "total_signals": fp.total_signals,
                "current_weight": state.weights.get(name, 0),
            }

        return {
            "strategy_version": state.version,
            "current_weights": state.weights,
            "current_regime": state.regime,
            "global_win_rate": round(state.global_win_rate, 3),
            "total_signals_generated": state.total_signals_generated,
            "total_signals_settled": state.total_signals_settled,
            "factor_details": factor_reports,
            "recent_evolutions": state.evolution_history[-5:],
        }

    def increment_generated(self):
        """信号生成计数+1"""
        self.state.total_signals_generated += 1
        self._save_state()

    # ── 按币种胜率累加 ────────────────────────────────────────────────

    def update_coin_winrate(self, coin: str, pnl_pct: float, status: str):
        """
        结算后更新币种累加胜率（写入本地 strategy_state.json）

        Args:
            coin: 币种
            pnl_pct: 盈亏百分比
            status: hit_tp / hit_sl / expired
        """
        coin = coin.upper()
        ts = time.time()

        wr = self.state.coin_winrates.get(coin, {
            "wins": 0, "total": 0, "total_pnl": 0.0, "last_updated": 0.0,
        })
        wr["total"] = wr.get("total", 0) + 1
        wr["total_pnl"] = wr.get("total_pnl", 0.0) + pnl_pct
        wr["last_updated"] = ts

        # hit_tp 算赢，hit_sl 算输，expired 按 pnl 正负判断
        if status == "hit_tp" or (status == "expired" and pnl_pct > 0):
            wr["wins"] = wr.get("wins", 0) + 1

        self.state.coin_winrates[coin] = wr
        self._save_state()

    def get_coin_winrate(self, coin: str, grade: str = None) -> Optional[Dict[str, Any]]:
        """
        读取本地币种累加胜率（不查数据库，秒返回）

        Returns:
            {"win_rate": 65.0, "sample_count": 20, "avg_profit_pct": 3.2}
            None if no data
        """
        coin = coin.upper()
        wr = self.state.coin_winrates.get(coin)
        if not wr or wr.get("total", 0) < 1:
            return None

        total = wr["total"]
        wins = wr.get("wins", 0)
        total_pnl = wr.get("total_pnl", 0.0)

        return {
            "win_rate": round(wins / total * 100, 1),
            "sample_count": total,
            "avg_profit_pct": round(total_pnl / total, 2),
        }


# ── 全局单例 ────────────────────────────────────────────────────────────────

_engine: Optional[AdaptiveStrategyEngine] = None


def get_strategy_engine() -> AdaptiveStrategyEngine:
    global _engine
    if _engine is None:
        _engine = AdaptiveStrategyEngine()
    return _engine
