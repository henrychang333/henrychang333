# scripts/update_stock.py
import requests
import pandas as pd
import os
import json
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
import time
import urllib3
import shutil

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ============ 路徑設定 ============
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 回到專案根目錄
DATA_DIR = os.path.join(BASE_DIR, "data")
DOCS_DIR = os.path.join(BASE_DIR, "docs")
CSV_FILE = os.path.join(DATA_DIR, "stock_closing.csv")
HTML_FILE = os.path.join(DOCS_DIR, "stock_report.html")
WATCH_PATH = os.path.join(BASE_DIR, "watch.txt")
BACKUP_DIR = os.path.join(DATA_DIR, "backup")

# 確保目錄存在
for dir_path in [DATA_DIR, DOCS_DIR, BACKUP_DIR]:
    if not os.path.exists(dir_path):
        os.makedirs(dir_path)
        print(f"建立目錄: {dir_path}")

def backup_csv():
    """每次更新前備份 CSV"""
    if os.path.exists(CSV_FILE):
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_file = os.path.join(BACKUP_DIR, f"stock_closing_{timestamp}.csv")
        shutil.copy2(CSV_FILE, backup_file)
        print(f"已備份至: {backup_file}")
        
        # 只保留最近 7 天的備份
        backup_files = sorted([f for f in os.listdir(BACKUP_DIR) if f.endswith('.csv')])
        if len(backup_files) > 7:
            for f in backup_files[:-7]:
                os.remove(os.path.join(BACKUP_DIR, f))
                print(f"刪除舊備份: {f}")

def get_all_stocks():
    """取得所有股票代碼"""
    url = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
    try:
        data = requests.get(url, verify=False, timeout=30).json()
        return [{"Code": s["Code"], "Name": s["Name"]} for s in data]
    except Exception as e:
        print(f"取得股票清單失敗: {e}")
        return []

def fetch_stock_day(code, yyyymm):
    """抓取特定月份的股票資料"""
    url = f"https://www.twse.com.tw/exchangeReport/STOCK_DAY?response=json&date={yyyymm}01&stockNo={code}"
    try:
        res = requests.get(url, verify=False, timeout=10).json()
        if res.get("stat") != "OK":
            return []
        fields = res["fields"]
        rows = res["data"]
        date_idx = fields.index("日期")
        close_idx = fields.index("收盤價")
        return [{
            "Date": r[date_idx],
            "ClosingPrice": r[close_idx].replace(",", "")
        } for r in rows]
    except Exception as e:
        print(f"  抓取失敗：{code} {yyyymm}，原因：{e}")
        return []

def update_csv():
    """更新 CSV 檔案"""
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 開始更新資料...")
    
    # 1. 備份現有資料
    backup_csv()
    
    # 2. 讀取現有資料
    old_df = pd.DataFrame()
    last_date_dict = {}
    if os.path.exists(CSV_FILE):
        print("讀取現有資料...")
        old_df = pd.read_csv(CSV_FILE, encoding="utf-8-sig", dtype={"Code": str})
        last_date_dict = old_df.groupby("Code")["Date"].max().to_dict()
        print(f"現有資料: {len(old_df)} 筆")
    
    # 3. 抓取新資料
    stocks = get_all_stocks()
    if not stocks:
        print("無法取得股票清單，結束更新")
        return False
    
    print(f"共 {len(stocks)} 支股票需要檢查")
    
    all_new_rows = []
    today = datetime.today()
    
    # 抓取最近 3 個月的資料
    months = [(today - relativedelta(months=i)).strftime("%Y%m") for i in range(3, -1, -1)]
    
    for i, stock in enumerate(stocks):
        code = stock["Code"]
        name = stock["Name"]
        
        # 每 50 支顯示進度
        if i % 50 == 0:
            print(f"進度: {i+1}/{len(stocks)}")
        
        last_date = last_date_dict.get(code, None)
        
        for ym in months:
            if last_date and ym <= last_date[:7].replace("-", ""):
                continue
                
            records = fetch_stock_day(code, ym)
            for r in records:
                all_new_rows.append({
                    "Code": code,
                    "Name": name,
                    "Date": r["Date"],
                    "ClosingPrice": r["ClosingPrice"]
                })
            time.sleep(0.2)
    
    # 4. 合併資料
    if all_new_rows:
        new_df = pd.DataFrame(all_new_rows)
        df = pd.concat([old_df, new_df]).drop_duplicates(subset=["Code", "Date"])
        df.to_csv(CSV_FILE, index=False, encoding="utf-8-sig")
        print(f"更新完成！新增 {len(new_df)} 筆，總計 {len(df)} 筆")
        return True
    else:
        print("無新資料需要更新")
        return True  # 沒有新資料但現有資料仍可使用

