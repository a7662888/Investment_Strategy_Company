# -*- coding: utf-8 -*-
"""
可解釋 + 校準的隔日偏多機率模型(Claude lane)。

設計重點:
  * 特徵與評分皆**純標準函式庫**(features.py / score.py 無 numpy/pandas),Render 的 app.py 可直接 import,不影響 build。
  * 訓練(train.py)用 numpy 擬合 logistic 權重,並以**時間外樣本(out-of-sample)**做機率校準,
    產出純 JSON artifact(權重 + 標準化參數 + 校準表 + 驗證指標)。
  * 評分時回傳:校準後機率、每個因子的貢獻(理由)、該機率桶的歷史命中率與前向報酬(依據/結果)。
  * 嚴守 PIT:只吃截止日以前的收盤序列,future_knowledge_used=False。
"""
