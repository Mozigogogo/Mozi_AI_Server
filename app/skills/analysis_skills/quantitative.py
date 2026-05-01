"""量化决策分析 Skill - 六因子评分模型"""
import asyncio
from app.skills.base import BaseSkill, IntentInfo, SkillResult
from app.services.data_service import (
    get_header_data,
    get_kline_data,
    get_recent_news,
    get_buy_sell_ratio,
    get_open_interest,
    get_funding_rate
)


class QuantitativeAnalysisSkill(BaseSkill):
    """量化决策分析 Skill - 六因子评分模型"""

    name = "quantitative_analysis"
    description = "量化决策分析（六因子评分模型）"

    def match(self, intent: IntentInfo, mode: str = "chat") -> bool:
        """匹配意图"""
        return intent.intent_type == "analyze_quantitative"

    def get_required_apis(self) -> list:
        """需要调用所有相关的 API"""
        return [
            "get_header_data",
            "get_kline_data",
            "get_recent_news",
            "get_buy_sell_ratio",
            "get_open_interest",
            "get_funding_rate"
        ]

    async def execute_async(
        self,
        symbol: str,
        intent: IntentInfo
    ) -> SkillResult:
        """执行量化分析（并发调用多个 API）"""
        # 并发调用所有相关 API
        tasks = [
            asyncio.to_thread(get_header_data, symbol),
            asyncio.to_thread(get_kline_data, symbol),
            asyncio.to_thread(get_recent_news, symbol, limit=5),
            asyncio.to_thread(get_buy_sell_ratio, symbol),
            asyncio.to_thread(get_open_interest, symbol),
            asyncio.to_thread(get_funding_rate, symbol)
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 解析结果
        data = {}
        api_calls = []

        api_names = [
            "get_header_data",
            "get_kline_data",
            "get_recent_news",
            "get_buy_sell_ratio",
            "get_open_interest",
            "get_funding_rate"
        ]

        for api_name, result in zip(api_names, results):
            if not isinstance(result, Exception):
                data[api_name] = result
                api_calls.append(api_name)
            else:
                print(f"  警告: {api_name} 调用失败: {str(result)}")

        # 构建传给LLM的精简数据
        llm_data = self._build_llm_data(symbol, data)

        return SkillResult(
            skill_name=self.name,
            data=llm_data,
            timestamp=self._get_timestamp(),
            api_calls=api_calls
        )

    def _quantitative_analysis(self, symbol: str, data: dict) -> dict:
        """六因子量化评分分析"""
        scores = {
            "trend": 0,       # 趋势因子
            "momentum": 0,     # 动量因子
            "volume": 0,        # 成交量因子
            "capital": 0,        # 资金因子
            "volatility": 0,     # 波动率因子
            "narrative": 0       # 叙事因子
        }

        explanations = {
            "trend": "",
            "momentum": "",
            "volume": "",
            "capital": "",
            "volatility": "",
            "narrative": ""
        }

        # 1. 趋势因子评分 (-2 ~ +2)
        if "get_kline_data" in data:
            kline_data = data["get_kline_data"]
            if kline_data and isinstance(kline_data, dict) and "values" in kline_data:
                values = kline_data["values"]
                if values and len(values) >= 10:
                    # 获取最近10天的收盘价
                    close_prices = []
                    for day in values[-10:]:
                        if isinstance(day, list) and len(day) >= 4:
                            try:
                                close_prices.append(float(day[3]))
                            except (ValueError, IndexError):
                                continue

                    if len(close_prices) >= 7:
                        # 计算7日和20日移动平均
                        ma7 = sum(close_prices[-7:]) / 7
                        ma20 = sum(close_prices[-20:]) / 20 if len(close_prices) >= 20 else ma7

                        # 判断趋势
                        if close_prices[-1] > ma7 and ma7 > ma20:
                            scores["trend"] = 2
                            explanations["trend"] = "价格站上MA7和MA20，强势上涨趋势"
                        elif close_prices[-1] > ma7:
                            scores["trend"] = 1
                            explanations["trend"] = "价格站上MA7但低于MA20，短期上涨，中期震荡"
                        elif close_prices[-1] < ma7 and ma7 < ma20:
                            scores["trend"] = -2
                            explanations["trend"] = "价格跌破MA7和MA20，明显下跌趋势"
                        elif close_prices[-1] < ma7:
                            scores["trend"] = -1
                            explanations["trend"] = "价格跌破MA7但高于MA20，短期下跌，中期震荡"
                        else:
                            scores["trend"] = 0
                            explanations["trend"] = "价格围绕均线震荡，趋势中性"

        # 2. 动量因子评分 (-2 ~ +2)
        if "get_kline_data" in data:
            kline_data = data["get_kline_data"]
            if kline_data and isinstance(kline_data, dict) and "values" in kline_data:
                values = kline_data["values"]
                if values and len(values) >= 15:
                    # 获取最近15天的收盘价
                    close_prices = []
                    for day in values[-15:]:
                        if isinstance(day, list) and len(day) >= 4:
                            try:
                                close_prices.append(float(day[3]))
                            except (ValueError, IndexError):
                                continue

                    if len(close_prices) >= 14:
                        # 简单计算RSI（相对强弱指标）
                        gains = []
                        losses = []

                        for i in range(1, len(close_prices)):
                            change = close_prices[i] - close_prices[i-1]
                            if change > 0:
                                gains.append(change)
                                losses.append(0)
                            else:
                                gains.append(0)
                                losses.append(abs(change))

                        if len(gains) >= 5:
                            avg_gain = sum(gains[-5:]) / 5
                            avg_loss = sum(losses[-5:]) / 5 if sum(losses[-5:]) > 0 else 0.01

                            if avg_loss > 0:
                                rs = avg_gain / avg_loss
                                rsi = 100 - (100 / (1 + rs))

                                # RSI评分
                                if rsi >= 70:
                                    scores["momentum"] = -2
                                    explanations["momentum"] = f"RSI={rsi:.1f}，处于超买区，动量过热"
                                elif rsi >= 55:
                                    scores["momentum"] = 1
                                    explanations["momentum"] = f"RSI={rsi:.1f}，处于强势区，动量偏强"
                                elif rsi <= 30:
                                    scores["momentum"] = 2
                                    explanations["momentum"] = f"RSI={rsi:.1f}，处于超卖区，动量偏弱但可能反弹"
                                elif rsi <= 45:
                                    scores["momentum"] = -1
                                    explanations["momentum"] = f"RSI={rsi:.1f}，处于弱势区，动量偏弱"
                                else:
                                    scores["momentum"] = 0
                                    explanations["momentum"] = f"RSI={rsi:.1f}，处于中性区，动量均衡"

        # 3. 成交量因子评分 (-2 ~ +2)
        if "get_header_data" in data:
            header_data = data["get_header_data"]
            if header_data:
                # 使用价格变化判断量价关系
                price_change = header_data.get("priceChange_24h")
                volume = header_data.get("volume", "")

                try:
                    price_change_val = float(price_change) if price_change else 0
                except (ValueError, TypeError):
                    price_change_val = 0

                if price_change_val > 20:
                    scores["volume"] = 2
                    explanations["volume"] = f"24小时上涨{price_change_val:.2f}，量价配合度良好"
                elif price_change_val > 5:
                    scores["volume"] = 1
                    explanations["volume"] = f"24小时上涨{price_change_val:.2f}，放量上涨"
                elif price_change_val < -10:
                    scores["volume"] = -2
                    explanations["volume"] = f"24小时下跌{price_change_val:.2f}，放量下跌"
                elif price_change_val < -3:
                    scores["volume"] = -1
                    explanations["volume"] = f"24小时下跌{price_change_val:.2f}，缩量下跌"
                else:
                    scores["volume"] = 0
                    explanations["volume"] = f"24小时变化{price_change_val:.2f}，量价关系中性"

        # 4. 资金因子评分 (-2 ~ +2)
        capital_score = 0
        capital_signals = []

        # 4.1 主动买卖比
        if "get_buy_sell_ratio" in data:
            ratio_data = data["get_buy_sell_ratio"]
            if ratio_data and isinstance(ratio_data, dict):
                # 解析各交易所多空比
                exchange_ratios = []
                for exchange, exchange_data in ratio_data.items():
                    if isinstance(exchange_data, dict):
                        ls = exchange_data.get("longShortData", [])
                        if isinstance(ls, list) and ls:
                            exchange_ratios.append(ls[-1])

                if exchange_ratios:
                    avg_ratio = sum(exchange_ratios) / len(exchange_ratios)
                    if avg_ratio > 1.1:
                        capital_score += 1
                        capital_signals.append(f"多空比{avg_ratio:.2f}，买盘占优")
                    elif avg_ratio < 0.9:
                        capital_score -= 1
                        capital_signals.append(f"多空比{avg_ratio:.2f}，卖盘占优")
                    else:
                        capital_signals.append(f"多空比{avg_ratio:.2f}，多空平衡")
                else:
                    capital_signals.append("多空比数据不足")

        # 4.2 持仓变化
        if "get_open_interest" in data:
            oi_data = data["get_open_interest"]
            if oi_data:
                oi_change = oi_data.get("oi_change", 0)
                try:
                    oi_change_val = float(oi_change) if oi_change else 0
                except (ValueError, TypeError):
                    oi_change_val = 0

                if oi_change_val > 5:
                    capital_score += 1
                    capital_signals.append(f"持仓增加{oi_change_val:.2f}%，资金流入")
                elif oi_change_val < -5:
                    capital_score -= 1
                    capital_signals.append(f"持仓减少{oi_change_val:.2f}%，资金流出")
                else:
                    capital_signals.append(f"持仓变化{oi_change_val:.2f}%，资金平稳")

        # 4.3 费率结构
        if "get_funding_rate" in data:
            funding_data = data["get_funding_rate"]
            if funding_data and isinstance(funding_data, dict):
                exchanges = funding_data.get("exchanges", {})
                if exchanges:
                    avg_funding = 0
                    valid_rates = []
                    for exchange, rate_str in exchanges.items():
                        try:
                            rate = float(str(rate_str).replace("%", ""))
                            valid_rates.append(rate)
                        except (ValueError, AttributeError):
                            continue

                    if valid_rates:
                        avg_funding = sum(valid_rates) / len(valid_rates)

                        if avg_funding > 0.01:
                            capital_score += 1
                            capital_signals.append(f"平均资金费率{avg_funding:.4f}%，多头强势")
                        elif avg_funding < -0.01:
                            capital_score -= 1
                            capital_signals.append(f"平均资金费率{avg_funding:.4f}%，空头强势")
                        else:
                            capital_signals.append(f"平均资金费率{avg_funding:.4f}%，多空平衡")

        # 资金因子评分归一化到-2~+2
        if capital_score >= 2:
            scores["capital"] = 2
        elif capital_score == 1:
            scores["capital"] = 1
        elif capital_score == -1:
            scores["capital"] = -1
        elif capital_score <= -2:
            scores["capital"] = -2
        else:
            scores["capital"] = 0

        explanations["capital"] = "；".join(capital_signals) if capital_signals else "资金因子数据不足"

        # 5. 波动率因子评分 (-1 ~ +1)
        if "get_header_data" in data:
            header_data = data["get_header_data"]
            if header_data:
                high_24h = header_data.get("high_24h")
                low_24h = header_data.get("low_24h")

                try:
                    high_val = float(high_24h) if high_24h else 0
                    low_val = float(low_24h) if low_24h else 0

                    if high_val > 0 and low_val > 0:
                        # 计算波动幅度
                        volatility = (high_val - low_val) / low_val * 100

                        if volatility > 5:
                            scores["volatility"] = -1
                            explanations["volatility"] = f"24小时波动{volatility:.2f}%，波动剧烈，趋势不稳定"
                        elif volatility > 2:
                            scores["volatility"] = 0
                            explanations["volatility"] = f"24小时波动{volatility:.2f}%，波动适中"
                        else:
                            scores["volatility"] = 1
                            explanations["volatility"] = f"24小时波动{volatility:.2f}%，波动收敛，趋势稳定"
                except (ValueError, TypeError):
                    explanations["volatility"] = "波动率计算失败"

        # 6. 叙事因子评分 (-2 ~ +2)
        narrative_score = 0
        narrative_signals = []

        # 6.1 新闻情绪
        if "get_recent_news" in data:
            news_data = data["get_recent_news"]
            if isinstance(news_data, list) and news_data:
                positive_keywords = ['涨', '涨', '上涨', '突破', '新高', '利好', '增加', '增长']
                negative_keywords = ['跌', '下跌', '暴跌', '新低', '利空', '减少', '收缩']

                positive_count = 0
                negative_count = 0

                for news in news_data[:5]:  # 只分析最近5条新闻
                    news_text = str(news).lower()
                    for keyword in positive_keywords:
                        if keyword in news_text:
                            positive_count += 1
                    for keyword in negative_keywords:
                        if keyword in news_text:
                            negative_count += 1

                if positive_count > negative_count * 2:
                    narrative_score += 1
                    narrative_signals.append("新闻偏利好")
                elif negative_count > positive_count * 2:
                    narrative_score -= 1
                    narrative_signals.append("新闻偏利空")
                else:
                    narrative_signals.append("新闻情绪中性")

        # 6.2 监管风险和项目进展（简化处理，默认为中性）
        narrative_signals.append("暂无重大监管/项目进展信息")

        # 叙事因子评分归一化到-2~+2
        if narrative_score >= 2:
            scores["narrative"] = 2
        elif narrative_score == 1:
            scores["narrative"] = 1
        elif narrative_score == -1:
            scores["narrative"] = -1
        elif narrative_score <= -2:
            scores["narrative"] = -2
        else:
            scores["narrative"] = 0

        explanations["narrative"] = "；".join(narrative_signals) if narrative_signals else "叙事因子数据不足"

        # 计算总分
        total_score = sum(scores.values())

        # 映射胜率
        def calculate_win_rate(score):
            if score >= 7:
                return (70, 80)
            elif score >= 4:
                return (60, 69)
            elif score >= 1:
                return (52, 59)
            elif score >= -1:
                return (48, 51)
            elif score >= -4:
                return (40, 47)
            else:
                return (30, 39)

        buy_win_rate_range = calculate_win_rate(total_score)
        buy_win_rate_str = f"{buy_win_rate_range[0]}%~{buy_win_rate_range[1]}%"
        sell_win_rate_str = f"{100 - buy_win_rate_range[1]}%~{100 - buy_win_rate_range[0]}%"

        # 判断综合倾向
        if total_score >= 4:
            tendency = "偏多"
        elif total_score <= -4:
            tendency = "偏空"
        else:
            tendency = "中性"

        return {
            "scores": scores,
            "explanations": explanations,
            "total_score": total_score,
            "buy_win_rate": buy_win_rate_str,
            "sell_win_rate": sell_win_rate_str,
            "tendency": tendency,
            "symbol": symbol
        }

    def _build_llm_data(self, symbol: str, data: dict) -> dict:
        """构建传给LLM的精简数据：六因子评分 + 关键原始数据摘要"""
        analysis = self._quantitative_analysis(symbol, data)
        result = {"六因子评分": analysis}

        # 实时价格
        if "get_header_data" in data and data["get_header_data"]:
            h = data["get_header_data"]
            try:
                price = float(h.get("currentPrice", 0))
            except (ValueError, TypeError):
                price = 0
            result["实时数据"] = {
                "当前价格": price,
                "24h涨跌幅": h.get("priceChangePercentage_24h"),
                "24h最高": h.get("high_24h"),
                "24h最低": h.get("low_24h"),
            }

        # 多空比精简：每个交易所只保留最新值
        if "get_buy_sell_ratio" in data and data["get_buy_sell_ratio"]:
            ratio_raw = data["get_buy_sell_ratio"]
            ratio_summary = {}
            for exchange, exchange_data in ratio_raw.items():
                if isinstance(exchange_data, dict):
                    ls = exchange_data.get("longShortData", [])
                    ratio_summary[exchange] = {
                        "多空比": ls[-1] if ls else "N/A",
                        "多头占比": exchange_data.get("longData", [None])[-1],
                        "空头占比": exchange_data.get("shortData", [None])[-1],
                    }
            result["多空比"] = ratio_summary

        # 资金费率
        if "get_funding_rate" in data and data["get_funding_rate"]:
            result["资金费率"] = data["get_funding_rate"]

        # 新闻（只取最新3条）
        if "get_recent_news" in data and data["get_recent_news"]:
            news_list = data["get_recent_news"]
            if isinstance(news_list, list):
                result["最新新闻"] = news_list[:3]

        return result
