import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Clock3, Monitor, Pause, Play, RefreshCw, Trash2 } from "lucide-react";
import { apiDelete, apiGet, apiPatch, apiPost } from "@/lib/api";
import StatusBadge from "@/components/StatusBadge";
import type { Keyword } from "@/types";

const ACTIVE_RUNS = new Set(["pending", "running", "needs_verification"]);
const DELETE_BLOCKED_RUNS = new Set(["pending", "running"]);
const RESULT_RUNS = new Set(["completed", "partial"]);

export default function Tracking() {
  const [params] = useSearchParams();
  const highlighted = Number(params.get("run_id") || 0);
  const [keywords, setKeywords] = useState<Keyword[]>([]);
  const [message, setMessage] = useState("");
  const load = useCallback(() => apiGet<any>("/keywords").then((r) => setKeywords(r.data || [])), []);

  useEffect(() => {
    load().catch(() => {});
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") load().catch(() => {});
    }, 3000);
    return () => window.clearInterval(timer);
  }, [load]);

  const action = async (fn: () => Promise<any>, ok: string) => {
    try {
      await fn();
      setMessage(ok);
      await load();
    } catch (e: any) {
      setMessage(e.message || "操作失败");
    }
  };

  const queuedAction = async (fn: () => Promise<any>, ok: string) => action(async () => {
    const result = await fn();
    if (result?.queued === false) {
      const status = result?.run?.status;
      if (status === "needs_verification") throw new Error("当前任务正在等待人工验证，请先完成验证后再继续。");
      if (status === "running" || status === "pending") throw new Error("当前任务已经在采集队列中，无需重复提交。");
      throw new Error("任务未能加入采集队列，请刷新状态后重试。");
    }
    return result;
  }, ok);

  return <div className="page-stack">
    <section className="section-heading">
      <div><span className="eyebrow">DAILY COLLECTION QUEUE</span><h2>每日跟踪</h2><p>所有关键词串行运行，避免同时请求两个平台；页面不可见时暂停前端轮询，后台采集不受影响。</p></div>
      <div className="schedule-stamp"><Clock3 /><span>调度方式</span><strong>按关键词计划</strong></div>
    </section>

    {message && <div className="info-box">{message}</div>}
    <div className="tracking-list">
      {keywords.length === 0 && <div className="empty-state">还没有跟踪关键词。先去“关键词分析”创建一个。</div>}
      {keywords.map((item) => {
        const run = item.latest_run;
        const searchPages = item.search_pages || 3;
        const maxPerPlatform = item.results_limit * searchPages;
        const active = Boolean(run && ACTIVE_RUNS.has(run.status));
        const deleteBlocked = Boolean(run && DELETE_BLOCKED_RUNS.has(run.status));
        const resultRun = run && RESULT_RUNS.has(run.status) ? run : item.latest_result_run;
        const showingOlderScore = Boolean(run && resultRun && run.id !== resultRun.id);
        return <article key={item.id} className={`tracking-card ${run?.id === highlighted ? "highlight" : ""}`}>
          <div className="tracking-main">
            <div className="keyword-monogram">{item.keyword.slice(0, 2).toUpperCase()}</div>
            <div className="tracking-keyword-copy"><h3>{item.keyword}</h3><p>{item.platforms.join(" + ")}</p><div className="tracking-scan-meta"><span><b>前 {searchPages} 页</b>真实访问</span><span><b>{item.results_limit} 条</b> / 页</span><span><b>最多 {maxPerPlatform} 条</b> / 平台</span><span>每日 <b>{item.daily_time}</b></span></div></div>
          </div>
          <div className="tracking-status">
            {run && <StatusBadge status={run.status} />}
            <div className="progress-track"><i style={{ width: `${run?.progress ?? 0}%` }} /></div>
            <small>{run?.current_step || "尚未运行"}</small>
          </div>
          <div className="tracking-score">
            <strong>{resultRun?.opportunity_score ?? "—"}</strong>
            <span>{resultRun?.verdict || "等待结论"}{showingOlderScore ? " · 上次结果" : ""}</span>
          </div>
          <div className="tracking-actions">
            {run?.status === "needs_verification" && <>
              <button title="打开验证浏览器" onClick={() => action(() => apiPost(`/runs/${run.id}/verification-browser`), "验证浏览器已打开；验证完成后保持窗口打开。") }><Monitor /><span>验证</span></button>
              <button title="验证完成后继续采集" onClick={() => queuedAction(() => apiPost(`/runs/${run.id}/resume`), "正在从验证浏览器继续采集") }><Play /><span>继续</span></button>
            </>}
            {(run?.status === "failed" || run?.status === "partial") && <button title="创建新的重试任务" onClick={() => queuedAction(() => apiPost(`/runs/${run.id}/resume`), "已创建新的重试任务") }><Play /><span>重试</span></button>}
            <button
              title={active ? "当前任务尚未结束" : "立即更新"}
              disabled={active}
              onClick={() => queuedAction(() => apiPost(`/keywords/${item.id}/runs`), "已加入采集队列")}
            ><RefreshCw /><span>更新</span></button>
            <button title={item.tracking_enabled ? "暂停每日跟踪" : "启用每日跟踪"} onClick={() => action(() => apiPatch(`/keywords/${item.id}`, { tracking_enabled: !item.tracking_enabled }), item.tracking_enabled ? "已暂停" : "已启用")}>{item.tracking_enabled ? <Pause /> : <Play />}<span>{item.tracking_enabled ? "暂停" : "启用"}</span></button>
            <button
              title={deleteBlocked ? "任务正在排队或采集中，暂不能删除" : "删除"}
              disabled={deleteBlocked}
              className="danger"
              onClick={() => action(() => apiDelete(`/keywords/${item.id}`), "已删除")}
            ><Trash2 /><span>删除</span></button>
          </div>
          {run?.error_message && <div className="run-error">{run.error_message}</div>}
        </article>;
      })}
    </div>
  </div>;
}
