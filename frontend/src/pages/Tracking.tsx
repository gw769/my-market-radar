import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Monitor, Clock3, Pause, Play, RefreshCw, Trash2 } from "lucide-react";
import { apiDelete, apiGet, apiPatch, apiPost } from "@/lib/api";
import StatusBadge from "@/components/StatusBadge";
import type { Keyword } from "@/types";

export default function Tracking() {
  const [params] = useSearchParams();
  const highlighted = Number(params.get("run_id") || 0);
  const [keywords, setKeywords] = useState<Keyword[]>([]);
  const [message, setMessage] = useState("");
  const load = useCallback(() => apiGet<any>("/keywords").then((r) => setKeywords(r.data || [])), []);
  useEffect(() => { load(); const timer = setInterval(load, 3000); return () => clearInterval(timer); }, [load]);
  const action = async (fn: () => Promise<any>, ok: string) => { try { await fn(); setMessage(ok); await load(); } catch (e: any) { setMessage(e.message); } };
  return <div className="page-stack">
    <section className="section-heading"><div><span className="eyebrow">DAILY COLLECTION QUEUE</span><h2>每日跟踪</h2><p>所有关键词串行运行，避免同时请求两个平台。</p></div><div className="schedule-stamp"><Clock3 /><span>下一批次</span><strong>20:00</strong></div></section>
    {message && <div className="info-box">{message}</div>}
    <div className="tracking-list">
      {keywords.length === 0 && <div className="empty-state">还没有跟踪关键词。先去“关键词分析”创建一个。</div>}
      {keywords.map((item) => { const run = item.latest_run; return <article key={item.id} className={`tracking-card ${run?.id === highlighted ? "highlight" : ""}`}>
        <div className="tracking-main"><div className="keyword-monogram">{item.keyword.slice(0, 2).toUpperCase()}</div><div><h3>{item.keyword}</h3><p>{item.platforms.join(" + ")} · 每个平台 {item.results_limit} 条</p></div></div>
        <div className="tracking-status">{run && <StatusBadge status={run.status} />}<div className="progress-track"><i style={{ width: `${run?.progress || 0}%` }} /></div><small>{run?.current_step || "尚未运行"}</small></div>
        <div className="tracking-score"><strong>{run?.opportunity_score ?? "—"}</strong><span>{run?.verdict || "等待结论"}</span></div>
        <div className="tracking-actions">
          {run?.status === "needs_verification" && <><button title="打开验证浏览器" onClick={() => action(() => apiPost(`/runs/${run.id}/verification-browser`), "验证浏览器已打开；验证完成后保持窗口打开。") }><Monitor /><span>验证</span></button><button title="继续采集" onClick={() => action(() => apiPost(`/runs/${run.id}/resume`), "正在从验证浏览器继续采集") }><Play /><span>继续</span></button></>}
          <button title="立即更新" onClick={() => action(() => apiPost(`/keywords/${item.id}/runs`), "已加入采集队列") }><RefreshCw /><span>更新</span></button>
          <button title={item.tracking_enabled ? "暂停每日跟踪" : "启用每日跟踪"} onClick={() => action(() => apiPatch(`/keywords/${item.id}`, { tracking_enabled: !item.tracking_enabled }), item.tracking_enabled ? "已暂停" : "已启用")}>{item.tracking_enabled ? <Pause /> : <Play />}<span>{item.tracking_enabled ? "暂停" : "启用"}</span></button>
          <button title="删除" className="danger" onClick={() => action(() => apiDelete(`/keywords/${item.id}`), "已删除") }><Trash2 /><span>删除</span></button>
        </div>
        {run?.error_message && <div className="run-error">{run.error_message}</div>}
      </article>; })}
    </div>
  </div>;
}
