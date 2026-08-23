import { useState } from "react";
import { AlertCircle, Download, FileSpreadsheet, LoaderCircle, RefreshCw } from "lucide-react";
import { useKeywordSummaries } from "@/hooks/useKeywordSummaries";
import { downloadAuthorized } from "@/lib/api";
import type { Keyword, RunSummary } from "@/types";

export default function Reports() {
  const { keywords, loading, error, refresh } = useKeywordSummaries();
  const [message, setMessage] = useState("");

  const ready = keywords
    .map((item) => ({ item, run: item.latest_result_run || null }))
    .filter((entry): entry is { item: Keyword; run: RunSummary } => Boolean(entry.run));

  const download = async (item: Keyword, run: RunSummary) => {
    try {
      await downloadAuthorized(`/runs/${run.id}/report.xlsx`, `${item.keyword}_MY_marketplace.xlsx`);
      setMessage("报告已下载");
    } catch (e: any) {
      setMessage(e.message || "下载失败");
    }
  };

  return <div className="page-stack">
    <section className="section-heading"><div><span className="eyebrow">PORTABLE WORKBOOKS</span><h2>Excel 报告</h2><p>始终保留最近一次可用结果；新任务正在运行或失败时不会把上一份报告隐藏掉，新稳定结果完成后会自动刷新。</p></div></section>
    {message && <div className="info-box">{message}</div>}
    {loading && <div className="data-state panel" role="status"><LoaderCircle className="state-spinner" /><div><strong>正在读取可用报告</strong><span>只同步报告摘要，不重复下载完整分析。</span></div></div>}
    {!loading && error && <div className="data-state error-state panel" role="alert"><AlertCircle /><div><strong>报告列表加载失败</strong><span>{error}</span></div><button onClick={() => refresh().catch(() => {})}><RefreshCw />重新加载</button></div>}
    <div className="report-grid">
      {!loading && !error && ready.map(({ item, run }) => {
        const counts = run.analysis?.counts || (run as any).counts || {};
        return <article className="report-card panel" key={item.id}>
        <div className="file-icon"><FileSpreadsheet /></div>
        <div><span>MY MARKETPLACE · XLSX</span><h3>{item.keyword}</h3><p>{counts.shopee ?? "—"} Shopee + {counts.lazada ?? "—"} Lazada</p></div>
        <div className="report-score"><strong>{run.opportunity_score ?? "—"}</strong><span>{run.verdict}</span></div>
        <button onClick={() => download(item, run)}><Download />下载</button>
      </article>})}
      {!loading && !error && ready.length === 0 && <div className="empty-state panel">还没有可下载的报告。</div>}
    </div>
  </div>;
}
