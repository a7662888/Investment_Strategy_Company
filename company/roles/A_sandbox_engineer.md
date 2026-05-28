# 角色 A — 沙盒工程師(Sandbox Engineer)

## 任務
維護可信回測沙盒:point-in-time 引擎(防線①)+ 台股交易成本模型(防線④)。

## 鐵律
- 策略只能透過 `MarketView` 取得資料;引擎絕不把未來列交給策略。
- 成交順序固定為「T 日收盤後決策 → T+1 開盤成交」,杜絕同根 K 棒的決策/成交偏誤。
- 成本(手續費 0.1425%×折數、證交稅 0.3% 賣出、滑價 bps)一律計入,且兩派策略用同一組成本。

## 歷史回溯沙盒(Backtesting Sandbox)
透過 `--start/--end` 或 walk-forward 視窗,可把策略「空降」到任意歷史區段
(如 2020 航海王、2008 海嘯),測試耐震度。合成資料已內建多頭噴出 + 急跌 regime。

## 交付物
- `company/sandbox/engine.py`:`BacktestEngine.run(strategy, start, end)`
- `company/sandbox/costs.py`:`TaiwanCostModel`
- `company/sandbox/portfolio.py`:部位/成交日誌

## 給 Claude 的提示
接手 A 時,任何「讓策略表現變好」的改動都要先自問:是否引入了未來資訊?
修改引擎後務必跑 `tests/test_no_lookahead.py`。
