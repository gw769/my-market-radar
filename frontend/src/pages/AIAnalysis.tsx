import { useEffect, useState } from "react";
import { AlertCircle, ArrowRight, CheckCircle2, Gauge, Sparkles } from "lucide-react";
import { apiGet } from "@/lib/api";
import type { Keyword, PlatformScore } from "@/types";

const dimLabels: Record<string,string> = { demand: "需求信号", entry_ease: "进入可行性", price_room: "价格空间" };
export default function AIAnalysis() {
  const [keywords, setKeywords] = useState<Keyword[]>([]); const [keywordId, setKeywordId] = useState(0);
  useEffect(() => { apiGet<any>("/keywords").then((r) => { setKeywords(r.data || []); if (r.data?.[0]) setKeywordId(r.data[0].id); }); }, []);
  const item = keywords.find((x) => x.id === keywordId); const run = item?.latest_run; const analysis = run?.analysis || {};
  return <div className="page-stack"><section className="section-heading"><div><span className="eyebrow">EXPLAINABLE HEURISTIC</span><h2>分析建议</h2><p>规则引擎可复现；缺少公开字段时降低完整度，不补造数据。</p></div><select value={keywordId} onChange={(e) => setKeywordId(Number(e.target.value))}>{keywords.map((x) => <option value={x.id} key={x.id}>{x.keyword}</option>)}</select></section>
    {!run || !["completed","partial"].includes(run.status) ? <div className="empty-state panel">请选择一个已完成的分析任务。</div> : <>
      <section className="score-banner panel"><div className="score-orb"><span>机会分</span><strong>{run.opportunity_score ?? "—"}</strong><small>完整度 {run.confidence || 0}%</small></div><div><span className="eyebrow">VERDICT</span><h3>{run.verdict}</h3><p>{analysis.methodology}</p></div><Gauge /></section>
      <section className="platform-analysis">{Object.entries(run.platform_scores || {}).map(([name, score]) => <PlatformCard key={name} name={name} score={score} />)}</section>
      <section className="panel recommendation-panel"><div className="panel-title"><div><span>ACTION NOTES</span><h3>下一步建议</h3></div><Sparkles /></div><div className="recommendation-list">{(analysis.recommendations || []).map((text: string, i: number) => <article key={i}><CheckCircle2 /><span>{text}</span><ArrowRight /></article>)}</div><div className="method-note"><AlertCircle /> 机会分不是利润预测；下单前仍需核算采购、物流、平台费用与广告成本。</div></section>
    </>}
  </div>;
}
function PlatformCard({ name, score }: { name: string; score: PlatformScore }) { return <article className={`panel platform-analysis-card ${name}`}><div><span className={`platform-tag ${name}`}>{name}</span><strong>{score.score ?? "—"}</strong><small>{score.verdict} · {score.sample_size} 条有效样本 · 完整度 {score.confidence}%</small></div><div className="dimension-bars">{Object.entries(score.dimensions || {}).map(([key, value]) => <label key={key}><span>{dimLabels[key]}</span><div><i style={{ width: `${value || 0}%` }} /></div><b>{value ?? "—"}</b></label>)}</div></article>; }
