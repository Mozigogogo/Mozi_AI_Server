"""展示成交量和持仓量接口的完整实现"""
import sys
sys.path.insert(0, '/Users/shaoqi/PycharmProjects/agent')

print("=" * 80)
print("🔍 成交量和持仓量接口实现分析")
print("=" * 80)

# 展示 get_trading_volume 接口实现
print("\n" + "=" * 80)
print("📊 接口1: get_trading_volume (成交量）")
print("=" * 80)

code_trading_volume = '''
def get_trading_volume(symbol: str) -> Dict[str, Any]:
    """获取成交量（从成交额数据中提取）"""
    try:
        # 调用 get_trading_value 获取成交额数据
        trading_data = get_trading_value(symbol)

        # 从成交额数据中提取 volume 字段
        return {
            "volume": trading_data.get("volume", 0),
            "volume_change": trading_data.get("volume_change", 0),
            "timestamp": trading_data.get("timestamp")
        }
    except Exception:
        return {"volume": 0, "volume_change": 0}
'''

print("代码实现:")
print(code_trading_volume)

print("\n调用链路分析:")
print("1. get_trading_volume(symbol)")
print("   └─> get_trading_value(symbol)")
print("       └─> fetch_json(url)")
print("           └─> 请求: https://moziinnovations.com/derivatives/histTradingVal/forllm?coin={symbol}")

print("\n" + "-" * 80)
print("API返回数据结构（根据测试）:")
print("返回的根对象:")
print("  {")
print("    \"code\": 0,")
print("    \"data\": {")
print("      \"coin\": \"BTC\",")
print("      \"metric\": \"trading_value_usd\",")
print("      \"unit\": \"亿USD(bar)\",")
print("      \"exchanges\": [...],  # 交易所列表")
print("      \"dates\": [...],  # 日期列表")
print("      \"data\": {  # 这里是关键！")
print("        \"Binance\": [167.26, 203.79, ...],  # 各交易所的历史数据")
print("        \"Bybit\": [...],")
print("        \"Coinbase\": [...]")
print("        ...")
print("      }")
print("    }")
print("  }")

print("\n⚠️  问题分析:")
print("当前实现: return data.get(\"data\", {})")
print("返回的是: 整个data对象（包含coin, metric, exchanges, dates, data）")
print("期望提取: 从 data.data.{exchange} 中获取volume")
print("")
print("实际提取: trading_data.get(\"volume\", 0)")
print("")
print("但 data.get(\"data\", {}) 返回的对象中没有volume字段！")
print("data.data 中是按交易所分组的数组，不是单个volume值")

print("\n" + "=" * 80)

# 展示 get_open_interest 接口实现
print("\n" + "=" * 80)
print("📊 接口2: get_open_interest (持仓量）")
print("=" * 80)

code_open_interest = '''
def get_open_interest(symbol: str) -> Dict[str, Any]:
    """获取持仓量"""
    url = f"{settings.derivatives_api_base}/histOpenInterest/forllm?coin={symbol}"
    try:
        data = fetch_json(url)
        if data.get("code") == 0:
            return data.get("data", {})
        return {}
    except Exception:
        return {}
'''

print("代码实现:")
print(code_open_interest)

print("\n调用链路分析:")
print("1. get_open_interest(symbol)")
print("   └─> fetch_json(url)")
print("           └─> 请求: https://moziinnovations.com/derivatives/histOpenInterest/forllm?coin={symbol}")

print("\n" + "-" * 80)
print("⚠️ 问题分析:")
print("当前实现: return data.get(\"data\", {})")
print("")
print("根据API文档，这个接口应该返回:")
print("  {")
print("    \"code\": 0,")
print("    \"data\": {")
print("      \"coin\": \"BTC\",")
print("      \"openInterest\": 1000.0,  # 最新持仓量")
print("      \"oiChange\": 5.5,  # 持仓量变化")
print("      \"timestamp\": \"2026-04-30T00:00:00\"")
print("    }")
print("  }")

print("\n" + "=" * 80)
print("🎯 核心问题总结")
print("=" * 80)

print("问题1: get_trading_volume 提取错误")
print("  当前: trading_data.get(\"volume\", 0)")
print("  原因: 返回的data.data中没有直接的volume字段")
print("  应该: 需要从data.data.{exchange}中提取，或使用最新值")
print("")
print("问题2: get_open_interest 可能提取错误")
print("  当前: data.get(\"data\", {})")
print("  原因: 可能直接返回了空对象，或字段名称不匹配")
print("")
print("💡 建议修复方案:")
print("")
print("1. 对于 get_trading_volume:")
print("   - 方案A: 使用 get_trading_value 返回的完整data对象")
print("   - 方案B: 直接调用 /histTradingVal/forllm 接口并正确解析data.data")
print("   - 方案C: 如果API返回数据为0，说明数据源问题")
print("")
print("2. 对于 get_open_interest:")
print("   - 检查API实际返回的字段名称")
print("   - 可能是 \"openInterest\" 而不是 \"oi\"")
print("   - 根据实际返回的字段调整代码")

print("\n" + "=" * 80)
