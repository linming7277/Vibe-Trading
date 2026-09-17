import fs from "node:fs/promises";
import { DatabaseSync } from "node:sqlite";
import { Workbook, SpreadsheetFile } from "@oai/artifact-tool";

const dbPath = "C:/Users/Administrator/.vibe-trading/research.db";
const tdxDbPath = "C:/Users/Administrator/.vibe-trading/tdx_data.db";
const outputDir = "D:/AI/hzstock/outputs/value-l3-leader-export-20260822";
const outputPath = `${outputDir}/当前三级行业龙头池_2026-08-21.xlsx`;
const previewPath = `${outputDir}/当前三级行业龙头池_2026-08-21.png`;

const db = new DatabaseSync(dbPath, { readOnly: true });
const pool = db.prepare(`
  SELECT id, as_of, company_count, current_membership_count, terminal_industry_count, formula_version
  FROM l3_leader_pool_runs
  WHERE status = 'COMPLETED'
  ORDER BY as_of DESC, completed_at DESC
  LIMIT 1
`).get();

if (!pool) {
  throw new Error("未找到已完成的当前三级行业龙头池快照。");
}

const leaders = db.prepare(`
  SELECT
    stock_code,
    stock_name,
    level1_name,
    level2_name,
    level3_name,
    leader_rank,
    leader_score,
    component_scores_json,
    coverage,
    lifecycle_status
  FROM l3_leader_pool_members
  WHERE pool_id = ?
    AND lifecycle_status IN ('ACTIVE', 'NEW', 'REENTERED')
  ORDER BY leader_score DESC, level1_code, level2_code, level3_code, leader_rank, stock_code
`).all(pool.id);
db.close();

const tdxDb = new DatabaseSync(tdxDbPath, { readOnly: true });
const fundamentals = tdxDb.prepare(`
  SELECT record_key, payload_json
  FROM records
  WHERE dataset = 'fundamentals'
`).all();
const securityDetails = tdxDb.prepare(`
  SELECT record_key, payload_json
  FROM records
  WHERE dataset = 'security_details'
`).all();
tdxDb.close();

function parsePayload(payloadJson) {
  try {
    return JSON.parse(payloadJson || "{}");
  } catch {
    return {};
  }
}

const fundamentalsByCode = new Map(fundamentals.map((row) => [row.record_key, parsePayload(row.payload_json)]));
const detailsByCode = new Map(securityDetails.map((row) => [row.record_key, parsePayload(row.payload_json)]));

function firstText(...values) {
  return values.map((value) => String(value || "").trim()).find((value) => value && !["--", "0", "0.0"].includes(value)) || "";
}

function compactText(value, maxLength = 160) {
  const cleaned = String(value || "").replace(/\s+/g, " ").trim();
  return cleaned.length > maxLength ? `${cleaned.slice(0, maxLength - 1)}…` : cleaned;
}

function companyIntroduction(stockCode) {
  const fundamental = fundamentalsByCode.get(stockCode) || {};
  const detail = detailsByCode.get(stockCode) || {};
  const base = fundamental.base_raw || {};
  const extended = fundamental.extended_raw || {};
  const detailExtended = detail.extended || {};
  const description = firstText(
    detailExtended.CompanyDescription,
    detailExtended.CompanyIntroduction,
    detailExtended.CompanyIntro,
    detailExtended.GSJJ,
    extended.CompanyDescription,
    extended.CompanyIntroduction,
    extended.CompanyIntro,
    extended.GSJJ,
    base.CompanyDescription,
    base.CompanyIntroduction,
    base.CompanyIntro,
    base.GSJJ,
  );
  const mainBusiness = firstText(
    fundamental.main_business,
    detailExtended.MainBusiness,
    detailExtended.ZYYW,
    extended.MainBusiness,
    extended.ZYYW,
    base.MainBusiness,
    base.ZYYW,
  );
  const products = firstText(detailExtended.MainProducts, detailExtended.MainProduct, detailExtended.Products, detailExtended.ZYCP,
    extended.MainProducts, extended.MainProduct, extended.Products, extended.ZYCP,
    base.MainProducts, base.MainProduct, base.Products, base.ZYCP);
  const businessScope = firstText(detailExtended.BusinessScope, detailExtended.BusinessRange, detailExtended.OperationScope,
    extended.BusinessScope, extended.BusinessRange, extended.OperationScope,
    base.BusinessScope, base.BusinessRange, base.OperationScope);
  if (description) return compactText(description);
  if (mainBusiness) return compactText(`主营业务：${mainBusiness}`);
  if (products) return compactText(`主要产品：${products}`);
  if (businessScope) return compactText(`经营范围：${businessScope}`);
  return "本地暂无可靠公司简介";
}

