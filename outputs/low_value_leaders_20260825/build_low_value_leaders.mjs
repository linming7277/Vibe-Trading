import fs from "node:fs/promises";
import { execFileSync } from "node:child_process";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const outputDir = "D:/AI/hzstock/outputs/low_value_leaders_20260825";
const python = "D:/AI/hzstock/.venv/Scripts/python.exe";
const queryScript = String.raw`
import json, sys, sqlite3
from pathlib import Path
sys.path.insert(0, r"D:\AI\hzstock\agent")
from src.value_price_zones.service import ValuePriceZoneService

research = Path(r"C:\Users\Administrator\.vibe-trading\research.db")
con = sqlite3.connect(f"file:{research.as_posix()}?mode=ro", uri=True)
con.row_factory = sqlite3.Row
run = con.execute("""SELECT id,as_of FROM value_level3_leader_runs
                     WHERE status='COMPLETED' ORDER BY as_of DESC,completed_at DESC LIMIT 1""").fetchone()
if not run: raise SystemExit("No completed leader snapshot")
candidates = con.execute("""
    SELECT l.stock_code,l.stock_name,l.level1_name,l.level2_name,l.level3_name,
           v.presentation_status,v.historical_valuation_status
    FROM value_level3_leader_valuation_snapshots v
    JOIN value_level3_leaders l ON l.run_id=v.run_id AND l.stock_code=v.stock_code
    WHERE v.run_id=? AND l.eligibility_status='eligible' AND l.leader_rank<=2
      AND v.presentation_status IN ('DEEPLY_UNDERVALUED','UNDERVALUED')
    ORDER BY CASE v.presentation_status WHEN 'DEEPLY_UNDERVALUED' THEN 0 ELSE 1 END,
             l.leader_score DESC,l.stock_code
""", (run['id'],)).fetchall()
status_labels = {
  'DEEPLY_UNDERVALUED':'明显低于合理价值区间', 'UNDERVALUED':'低于合理价值区间',
  'FAIR':'接近合理价值区间', 'OVERVALUED':'高于合理价值区间',
  'DEEPLY_OVERVALUED':'明显高于合理价值区间', 'INSUFFICIENT_DATA':'合理价值资料不足',
}
history_labels = {
  'VERY_CHEAP':'历史极低位', 'CHEAP':'历史偏低位', 'NORMAL':'历史正常区间',
  'EXPENSIVE':'历史偏高位', 'VERY_EXPENSIVE':'历史极高位', 'INSUFFICIENT_DATA':'资料不足',
}
def number(value):
    try: return float(value)
    except (TypeError, ValueError): return None
def range_text(low, high):
    low, high = number(low), number(high)
    if low is None or high is None: return '资料不足'
    return f'{low:.2f} – {high:.2f} 元'
def midpoint_text(current, midpoint):
    current, midpoint = number(current), number(midpoint)
    if current is None or midpoint is None or midpoint <= 0: return '资料不足'
    ratio = (current / midpoint - 1) * 100
    return f"{'高于' if ratio >= 0 else '低于'}估值中枢 {abs(ratio):.1f}%"
def support_text(current, supports):
    current = number(current)
    if current is None or not supports: return ('资料不足','资料不足')
    first = supports[0]
    low, high = number(first.get('low')), number(first.get('high'))
    if low is None or high is None: return ('资料不足','资料不足')
    zone = f'{low:.2f} – {high:.2f} 元'
    if current < low: return (zone, f'低于支撑区域 {(low/current-1)*100:.1f}%')
    if current > high: return (zone, f'高于支撑区域 {(current/high-1)*100:.1f}%')
    return (zone, '位于支撑区域内')

service = ValuePriceZoneService()
rows, failures = [], 0
try:
  for candidate in candidates:
    item = dict(candidate)
    try:
      zones = service.get_price_zones('CN', item['stock_code'], as_of=run['as_of'])
      valuation = dict(zones.get('valuation') or {})
      historical = dict(zones.get('historical_valuation') or {})
      metrics = dict(historical.get('historical_percentiles') or {})
      support, distance = support_text(zones.get('current_price'), list(zones.get('support_zones') or []))
      rows.append({
        'category': '历史深度低估' if item['presentation_status']=='DEEPLY_UNDERVALUED' else '历史低估关注',
        'stock_code': item['stock_code'], 'stock_name': item['stock_name'],
        'level1': item['level1_name'], 'level2': item['level2_name'], 'industry': item['level3_name'],
        'price': zones.get('current_price'), 'midpoint_position': midpoint_text(zones.get('current_price'), valuation.get('fair_value_mid')),
        'fair_mid': valuation.get('fair_value_mid'), 'fair_range': range_text(valuation.get('fair_value_low'), valuation.get('fair_value_high')),
        'support': support, 'support_distance': distance,
        'history': history_labels.get(historical.get('historical_valuation_status'), '资料不足'),
        'pe': dict(metrics.get('pe_ttm') or {}).get('current'), 'pb': dict(metrics.get('pb_mrq') or {}).get('current'),
        'dividend': dict(metrics.get('dividend_yield') or {}).get('current'),
        'price_judgment': status_labels.get(valuation.get('status'), '合理价值资料不足'),
      })
    except Exception:
      failures += 1
      rows.append({
        'category': '历史深度低估' if item['presentation_status']=='DEEPLY_UNDERVALUED' else '历史低估关注',
        'stock_code': item['stock_code'], 'stock_name': item['stock_name'],
        'level1': item['level1_name'], 'level2': item['level2_name'], 'industry': item['level3_name'],
        'price': None, 'midpoint_position':'资料不足', 'fair_mid':None, 'fair_range':'资料不足',
        'support':'资料不足', 'support_distance':'资料不足', 'history':history_labels.get(item['historical_valuation_status'],'资料不足'),
        'pe':None,'pb':None,'dividend':None,'price_judgment':'合理价值资料不足',
      })
finally:
  service.close()
print(json.dumps({'rows':rows,'as_of':run['as_of'],'failures':failures},ensure_ascii=False))
`;

