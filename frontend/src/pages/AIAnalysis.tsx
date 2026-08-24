import { useEffect, useState } from "react";
import { Activity, AlertCircle, ArrowRight, CheckCircle2, Eye, Gauge, LoaderCircle, RefreshCw, Sparkles } from "lucide-react";
import { apiGet } from "@/lib/api";
import { useKeywordSummaries } from "@/hooks/useKeywordSummaries";
import type { AIInsight, KeywordLocalization, OpportunitySegment, PlatformScore, Run } from "@/types";

const dimLabels: Record<string,string> = { demand: "需求信号", entry_ease: "进入可行性", price_room: "价格空间" };
const RESULT_STATUSES = new Set(["completed", "partial"]);

export default function AIAnalysis() {
  const { keywords, loading: keywordsLoading, error: keywordsError, refresh } = useKeywordSummaries();
  const [keywordId, setKeywordId] = useState(0);
  const [run, setRun] = useState<Run | null>(null);
  const [trend, setTrend] = useState<any>(null);
  const [runLoading, setRunLoading] = useState(false);
  const [runError, setRunError] = useState("");
  const [loadedRunId, setLoadedRunId] = useState<number | null>(null);
  const [runReloadKey, setRunReloadKey] = useState(0);
  const [trendLoading, setTrendLoading] = useState(false);
  const [trendError, setTrendError] = useState("");
  const [loadedTrendKey, setLoadedTrendKey] = useState("");
  const [trendReloadKey, setTrendReloadKey] = useState(0);

  useEffect(() => {
    setKeywordId((current) => keywords.some((keyword) => keyword.id === current) ? current : (keywords[0]?.id || 0));
  }, [keywords]);

  const item = keywords.find((x) => x.id === keywordId);
  const latestRun = item?.latest_run;
  const stableRunId = latestRun && RESULT_STATUSES.has(latestRun.status)
    ? latestRun.id
    : item?.latest_result_run?.id;
  const trendRequestKey = keywordId && stableRunId ? `${keywordId}:${stableRunId}` : "";

  useEffect(() => {
    if (!stableRunId) {
      setRun(null);
      setLoadedRunId(null);
      setRunLoading(false);
      setRunError("");
      return;
    }

    const controller = new AbortController();
    setRun(null);
    setRunLoading(true);
    setRunError("");
    apiGet<any>(`/runs/${stableRunId}`, { signal: controller.signal })
      .then((runResponse) => {
        setRun(runResponse.data || null);
        setLoadedRunId(stableRunId);
      })
      .catch((reason: unknown) => {
        if (reason instanceof Error && reason.name === "AbortError") return;
        setLoadedRunId(stableRunId);
        setRunError(reason instanceof Error ? reason.message : "分析详情加载失败");
      })
      .finally(() => {
        if (!controller.signal.aborted) setRunLoading(false);
      });
    return () => controller.abort();
  }, [stableRunId, runReloadKey]);

  useEffect(() => {
    if (!keywordId || !stableRunId || !trendRequestKey) {
      setTrend(null);
      setLoadedTrendKey("");
      setTrendLoading(false);
      setTrendError("");
      return;
    }

    const controller = new AbortController();
    setTrend(null);
    setTrendLoading(true);
    setTrendError("");
    apiGet<any>(`/trends/keywords/${keywordId}`, { signal: controller.signal })
      .then((trendResponse) => {
        setTrend(trendResponse.data || null);
        setLoadedTrendKey(trendRequestKey);
      })
      .catch((reason: unknown) => {
        if (reason instanceof Error && reason.name === "AbortError") return;
        setLoadedTrendKey(trendRequestKey);
        setTrendError(reason instanceof Error ? reason.message : "近期趋势加载失败");
      })
      .finally(() => {
        if (!controller.signal.aborted) setTrendLoading(false);
      });
    return () => controller.abort();
  }, [keywordId, stableRunId, trendRequestKey, trendReloadKey]);

  const analysis = run?.analysis || {};
  const segments = (analysis.opportunity_segments || []) as OpportunitySegment[];
  const evidence = analysis.evidence || null;
  const collector = analysis.collector_health || null;
  const aiInsight = (analysis.ai || null) as AIInsight | null;
  const aiNextSteps = aiInsight?.status === "completed" ? (aiInsight.next_steps || []) : [];
  const localization = (analysis.request_config?.localization || null) as KeywordLocalization | null;
  const showingOlderResult = Boolean(latestRun && stableRunId && latestRun.id !== stableRunId);
  const loading = keywordsLoading || runLoading || Boolean(stableRunId && loadedRunId !== stableRunId);
  const visibleRunError = loadedRunId === stableRunId ? runError : "";
  const pageError = keywordsError || visibleRunError;
  const visibleTrend = loadedTrendKey === trendRequestKey ? trend : null;
  const visibleTrendError = loadedTrendKey === trendRequestKey ? trendError : "";
  const trendPending = Boolean(run && trendRequestKey && (trendLoading || loadedTrendKey !== trendRequestKey));
  const retryRun = () => {
    if (keywordsError) refresh().catch(() => {});
    setLoadedRunId(null);
    setRunLoading(true);
    setRunError("");
    setRunReloadKey((value) => value + 1);
  };
  const retryTrend = () => {
    setLoadedTrendKey("");
    setTrendLoading(true);
    setTrendError("");
    setTrendReloadKey((value) => value + 1);
  };

  return <div className="page-stack">
    <section className="section-heading">
      <div>
        <span className="eyebrow">EVIDENCE-GATED HEURISTIC</span>
        <h2>分析建议</h2>
        <p>缺少核心公开字段时不放大剩余权重；配件、多件装和低相关结果不进入评分，并独立检查采集器健康度与跨快照近期动量。</p>
      </div>
      <select value={keywordId} onChange={(e) => setKeywordId(Number(e.target.value))}>
        {keywords.map((x) => <option value={x.id} key={x.id}>{x.keyword}</option>)}
      </select>
    </section>

    {loading && <div className="data-state panel" role="status"><LoaderCircle className="state-spinner" /><div><strong>正在加载分析证据</strong><span>先读取机会分与平台结论，近期趋势独立加载。</span></div></div>}
    {!loading && pageError && <div className="data-state error-state panel" role="alert"><AlertCircle /><div><strong>分析详情加载失败</strong><span>{pageError}</span></div><button onClick={retryRun}><RefreshCw />重新加载</button></div>}

    {!loading && !pageError && showingOlderResult && <div className="info-box">最新任务状态：{latestRun?.status}。当前先展示最近一次可用分析结果；采集中任务每 3 秒检查，等待验证或稳定后降低刷新频率。</div>}

    {!loading && !pageError && (!item || !run) ? <div className="empty-state panel">{item ? "这个关键词还没有可用的分析结果。" : "还没有可分析的关键词。"}</div> : null}
    {!loading && !pageError && run ? <>
      <section className="score-banner panel">
        <div className="score-orb"><span>机会分</span><strong>{run.opportunity_score ?? "—"}</strong><small>完整度 {run.confidence ?? 0}%</small></div>
        <div><span className="eyebrow">VERDICT</span><h3>{run.verdict}</h3><p>{analysis.methodology}</p>{localization && <small className="localized-query">实际搜索词：{localization.search_term} · 同义词 {localization.aliases.join(" / ")}</small>}</div>
        <Gauge />
      </section>

      {aiInsight && <section className="panel ai-insight-panel">
        <div className="panel-title"><div><span>BOUNDED AI INTERPRETATION</span><h3>AI 经营解读</h3></div><Sparkles /></div>
        {aiInsight.status === "completed" ? <>
          <p className="ai-insight-summary">{aiInsight.summary}</p>
          <div className="ai-insight-grid">
            <div><strong>公开信号</strong>{(aiInsight.findings || []).map((text) => <span key={text}>{text}</span>)}</div>
            <div><strong>主要风险</strong>{(aiInsight.risks || []).map((text) => <span key={text}>{text}</span>)}</div>
            <div><strong>验证动作</strong>{(aiInsight.actions || []).map((text) => <span key={text}>{text}</span>)}</div>
          </div>
          <div className="method-note"><AlertCircle /> {aiInsight.model} 只读取聚合公开字段；没有修改机会分、证据等级或规则结论。</div>
        </> : <div className="method-note"><AlertCircle /> {aiInsight.message || "AI 解读暂不可用，规则分析仍然有效。"}</div>}
      </section>}

      {(evidence || collector) && <section className="panel">
        <div className="panel-title"><div><span>DATA TRUST</span><h3>证据与采集健康度</h3></div><Gauge /></div>
        {evidence && <div className="info-box">
          <strong>Evidence {evidence.grade} · {evidence.label}</strong>
          <div>数据完整度 {evidence.confidence}% · 采集健康度 {evidence.collector_health}% · 相关样本 {evidence.sample_total} 条</div>
          {evidence.reasons?.length > 0 && <small>{evidence.reasons.join("；")}</small>}
        </div>}
        {collector && <div className="table-shell">
          <table>
            <thead><tr><th>平台</th><th>状态</th><th>健康度</th><th>Raw → Parsed</th><th>价格覆盖</th><th>需求字段</th><th>警告</th></tr></thead>
            <tbody>{Object.entries(collector.platforms || {}).map(([name, value]: [string, any]) => <tr key={name}>
              <td><strong>{name}</strong></td>
              <td>{value.status}</td>
              <td>{value.health_score}%</td>
              <td>{value.raw_count} → {value.parsed_count}<small>DOM 行 {value.raw_rows ?? value.raw_count} · 唯一商品解析率 {value.parse_ratio}%</small></td>
              <td>{value.coverage?.price ?? 0}%</td>
              <td>销量 {value.coverage?.sold_count ?? 0}% · 评论 {value.coverage?.review_count ?? 0}%</td>
              <td>{value.warnings?.length ? value.warnings.join("；") : "—"}</td>
            </tr>)}</tbody>
          </table>
        </div>}
      </section>}

      {trendPending && <div className="data-state panel" role="status"><LoaderCircle className="state-spinner" /><div><strong>正在加载近期趋势</strong><span>主机会分已经可用，趋势证据稍后补充。</span></div></div>}
      {!trendPending && visibleTrendError && <section className="panel">
        <div className="panel-title"><div><span>TEMPORAL EVIDENCE</span><h3>近期销量 / 评论动量</h3></div><Activity /></div>
        <div className="method-note"><AlertCircle /><span>趋势加载失败，不影响上面的主机会分：{visibleTrendError}</span><button className="table-action" onClick={retryTrend}><RefreshCw />重试趋势</button></div>
      </section>}
      {!trendPending && !visibleTrendError && visibleTrend && <section className="panel chart-panel">
        <div className="panel-title"><div><span>TEMPORAL EVIDENCE</span><h3>近期销量 / 评论动量</h3></div><Activity /></div>
        {visibleTrend.status === "insufficient_history" ? <div className="method-note"><Activity /> {visibleTrend.message}</div> : <>
          <p className="method-note"><Activity /> {visibleTrend.message} 当前对比间隔 {visibleTrend.interval_hours} 小时；这是辅助证据，暂不直接改写主机会分。</p>
          {visibleTrend.overall && <div className="stat-grid">
            <article className="stat-card"><span>历史匹配</span><strong>{visibleTrend.overall.matched_items}</strong><small>{visibleTrend.overall.match_rate}% · 可靠度 {visibleTrend.overall.reliability}%</small></article>
            <article className="stat-card"><span>近期活跃商品</span><strong>{visibleTrend.overall.activity_share == null ? "—" : `${visibleTrend.overall.activity_share}%`}</strong><small>sold 或 review 有增长</small></article>
            <article className="stat-card"><span>中位 Sold / 日</span><strong>{visibleTrend.overall.median_sold_velocity_per_day ?? "—"}</strong><small>中位增量 {visibleTrend.overall.median_sold_delta ?? "—"}</small></article>
            <article className="stat-card"><span>价格中位波动</span><strong>{visibleTrend.overall.median_abs_price_change_pct == null ? "—" : `${visibleTrend.overall.median_abs_price_change_pct}%`}</strong><small>排名中位变化 {visibleTrend.overall.median_rank_change ?? "—"}</small></article>
          </div>}
          {visibleTrend.recommendations?.length > 0 && <div className="recommendation-list">
            {visibleTrend.recommendations.map((text: string, index: number) => <article key={index}><CheckCircle2 /><span>{text}</span><ArrowRight /></article>)}
          </div>}
          <div className="table-shell">
            <table>
              <thead><tr><th>平台</th><th>匹配率</th><th>活跃占比</th><th>Sold / 日</th><th>Review / 日</th><th>价格波动</th><th>排名变化</th><th>可靠度</th></tr></thead>
              <tbody>{Object.entries(visibleTrend.platforms || {}).map(([name, value]: [string, any]) => <tr key={name}>
                <td><strong>{name}</strong></td>
                <td>{value.matched_items}/{value.current_items}<small>{value.match_rate}%</small></td>
                <td>{value.activity_share == null ? "—" : `${value.activity_share}%`}</td>
                <td>{value.median_sold_velocity_per_day ?? "—"}</td>
                <td>{value.median_review_velocity_per_day ?? "—"}</td>
                <td>{value.median_abs_price_change_pct == null ? "—" : `${value.median_abs_price_change_pct}%`}</td>
                <td>{value.median_rank_change ?? "—"}</td>
                <td>{value.reliability}%</td>
              </tr>)}</tbody>
            </table>
          </div>
        </>}
      </section>}

      <section className="platform-analysis">
        {Object.entries(run.platform_scores || {}).map(([name, score]) => <PlatformCard key={name} name={name} score={score} />)}
      </section>

      {segments.length > 0 && <section className="panel">
        <div className="panel-title"><div><span>PRODUCT FAMILIES</span><h3>商品族机会排序</h3></div><Sparkles /></div>
        <p className="method-note"><AlertCircle /> 按搜索标题里反复出现的属性做轻量拆分；小样本和单平台商品族会向中性分收缩，排序可靠度越低越不应直接下结论。</p>
        <div className="table-shell">
          <table>
            <thead><tr><th>#</th><th>商品族</th><th>机会分</th><th>可靠度</th><th>样本</th><th>平台证据</th><th>中位价</th><th>卖家集中度</th><th>代表商品</th></tr></thead>
            <tbody>{segments.map((segment, index) => <tr key={`${segment.label}-${index}`}>
              <td><b>#{index + 1}</b></td>
              <td><strong>{segment.label}</strong><small>{segment.verdict} · 完整度 {segment.confidence}%</small></td>
              <td><strong>{segment.opportunity_score}</strong>{segment.raw_opportunity_score != null && <small>原始 {segment.raw_opportunity_score}</small>}</td>
              <td>{segment.ranking_reliability == null ? "—" : `${segment.ranking_reliability}%`}</td>
              <td>{segment.sample_size} 条<small>占相关结果 {segment.share}%</small></td>
              <td>{segment.platform_coverage}%</td>
              <td>{segment.median_price == null ? "—" : `RM ${segment.median_price.toFixed(2)}`}</td>
              <td>{segment.seller_concentration == null ? "证据不足" : `${segment.seller_concentration.toFixed(1)} / 100`}</td>
              <td>{segment.representative_titles.slice(0, 2).map((title) => <small key={title}>{title}</small>)}</td>
            </tr>)}</tbody>
          </table>
        </div>
      </section>}

      <section className={`panel recommendation-panel ${aiNextSteps.length ? "ai-action-plan" : ""}`}>
        <div className="panel-title">
          <div><span>{aiNextSteps.length ? "AI-GUIDED VALIDATION ROUTE" : "RULE-GUIDED FALLBACK"}</span><h3>下一步建议</h3></div>
          <div className="ai-plan-stamp"><Sparkles /><span>{aiNextSteps.length ? `${aiInsight?.model} 生成` : "规则建议"}</span></div>
        </div>
        {aiNextSteps.length ? <>
          <p className="ai-plan-intro">这不是通用待办清单，而是 AI 根据本轮平台差异、证据缺口和商品族信号整理的验证顺序。每一步都要求留下可以复盘的证据。</p>
          <div className="ai-action-roadmap">
            {aiNextSteps.map((step, index) => <article className="ai-action-step" key={`${step.stage}-${step.title}-${index}`}>
              <header><b>{String(index + 1).padStart(2, "0")}</b><span>{step.stage}</span></header>
              <h4>{step.title}</h4>
              <p>{step.why}</p>
              <ul>{step.tasks.map((task, taskIndex) => <li key={`${task}-${taskIndex}`}><CheckCircle2 /><span>{task}</span></li>)}</ul>
              <div className="ai-watch"><Eye /><span><strong>复盘时看什么</strong>{step.watch}</span></div>
            </article>)}
          </div>
          {(analysis.recommendations || []).length > 0 && <details className="rule-basis">
            <summary>查看规则引擎给出的评分依据</summary>
            <div className="recommendation-list">
              {(analysis.recommendations || []).map((text: string, i: number) => <article key={i}><CheckCircle2 /><span>{text}</span><ArrowRight /></article>)}
            </div>
          </details>}
        </> : <div className="recommendation-list">
          {(analysis.recommendations || []).map((text: string, i: number) => <article key={i}><CheckCircle2 /><span>{text}</span><ArrowRight /></article>)}
        </div>}
        <div className="method-note"><AlertCircle /> AI 负责把证据转成验证路线，不负责批准下单。机会分不是利润预测；采购、物流、平台费、广告、退货和真实毛利仍需人工核算。</div>
      </section>
    </> : null}
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
  const sellerReliability = score.metrics?.seller_evidence_reliability;

  return <article className={`panel platform-analysis-card ${name}`}>
    <div>
      <span className={`platform-tag ${name}`}>{name}</span>
      <strong>{score.eligible === false ? "—" : (score.score ?? "—")}</strong>
      <small>{score.verdict} · {score.sample_size}/{raw} 条相关样本 · 完整度 {score.confidence}%</small>
      {excludedParts.length > 0 && <small>已排除：{excludedParts.join(" · ")}</small>}
      {sellerConcentration != null
        ? <small>卖家集中度 {sellerConcentration.toFixed(1)}/100 · 可识别卖家 {sellerCount ?? "—"} 个 · 证据可靠度 {sellerReliability ?? 0}%</small>
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