function scoreExplanation(row) {
  let score = {};
  try { score = JSON.parse(row.component_scores_json || "{}"); } catch { score = {}; }
  const format = (key) => score[key] == null ? "—" : Number(score[key]).toFixed(1);
  return [
    `综合 ${Number(row.leader_score || 0).toFixed(2)}/100`,
    `行业地位 ${format("industry_position")}`,
    `盈利质量 ${format("profitability")}`,
    `增长稳定 ${format("growth_stability")}`,
    `现金流 ${format("cash_flow")}`,
    `估值 ${format("valuation")}`,
    `治理风险 ${format("governance_risk")}`,
  ].join("；");
}

const uniqueCompanyCount = new Set(leaders.map((row) => row.stock_code)).size;
const uniqueTrackCount = new Set(leaders.map((row) => row.level3_name)).size;
const header = [
  "序号",
  "股票代码",
  "股票名称",
  "一级行业",
  "二级行业",
  "细分赛道（三级/末级）",
  "赛道内龙头排名",
  "综合评分",
  "评分说明（构成项）",
  "覆盖率",
  "状态",
  "公司简介（本地通达信资料）",
];
const rows = leaders.map((row, index) => [
  index + 1,
  row.stock_code,
  row.stock_name,
  row.level1_name,
  row.level2_name,
  row.level3_name,
  row.leader_rank,
  row.leader_score == null ? null : Number(row.leader_score),
  scoreExplanation(row),
  row.coverage == null ? null : Number(row.coverage),
  row.lifecycle_status === "ACTIVE" ? "在池" : row.lifecycle_status,
  companyIntroduction(row.stock_code),
]);
const introductionAvailable = rows.filter((row) => row[11] !== "本地暂无可靠公司简介").length;

const workbook = Workbook.create();
const sheet = workbook.worksheets.add("龙头列表");
sheet.showGridLines = false;

sheet.mergeCells("A1:L1");
sheet.getRange("A1").values = [[`当前三级行业龙头池（${pool.as_of}）`]];
sheet.getRange("A1:L1").format.fill = "#17365D";
sheet.getRange("A1:L1").format.font = { bold: true, color: "#FFFFFF", size: 16 };
sheet.getRange("A1:L1").format.horizontalAlignment = "center";
sheet.getRange("A1:L1").format.verticalAlignment = "center";
sheet.getRange("A1:L1").format.rowHeight = 30;

sheet.getRange("A2:L2").values = [[
  "数据口径：", `当前 L3 Leader Pool 快照；数据日期 ${pool.as_of}`,
  "细分赛道：", uniqueTrackCount,
  "龙头关系：", leaders.length,
  "去重公司：", uniqueCompanyCount,
  "简介可用：", introductionAvailable,
  "公式版本：", pool.formula_version,
]];
sheet.getRange("A2:L2").format.fill = "#EAF1FB";
sheet.getRange("A2:L2").format.font = { color: "#244062", size: 10 };
sheet.getRange("A2:L2").format.rowHeight = 21;
sheet.getRange("A2").format.font = { bold: true, color: "#244062", size: 10 };
sheet.getRange("C2").format.font = { bold: true, color: "#244062", size: 10 };
sheet.getRange("E2").format.font = { bold: true, color: "#244062", size: 10 };
sheet.getRange("G2").format.font = { bold: true, color: "#244062", size: 10 };
sheet.getRange("I2").format.font = { bold: true, color: "#244062", size: 10 };
sheet.getRange("K2").format.font = { bold: true, color: "#244062", size: 10 };

sheet.mergeCells("A3:L3");
sheet.getRange("A3").values = [["说明：每一行代表一只公司在一个三级/末级细分赛道中的龙头席位；同一公司如覆盖多个赛道，会保留多行。按综合评分从高到低排序。"]];
sheet.getRange("A3:L3").format.fill = "#F8FAFD";
sheet.getRange("A3:L3").format.font = { color: "#5B6573", italic: true, size: 10 };
sheet.getRange("A3:L3").format.wrapText = true;
sheet.getRange("A3:L3").format.rowHeight = 30;

sheet.mergeCells("A4:L4");
sheet.getRange("A4").values = [["评分说明：综合评分用于候选筛选和同池相对排序（满分 100），由行业地位、盈利质量、增长稳定性、现金流、估值与治理风险构成；不是买卖建议。"]];
sheet.getRange("A4:L4").format.fill = "#FFF8E7";
sheet.getRange("A4:L4").format.font = { color: "#7A4E00", italic: true, size: 10 };
sheet.getRange("A4:L4").format.wrapText = true;
sheet.getRange("A4:L4").format.rowHeight = 30;

