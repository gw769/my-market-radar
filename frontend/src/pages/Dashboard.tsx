import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Activity, AlertTriangle, ArrowUpRight, Clock3, Layers3, Search } from "lucide-react";
import { Area, AreaChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { apiGet } from "@/lib/api";
import StatusBadge from "@/components/StatusBadge";
import type { Run } from "@/types";

interface DashboardData { keyword_count: number; tracking_count: number; needs_verification: number; completed_runs: number; platform_counts: Record<string, number>; latest_runs: Run[]; score_history: any[]; }

export default function Dashboard() {
  const [data, setData] = useState<DashboardData | null>(null);
  const navigate = useNavigate();
  const load = useCallback(() => apiGet<any>("/dashboard").then((r) => setData(r.data)), []);

  useEffect(() => {
    load().catch(() => {});
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") load().catch(() => {});
    }, 3000);
    return () => window.clearInterval(timer);
  }, [load]);

  const stats = [
    { label: "跟踪关键词", value: data?.tracking_count ?? 0, note: `共 ${data?.keyword_count ?? 0} 个`, Icon: Search },
    { label: "完成任务", value: data?.completed_runs ?? 0, note: "累计 completed / partial", Icon: Layers3 },
    { label: "需要验证", value: data?.needs_verification ?? 0, note: "当前所有待处理任务", Icon: AlertTriangle },
    { label: "公开商品", value: Object.values(data?.platform_counts || {}).reduce((a, b) => a + b, 0), note: "各关键词最新稳定快照", Icon: Activity },
  ];

  return <div className="page-stack">
    <section className="dashboard-hero"><div><span className="eyebrow">MALAYSIA MARKET PULSE</span><h2>马来西亚市场，<br /><em>最近有什么变化？</em></h2><p>机会分只来自可见的价格、公开已售、评论与搜索结果，不把未知经营数据当成零。</p><button className="primary-button" onClick={() => navigate("/analyze")}>扫描新关键词<ArrowUpRight /></button></div><div className="market-map"><span className="map-dot d1" /><span className="map-dot d2" /><span className="map-ring" /><strong>MY</strong><small>SHOPEE · LAZADA</small></div></section>
    <section className="stat-grid">{stats.map(({ label, value, note, Icon }) => <article className="stat-card" key={label}><Icon /><span>{label}</span><strong>{value}</strong><small>{note}</small></article>)}</section>
    <section className="dashboard-grid">
      <article className="panel chart-panel"><div className="panel-title"><div><span>OPPORTUNITY PULSE</span><h3>最近机会分变化</h3></div><Clock3 /></div><div className="chart-wrap"><ResponsiveContainer width="100%" height="100%"><AreaChart data={data?.score_history || []}><defs><linearGradient id="scoreFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#087d72" stopOpacity={0.28}/><stop offset="100%" stopColor="#087d72" stopOpacity={0}/></linearGradient></defs><XAxis dataKey="keyword" axisLine={false} tickLine={false} tick={{ fill: "#6d7c7d", fontSize: 10 }}/><YAxis domain={[0,100]} axisLine={false} tickLine={false} tick={{ fill: "#6d7c7d", fontSize: 10 }}/><Tooltip contentStyle={{ background: "#fffdf8", color: "#163242", border: "1px solid #ded8ca", borderRadius: 10, boxShadow: "0 16px 38px rgba(14,39,52,.12)" }}/><Area type="monotone" dataKey="score" stroke="#087d72" strokeWidth={2.5} fill="url(#scoreFill)" /></AreaChart></ResponsiveContainer></div></article>
      <article className="panel recent-panel"><div className="panel-title"><div><span>LATEST RUNS</span><h3>最近任务</h3></div></div>{(data?.latest_runs || []).length === 0 ? <div className="empty-state compact">暂无分析任务</div> : <div className="recent-list">{data?.latest_runs.map((run) => <button key={run.id} onClick={() => navigate(`/tracking?run_id=${run.id}`)}><div><strong>{run.keyword}</strong><small>{run.current_step || "等待"}</small></div><StatusBadge status={run.status} /></button>)}</div>}</article>
    </section>
  </div>;
}
