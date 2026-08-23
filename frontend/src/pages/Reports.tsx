import { useEffect, useState } from "react";
import { Download, FileSpreadsheet } from "lucide-react";
import { apiGet, downloadAuthorized } from "@/lib/api";
import type { Keyword } from "@/types";

export default function Reports() {
  const [keywords, setKeywords] = useState<Keyword[]>([]); const [message, setMessage] = useState("");
  useEffect(() => { apiGet<any>("/keywords").then((r) => setKeywords(r.data || [])); }, []);
  const ready = keywords.filter((x) => x.latest_run && ["completed","partial"].includes(x.latest_run.status));
  const download = async (item: Keyword) => { try { await downloadAuthorized(`/runs/${item.latest_run!.id}/report.xlsx`, `${item.keyword}_MY_marketplace.xlsx`); setMessage("报告已下载"); } catch (e: any) { setMessage(e.message); } };
  return <div className="page-stack"><section className="section-heading"><div><span className="eyebrow">PORTABLE WORKBOOKS</span><h2>Excel 报告</h2><p>综合结论、双平台竞品、每日趋势和数据口径说明一次打包。</p></div></section>{message && <div className="info-box">{message}</div>}<div className="report-grid">{ready.map((item) => <article className="report-card panel" key={item.id}><div className="file-icon"><FileSpreadsheet /></div><div><span>MY MARKETPLACE · XLSX</span><h3>{item.keyword}</h3><p>{item.latest_run!.analysis?.counts?.shopee || 0} Shopee + {item.latest_run!.analysis?.counts?.lazada || 0} Lazada</p></div><div className="report-score"><strong>{item.latest_run!.opportunity_score ?? "—"}</strong><span>{item.latest_run!.verdict}</span></div><button onClick={() => download(item)}><Download />下载</button></article>)}{ready.length === 0 && <div className="empty-state panel">还没有可下载的报告。</div>}</div></div>;
}