const raw = execFileSync(python, ["-c", queryScript], {
  encoding: "utf8", env: { ...process.env, PYTHONIOENCODING: "utf-8" },
});
const data = JSON.parse(raw);
const rows = data.rows;
const lastRow = rows.length + 3;
const workbook = Workbook.create();
const sheet = workbook.worksheets.add("低估龙头");
sheet.showGridLines = false;

sheet.mergeCells("A1:P1");
sheet.getRange("A1").values = [["低估龙头 · 价格判断"]];
sheet.getRange("A1:P1").format = { fill: "#12355B", font: { bold: true, color: "#FFFFFF", size: 16 }, verticalAlignment: "center" };
sheet.getRange("A1:P1").format.rowHeight = 30;
sheet.mergeCells("A2:P2");
sheet.getRange("A2").values = [["数据说明：历史估值由公司自身市盈率、市净率和股息率的历史位置综合判断；合理价值区间基于已保存的行业可比估值；历史支撑来自前复权日K。空白或“资料不足”表示当前无法可靠计算。"]];
sheet.getRange("A2:P2").format = { fill: "#EAF2F8", font: { color: "#35546F" }, wrapText: true, verticalAlignment: "center" };
sheet.getRange("A2:P2").format.rowHeight = 34;
sheet.getRange("A3:P3").values = [["股票代码","公司名称","一级行业","二级行业","三级行业","当前价格","相对估值中枢","合理价值中枢","合理价值区间","最近历史支撑","距支撑区域","历史估值","市盈率","市净率","股息率","当前价格判断"]];
sheet.getRange("A3:P3").format = { fill: "#1F4E78", font: { bold: true, color: "#FFFFFF" }, horizontalAlignment: "center", verticalAlignment: "center", wrapText: true, borders: { preset: "all", style: "medium", color: "#6F8FA8" } };
sheet.getRange("A3:P3").format.rowHeight = 34;
sheet.getRange(`A4:P${lastRow}`).values = rows.map((row) => [
  row.stock_code,row.stock_name,row.level1,row.level2,row.industry,row.price,row.midpoint_position,row.fair_mid,row.fair_range,
  row.support,row.support_distance,row.history,row.pe,row.pb,row.dividend == null ? null : row.dividend/100,row.price_judgment,
]);
sheet.getRange(`A4:P${lastRow}`).format = { verticalAlignment: "center", wrapText: true, borders: { preset: "all", style: "medium", color: "#9AAEBD" } };
sheet.getRange(`F4:F${lastRow}`).format.numberFormat = "0.00";
sheet.getRange(`H4:H${lastRow}`).format.numberFormat = "0.00";
sheet.getRange(`M4:N${lastRow}`).format.numberFormat = "0.00";
sheet.getRange(`O4:O${lastRow}`).format.numberFormat = "0.0%";
sheet.getRange(`A4:A${lastRow}`).format.font = { name: "JetBrains Mono", color: "#234A6B" };
sheet.getRange(`F4:H${lastRow}`).format.horizontalAlignment = "right";
sheet.freezePanes.freezeRows(3);
sheet.getRange(`A3:P${lastRow}`).format.autofitRows();
const widths = [15,16,14,16,20,12,18,14,20,20,18,16,11,11,11,22];
widths.forEach((width,index) => { sheet.getRangeByIndexes(0,index,1,1).format.columnWidth = width; });

await fs.mkdir(outputDir, { recursive: true });
const preview = await workbook.render({ sheetName:"低估龙头", range:"A1:P14", scale:1.2, format:"png" });
await fs.writeFile(`${outputDir}/list_preview.png`, new Uint8Array(await preview.arrayBuffer()));
const tableCheck = await workbook.inspect({kind:"table",range:"低估龙头!A1:P8",include:"values,formulas",tableMaxRows:8,tableMaxCols:16});
const formulaErrors = await workbook.inspect({kind:"match",searchTerm:"#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",options:{useRegex:true,maxResults:20},summary:"formula error scan"});
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(`${outputDir}/低估龙头.xlsx`);
console.log(JSON.stringify({output:`${outputDir}/低估龙头.xlsx`,rows:rows.length,asOf:data.as_of,failures:data.failures,tableCheck:tableCheck.ndjson,formulaErrors:formulaErrors.ndjson},null,2));