const headerRow = 5;
const firstDataRow = headerRow + 1;
const lastDataRow = firstDataRow + rows.length - 1;
sheet.getRange(`A${headerRow}:L${headerRow}`).values = [header];
sheet.getRange(`A${headerRow}:L${headerRow}`).format.fill = "#244062";
sheet.getRange(`A${headerRow}:L${headerRow}`).format.font = { bold: true, color: "#FFFFFF", size: 10 };
sheet.getRange(`A${headerRow}:L${headerRow}`).format.horizontalAlignment = "center";
sheet.getRange(`A${headerRow}:L${headerRow}`).format.verticalAlignment = "center";
sheet.getRange(`A${headerRow}:L${headerRow}`).format.wrapText = true;
sheet.getRange(`A${headerRow}:L${headerRow}`).format.rowHeight = 28;
sheet.getRange(`A${firstDataRow}:L${lastDataRow}`).values = rows;
sheet.getRange(`A${firstDataRow}:L${lastDataRow}`).format.font = { color: "#1F2937", size: 10 };
sheet.getRange(`A${firstDataRow}:L${lastDataRow}`).format.verticalAlignment = "center";
sheet.getRange(`A${firstDataRow}:L${lastDataRow}`).format.borders = { preset: "all", style: "thin", color: "#D9E2F3" };
sheet.getRange(`A${firstDataRow}:A${lastDataRow}`).format.horizontalAlignment = "center";
sheet.getRange(`B${firstDataRow}:B${lastDataRow}`).format.horizontalAlignment = "center";
sheet.getRange(`G${firstDataRow}:H${lastDataRow}`).format.horizontalAlignment = "center";
sheet.getRange(`J${firstDataRow}:K${lastDataRow}`).format.horizontalAlignment = "center";
sheet.getRange(`K${firstDataRow}:K${lastDataRow}`).format.horizontalAlignment = "center";
sheet.getRange(`I${firstDataRow}:I${lastDataRow}`).format.wrapText = true;
sheet.getRange(`L${firstDataRow}:L${lastDataRow}`).format.wrapText = true;
sheet.getRange(`H${firstDataRow}:H${lastDataRow}`).format.numberFormat = "0.00";
sheet.getRange(`J${firstDataRow}:J${lastDataRow}`).format.numberFormat = "0.0%";
sheet.getRange(`A${firstDataRow}:L${lastDataRow}`).format.rowHeight = 34;

sheet.getRange(`A${firstDataRow}:L${lastDataRow}`).conditionalFormats.add("custom", {
  formula: `=MOD(ROW(),2)=0`,
  format: { fill: "#F7FAFF" },
});
sheet.getRange(`H${firstDataRow}:H${lastDataRow}`).conditionalFormats.add("colorScale", {
  colors: ["#FEE2E2", "#FEF3C7", "#DCFCE7"],
});

sheet.getRange("A:A").format.columnWidth = 7;
sheet.getRange("B:B").format.columnWidth = 14;
sheet.getRange("C:C").format.columnWidth = 16;
sheet.getRange("D:D").format.columnWidth = 15;
sheet.getRange("E:E").format.columnWidth = 18;
sheet.getRange("F:F").format.columnWidth = 24;
sheet.getRange("G:G").format.columnWidth = 15;
sheet.getRange("H:H").format.columnWidth = 12;
sheet.getRange("I:I").format.columnWidth = 58;
sheet.getRange("J:J").format.columnWidth = 10;
sheet.getRange("K:K").format.columnWidth = 10;
sheet.getRange("L:L").format.columnWidth = 46;
sheet.freezePanes.freezeRows(headerRow);

const table = sheet.tables.add(`A${headerRow}:L${lastDataRow}`, true, "ValueL3Leaders");
table.name = "ValueL3Leaders";

const inspection = await workbook.inspect({
  kind: "table",
  range: `龙头列表!A1:L${Math.min(lastDataRow, 16)}`,
  include: "values,formulas",
});
const formulaErrors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { use_regex: true, max_results: 100 },
  summary: "formula error scan",
});

await fs.mkdir(outputDir, { recursive: true });
const preview = await workbook.render({
  sheetName: "龙头列表",
  range: `A1:L15`,
  scale: 1.4,
  format: "png",
});
await fs.writeFile(previewPath, new Uint8Array(await preview.arrayBuffer()));

const xlsx = await SpreadsheetFile.exportXlsx(workbook);
await xlsx.save(outputPath);

console.log(JSON.stringify({
  outputPath,
  previewPath,
  pool: { asOf: pool.as_of, leaderRelations: leaders.length, uniqueCompanyCount, uniqueTrackCount, introductionAvailable },
  inspection,
  formulaErrors,
}, null, 2));
