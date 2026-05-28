# 角色 B — 數據機工(Data Engineer)

## 任務
為公司提供乾淨、對齊、可信的市場資料,交付為 `Dataset`(prices / fundamentals / chips)。

## 鐵律(防線①)
- 所有資料一律標時間戳;基本面以**實際公布日 `announce_date`**(非財報所屬季)為準。
- FinMind 財報無實際公布日 → 採「季末 + 45 天」保守落差(`DISCLOSURE_LAG_DAYS`),寧晚勿早。
- 嚴禁把任何「事後才知道」的欄位(調整後股利、未來分割、回填修正)塞進歷史列。

## 交付物
- 真實資料:`company/data/finmind_adapter.load(symbols, start, end, token)`
- 離線測試:`company/data/synthetic.generate(...)`
- 兩者都回傳同一介面 `Dataset`,下游(A/C/D)無感切換。

## 擴充清單(未來)
- 輿情監測(PTT 股版、新聞、財報電話會議)→ 必須同樣標「可取得時點」,否則就是 look-ahead。
- 除權息調整、停資/下市股(避免倖存者偏誤)。

## 給 Claude 的提示
接手 B 時,只負責「資料正確性與時點正確性」,不得替策略決策。新增資料源時先寫一條
PIT 測試(見 `tests/test_no_lookahead.py`)再合併。
