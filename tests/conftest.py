"""測試隔離：不讀本機 .env、不帶任何正式金鑰。

app.py 匯入時會讀 .env；本機的 .env 含正式私有資料庫與永豐金鑰，測試因而會真的連線
（2026-10-10 實測 test_health_endpoint 偶發逾時即為此因），最壞情況會寫入正式資料。
CI 沒有 .env 所以不受影響；這裡讓本機與 CI 行為一致。
"""
import os

os.environ["APP_SKIP_DOTENV"] = "1"
for _key in ("GITHUB_DATA_TOKEN", "GITHUB_PAT", "GITHUB_DATA_REPO", "SHIOAJI_API_KEY",
             "SHIOAJI_SECRET_KEY", "POSITIONS_SYNC_TOKEN", "GEMINI_API_KEY", "FINMIND_TOKEN"):
    os.environ.pop(_key, None)