def generate_html_report():
    """產生 HTML 報告到 docs/ 目錄"""
    if not os.path.exists(CSV_FILE):
        print("CSV 檔案不存在，無法產生報告")
        return False
    
    print("產生 HTML 報告...")
    df = pd.read_csv(CSV_FILE, encoding="utf-8-sig", dtype={"Code": str})
    
    # 準備資料
    stock_data = {}
    for (code, name), group in df.groupby(["Code", "Name"]):
        group = group.sort_values("Date")
        stock_data[code] = {
            "name": name,
            "dates": group["Date"].tolist(),
            "prices": group["ClosingPrice"].tolist()
        }
    
    # 選單選項
    options_html = "\n".join(
        f'<option value="{code}">{code} {info["name"]}</option>'
        for code, info in sorted(stock_data.items())
    )
    
    # 讀取監控清單
    watch_codes = set()
    if os.path.exists(WATCH_PATH):
        with open(WATCH_PATH, "r", encoding="utf-8") as f:
            for line in f:
                code = line.strip()
                if code:
                    watch_codes.add(code)
    
    # 產生 HTML（完整內容，與原 stock_report.py 相同）
    html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>台股收盤價</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
  <style>
    body {{ font-family: Arial; padding: 15px; }}
    select {{ padding: 8px; font-size: 16px; width: 100%; max-width: 400px; }}
    #container {{ display: flex; flex-wrap: wrap; gap: 20px; margin-top: 20px; }}
    #left {{ width: 100%; max-width: 350px; }}
    #right {{ flex: 1; min-width: 300px; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border: 1px solid #ddd; padding: 6px; text-align: center; }}
    th {{ background: #f2f2f2; }}
    #signalArea {{ margin-top: 30px; border-top: 2px solid #4caf50; padding-top: 15px; }}
    #warningArea {{ margin-top: 20px; border-top: 2px solid #f44336; padding-top: 15px; }}
    #signalList, #warningList {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }}
    .signal-item {{
      background: #e8f5e9;
      border: 1px solid #4caf50;
      padding: 6px 12px;
      border-radius: 4px;
      cursor: pointer;
    }}
    .signal-item:hover {{ background: #c8e6c9; }}
    .warning-item {{
      background: #ffebee;
      border: 1px solid #f44336;
      padding: 6px 12px;
      border-radius: 4px;
      cursor: pointer;
    }}
    .warning-item:hover {{ background: #ffcdd2; }}
    #updateTime {{ color: #888; font-size: 13px; margin-top: 5px; }}
  </style>
</head>
<body>
  <h2>台股股價查詢</h2>
  <p id="updateTime">資料產生時間：{pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}</p>

  <select id="stockSelect" onchange="updateView()">
    <option value="">-- 請選擇股票 --</option>
    {options_html}
  </select>

  <div id="container">
    <div id="left">
      <h3>近5個月收盤價</h3>
      <table>
        <thead><tr><th>日期</th><th>收盤價</th></tr></thead>
        <tbody id="tableBody"></tbody>
      </table>
    </div>
    <div id="right">
      <h3>近6個月均線圖</h3>
      <canvas id="maChart"></canvas>
    </div>
  </div>

  <div id="signalArea">
    <h3>📈 多頭排列股票</h3>
    <p style="color:#888; font-size:13px;">
      條件：MA8斜率 > MA21斜率 > MA55斜率（斜率皆為正）且當日 MA8 > MA21 > MA55
    </p>
    <div id="signalList"></div>
  </div>

  <div id="warningArea">
    <h3>⚠️ 死亡交叉警示（監控清單）</h3>
    <p style="color:#888; font-size:13px;">
      條件：MA8斜率 &lt; MA21斜率 &lt; MA55斜率，且 MA8-MA21 &lt; 2、MA8-MA55 &lt; 2
    </p>
    <div id="warningList"></div>
  </div>

  <script>
    const stockData = {json.dumps(stock_data, ensure_ascii=False)};
    const watchCodes = new Set({json.dumps(list(watch_codes))});
    let chartInstance = null;

    function parsePrice(v) {{
      return parseFloat(String(v).replace(/,/g, ""));
    }}

    function calcMA(prices, n) {{
      return prices.map((_, i) =>
        i < n - 1 ? null :
        (prices.slice(i-n+1, i+1).reduce((a,b) => a + parsePrice(b), 0) / n).toFixed(2)
      );
    }}

    function getSlope(arr) {{
      const valid = arr.filter(v => v !== null);
      if (valid.length < 2) return 0;
      return parseFloat(valid[valid.length-1]) - parseFloat(valid[valid.length-2]);
    }}

    function getLastValue(arr) {{
      const valid = arr.filter(v => v !== null);
      if (valid.length === 0) return null;
      return parseFloat(valid[valid.length-1]);
    }}

    function updateView() {{
      const code = document.getElementById("stockSelect").value;
      if (!code) return;
      const data = stockData[code];
      const dates = data.dates;
      const prices = data.prices;

      const recent  = dates.slice(-100);
      const recentP = prices.slice(-100);
      const tbody = document.getElementById("tableBody");
      tbody.innerHTML = "";
      [...recent].reverse().forEach((d, i) => {{
        const p = recentP[recentP.length - 1 - i];
        tbody.innerHTML += `<tr><td>${{d}}</td><td>${{p}}</td></tr>`;
      }});
      
      const ma8full  = calcMA(prices, 8);
      const ma21full = calcMA(prices, 21);
      const ma55full = calcMA(prices, 55);

      const N = 125;
      const yearDates  = dates.slice(-N);
      const yearPrices = prices.slice(-N);
      const ma8  = ma8full.slice(-N);
      const ma21 = ma21full.slice(-N);
      const ma55 = ma55full.slice(-N);
      
      const verticalLinePlugin = {{
        id: "verticalLine",
        afterDraw(chart) {{
          const tooltip = chart.tooltip;
          if (!tooltip || !tooltip.getActiveElements || !tooltip.getActiveElements().length) return;

          const ctx = chart.ctx;
          const x = tooltip.getActiveElements()[0].element.x;
          const {{ top, bottom }} = chart.chartArea;

          ctx.save();
          ctx.beginPath();
          ctx.moveTo(x, top);
          ctx.lineTo(x, bottom);
          ctx.lineWidth = 1;
          ctx.strokeStyle = "rgba(100,100,100,0.6)";
          ctx.setLineDash([5, 5]);
          ctx.stroke();
          ctx.restore();
        }}
      }};

      if (chartInstance) chartInstance.destroy();
      chartInstance = new Chart(document.getElementById("maChart"), {{
        type: "line",
        plugins: [verticalLinePlugin], 
        data: {{
          labels: yearDates,
          datasets: [
            {{ label: "收盤價", data: yearPrices.map(parsePrice), borderColor: "black", borderWidth: 2, pointRadius: 0 }},
            {{ label: "MA8",   data: ma8,                    borderColor: "blue",   borderWidth: 5, pointRadius: 0 }},
            {{ label: "MA21",  data: ma21,                   borderColor: "orange", borderWidth: 5, pointRadius: 0 }},
            {{ label: "MA55",  data: ma55,                   borderColor: "red",    borderWidth: 5, pointRadius: 0 }}
          ]
        }},
        options: {{
          responsive: true,
          interaction: {{
            mode: "index",
            intersect: false
          }},
          plugins: {{
            legend: {{ position: "top" }},
            tooltip: {{ enabled: true }}
          }},
          scales: {{ x: {{ ticks: {{ maxTicksLimit: 12 }} }} }}
        }}
      }});
    }}

    function buildSignalList() {{
      const signalList = document.getElementById("signalList");
      const warningList = document.getElementById("warningList");
      signalList.innerHTML = "";
      warningList.innerHTML = "";

      const signals = [];
      const warnings = [];

      for (const [code, data] of Object.entries(stockData)) {{
        const prices = data.prices;
        if (prices.length < 55) continue;

        const ma8  = calcMA(prices, 8);
        const ma21 = calcMA(prices, 21);
        const ma55 = calcMA(prices, 55);

        const s8  = getSlope(ma8);
        const s21 = getSlope(ma21);
        const s55 = getSlope(ma55);

        const v8  = getLastValue(ma8);
        const v21 = getLastValue(ma21);
        const v55 = getLastValue(ma55);

        if (v8 === null || v21 === null || v55 === null) continue;

        // 多頭排列條件
        if (
          s8 > s21 && s21 > s55 &&
          s8 > 0 && s21 > 0 && s55 > 0 &&
          v8 < v21 && v8 < v55
        ) {{
          signals.push({{ code, name: data.name }});
        }}

        // 死亡交叉警示條件
        if (watchCodes.has(code)) {{
          const diff_8_21 = v8 - v21;
          const diff_8_55 = v8 - v55;
          if (
            s8 < s21 && s21 < s55 &&
            diff_8_21 < 2 &&
            diff_8_55 < 2
          ) {{
            warnings.push({{
              code,
              name: data.name,
              diff_8_21: diff_8_21.toFixed(2),
              diff_8_55: diff_8_55.toFixed(2)
            }});
          }}
        }}
      }}

      if (signals.length === 0) {{
        signalList.innerHTML = "<p>目前無符合條件的股票</p>";
      }} else {{
        signals.sort((a, b) => a.code.localeCompare(b.code));
        signals.forEach(s => {{
          const div = document.createElement("div");
          div.className = "signal-item";
          div.textContent = `${{s.code}} ${{s.name}}`;
          div.onclick = () => {{
            document.getElementById("stockSelect").value = s.code;
            updateView();
            window.scrollTo({{ top: 0, behavior: "smooth" }});
          }};
          signalList.appendChild(div);
        }});
        const countEl = document.createElement("p");
        countEl.style.cssText = "width:100%; margin-top:10px; color:#555; font-size:13px;";
        countEl.textContent = `共 ${{signals.length}} 支股票符合條件`;
        signalList.appendChild(countEl);
      }}

      if (watchCodes.size === 0) {{
        warningList.innerHTML = "<p style='color:#888'>watch.txt 為空或不存在</p>";
      }} else if (warnings.length === 0) {{
        warningList.innerHTML = "<p style='color:#888'>監控清單中目前無警示股票</p>";
      }} else {{
        warnings.sort((a, b) => a.code.localeCompare(b.code));
        warnings.forEach(w => {{
          const div = document.createElement("div");
          div.className = "warning-item";
          div.innerHTML = `
            <strong>${{w.code}} ${{w.name}}</strong><br>
            <small>MA8-MA21: ${{w.diff_8_21}} ｜ MA8-MA55: ${{w.diff_8_55}}</small>
          `;
          div.onclick = () => {{
            document.getElementById("stockSelect").value = w.code;
            updateView();
            window.scrollTo({{ top: 0, behavior: "smooth" }});
          }};
          warningList.appendChild(div);
        }});
        const countEl = document.createElement("p");
        countEl.style.cssText = "width:100%; margin-top:10px; color:#f44336; font-size:13px;";
        countEl.textContent = `共 ${{warnings.length}} 支股票觸發警示`;
        warningList.appendChild(countEl);
      }}
    }}

    buildSignalList();
  </script>
</body>
</html>"""
    
    # 寫入 HTML 檔案
    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    
    print(f"HTML 報告已產生: {HTML_FILE}")
    return True

def main():
    success = update_csv()
    if success:
        generate_html_report()
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 全部完成！")
        print(f"📊 報告位置: docs/stock_report.html")
        print(f"📈 資料位置: data/stock_closing.csv")
    else:
        print("更新失敗，請檢查網路或 API 狀態")

if __name__ == "__main__":
    main()
