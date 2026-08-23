import { useEffect, useState } from "react";
import { AlertCircle, ArrowRight, CheckCircle2, Gauge, Sparkles } from "lucide-react";
import { apiGet } from "@/lib/api";
import type { Keyword, OpportunitySegment, PlatformScore } from "@/types";

const dimLabels: Record<string,string> = { demand: "需求信号", entry_ease: "进入可行性", price_room: "价格空间" };

export default function AIAnalysis() {
  const [keywords, setKeywords] = useState<Keyword[]>([]);
  const [keywordId, setKeywordId] = useState(0);

  useEffect(() => {
    apiGet<any>("/keywords").then((r) => {
      setKeywords(r.data || []);
      if (r.data?.[0]) setKeywordId(r.data[0].id);
    });
  }, []);

  const item = keywords.find((x) => x.id === keywordId);
  const run = item?.latest_run;
  const analysis = run?.analysis || {};
  const segments = (analysis.opportunity_segments || []) as OpportunitySegment[];
  const supportedSegments = segments.filter((segment) => segment.evidence_status === "supported").length;

  return <div className="page-stack">
    <section className="section-heading">
      <div>
        <span className="eyebrow">EVIDENCE-GATED HEURISTIC</span>
        <h2>分析建议</h2>
        <p>缺少核心公开字段时不放大剩余权重；配件、多件装和低相关结果不进入评分，卖家集中度只在有稳定卖家标识时启用。</p>
      </div>
      <select value={keywordId} onChange={(e) => setKeywordId(Number(e.target.value))}>
        {keywords.map((x) => <option value={x.id} key={x.id}>{x.keyword}</option>)}
      </select>
    </section>

    {!run || !["completed","partial"].includes(run.status) ? <div className="empty-state panel">请选择一个已完成的分析任务。</div> : <>
      <section className="score-banner panel">
        <div className="score-orb"><span>机会分</span><strong>{run.opportunity_score ?? "—"}</strong><small>完整度 {run.confidence ?? 0}%</small></div>
        <div><span className="eyebrow">VERDICT</span><h3>{run.verdict}</h3><p>{analysis.methodology}</p></div>
        <Gauge />
      </section>

      <section className="platform-analysis">
        {Object.entries(run.platform_scores || {}).map(([name, score]) => <PlatformCard key={name} name={name} score={score} />)}
      </section>

      {segments.length > 0 && <section className="panel">
        <div className="panel-title"><div><span>PRODUCT FAMILIES</span><h3>商品族候选排序</h3></div><Sparkles /></div>
        <p className="method-note"><AlertCircle /> 按搜索标题里反复出现的属性做轻量拆分，用来缩小验证范围，不等同于 Shopee / Lazada 官方类目。只有跨所选平台、样本量和完整度都达标的子方向才标记为“证据支持”；其余仅是探索候选。</p>
        <div className="table-shell">
          <table>
            <thead><tr><th>#</th><th>商品族</th><th>机会分</th><th>样本</th><th>平台证据</th><th>中位价</th><th>卖家集中度</th><th>代表商品</th></tr></thead>
            <tbody>{segments.map((segment, index) => {
              const supported = segment.evidence_status === "supported";
              return <tr key={`${segment.label}-${index}`}>
                <td><b>#{index + 1}</b></td>
                <td><strong>{segment.label}</strong><small>{supported ? `${segment.verdict} · 证据支持` : "探索候选，不作备货结论"} · 完整度 {segment.confidence}%</small></td>
                <td><strong>{segment.opportunity_score}</strong><small>{supported ? "可进入下一步验证" : "仅用于候选排序"}</small></td>
                <td>{segment.sample_size} 条<small>占相关结果 {segment.share}%</small></td>
                <td>{segment.platform_coverage}%<small>{supported ? "所选平台均有充分证据" : "证据尚未完全覆盖"}</small></td>
                <td>{segment.median_price == null ? "—" : `RM ${segment.median_price.toFixed(2)}`}</td>
                <td>{segment.seller_concentration == null ? "证据不足" : `${segment.seller_concentration.toFixed(1)} / 100`}</td>
                <td>{segment.representative_titles.slice(0, 2).map((title) => <small key={title}>{title}</small>)}</td>
              </tr>;
            })}</tbody>
          </table>
        </div>
        <p className="method-note"><AlertCircle /> 当前 {segments.length} 个候选中，{supportedSegments} 个达到“证据支持”门槛。</p>
      </section>}

      <section className="panel recommendation-panel">
        <div className="panel-title"><div><span>ACTION NOTES</span><h3>下一步建议</h3></div><Sparkles /></div>
        <div className="recommendation-list">
          {(analysis.recommendations || []).map((text: string, i: number) => <article key={i}><CheckCircle2 /><span>{text}</span><ArrowRight /></article>)}
        </div>
        <div className="method-note"><AlertCircle /> 机会分不是利润预测；下单前仍需核算采购、物流、平台费用、广告成本，并确认被拆分的商品族在供应链上确实可独立销售。</div>
      </section>
    </>}
  </div>;
}

function PlatformCard({ name, score }: { name: string; score: PlatformScore }) {
  const raw = score.raw_sample_size ?? score.sample_size;
  const excluded = score.exclusion_breakdown || {};
  const excludedParts = [
    excluded.accessory ? `配件/替换件 ${excluded.accessory}` : "",
    excluded.bundle ? `套装/多件装 ${excluded.bundle}` : "",
    excluded.low_relevance ? `低相关 ${excluded.low_relevance}` : "",
  ].filter(Boolean);
  const sellerConcentration = score.metrics?.seller_concentration;
  const sellerCount = score.metrics?.seller_count;
  const sellerCoverage = score.metrics?.seller_identity_coverage;

  return <article className={`panel platform-analysis-card ${name}`}>
    <div>
      <span className={`platform-tag ${name}`}>{name}</span>
      <strong>{score.eligible === false ? "—" : (score.score ?? "—")}</strong>
      <small>{score.verdict} · {score.sample_size}/{raw} 条相关样本 · 完整度 {score.confidence}%</small>
      {excludedParts.length > 0 && <small>已排除：{excludedParts.join(" · ")}</small>}
      {sellerConcentration != null
        ? <small>卖家集中度 {sellerConcentration.toFixed(1)}/100 · 可识别卖家 {sellerCount ?? "—"} 个</small>
        : <small>卖家集中度暂不参与：卖家标识覆盖 {sellerCoverage ?? 0}%</small>}
      {score.eligible === false && score.eligibility_reasons?.length ? <small>{score.eligibility_reasons.slice(0, 2).join("；")}</small> : null}
    </div>
    <div className="dimension-bars">
      {Object.entries(score.dimensions || {}).map(([key, value]) => <label key={key}>
        <span>{dimLabels[key]}</span>
        <div><i style={{ width: `${value ?? 0}%` }} /></div>
        <b>{value ?? "—"}</b>
      </label>)}
    </div>
  </article>;
}
