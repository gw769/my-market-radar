import { useCallback, useEffect, useMemo, useState } from "react";
import { Compass, Play, RefreshCw, ScanSearch, Sparkles } from "lucide-react";
import { apiGet, apiPost } from "@/lib/api";
import type { Keyword, Run } from "@/types";

interface MarketplaceDefaults {
  results_limit: number;
  daily_time: string;
  timezone: string;
  platforms: string[];
}

interface DiscoveryPreset {
  id: string;
  name: string;
  description: string;
  seeds: string[];
}

const PRESETS: DiscoveryPreset[] = [
  { id: "home", name: "家居收纳", description: "收纳、空间利用、日常整理类商品", seeds: ["storage box", "shoe rack", "laundry basket", "drawer organizer"] },
  { id: "kitchen", name: "厨房饮水", description: "厨房、便携饮水、食物收纳和小家电", seeds: ["water bottle", "lunch box", "food container", "portable blender"] },
  { id: "pet", name: "宠物用品", description: "猫狗日常喂养、清洁和活动用品", seeds: ["pet water fountain", "pet feeder", "cat scratcher", "pet grooming brush"] },
  { id: "office", name: "办公桌面", description: "桌面整理、支架、照明和常用数码周边", seeds: ["laptop stand", "desk lamp", "cable organizer", "mouse pad"] },
  { id: "sports", name: "运动出行", description: "健身、旅行、户外和随身收纳方向", seeds: ["yoga mat", "resistance band", "gym bag", "travel organizer"] },
];

const RESULT_STATUSES = new Set(["completed", "partial"]);
const ACTIVE_STATUSES = new Set(["pending", "running", "needs_verification"]);
const GRADE_RANK: Record<string, number> = { A: 4, B: 3, C: 2, D: 1 };

function resultRun(keyword?: Keyword): Run | null {
  if (!keyword) return null;
  const latest = keyword.latest_run;
  if (latest && RESULT_STATUSES.has(latest.status)) return latest;
  return keyword.latest_result_run || null;
}

export default function Discovery() {
  const [selectedId, setSelectedId] = useState(PRESETS[0].id);
  const [keywords, setKeywords] = useState<Keyword[]>([]);
  const [defaults, setDefaults] = useState<MarketplaceDefaults>({
    results_limit: 20,
    daily_time: "20:00",
    timezone: "Asia/Kuala_Lumpur",
    platforms: ["shopee", "lazada"],
  });
  const [busy, setBusy] = useState(false);
  const [busySeed, setBusySeed] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    const response = await apiGet<any>("/keywords");
    setKeywords(response.data || []);
  }, []);

  useEffect(() => {
    apiGet<any>("/marketplace-defaults")
      .then((response) => { if (response.data) setDefaults(response.data); })
      .catch(() => {});
    load().catch(() => {});
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") load().catch(() => {});
    }, 3000);
    return () => window.clearInterval(timer);
  }, [load]);

  const preset = PRESETS.find((item) => item.id === selectedId) || PRESETS[0];
  const quickLimit = Math.max(10, Math.min(defaults.results_limit || 20, 15));

  const rows = useMemo(() => preset.seeds.map((seed) => {
    const keyword = keywords.find((item) => item.keyword.trim().toLowerCase() === seed.toLowerCase());
    const run = resultRun(keyword);
    const latest = keyword?.latest_run || null;
    const evidence = run?.analysis?.evidence || null;
    const topSegment = run?.analysis?.opportunity_segments?.[0] || null;
    const prices = Object.values(run?.platform_scores || {})
      .map((score: any) => score?.metrics?.median_price)
      .filter((value: any) => typeof value === "number") as number[];
    const medianPrice = prices.length ? prices.reduce((sum, value) => sum + value, 0) / prices.length : null;
    const deepLimit = Math.min(40, Math.max(20, defaults.results_limit || 20, keyword?.results_limit || 0));
    const active = Boolean(latest && ACTIVE_STATUSES.has(latest.status));
    return { seed, keyword, latest, run, evidence, topSegment, medianPrice, deepLimit, active };
  }).sort((a, b) => {
    const aGrade = GRADE_RANK[a.evidence?.grade] || 0;
    const bGrade = GRADE_RANK[b.evidence?.grade] || 0;
    if (aGrade !== bGrade) return bGrade - aGrade;
    return (b.run?.opportunity_score ?? -1) - (a.run?.opportunity_score ?? -1);
  }), [defaults.results_limit, keywords, preset]);

  const activeCount = rows.filter((row) => row.active).length;
  const readyCount = rows.filter((row) => row.run).length;
  const missingCount = rows.filter((row) => !row.run && !row.active).length;

  const startDiscovery = async () => {
    setBusy(true);
    setError("");
    setMessage("");
    let submitted = 0;
    let kept = 0;
    const failed: string[] = [];

    for (const seed of preset.seeds) {
      try {
        const existing = keywords.find((item) => item.keyword.trim().toLowerCase() === seed.toLowerCase());
        if (existing) {
          const latest = existing.latest_run;
          if ((latest && ACTIVE_STATUSES.has(latest.status)) || resultRun(existing)) {
            kept += 1;
            continue;
          }
          const response = await apiPost<any>(`/keywords/${existing.id}/runs`);
          if (response?.queued === false && !ACTIVE_STATUSES.has(response?.run?.status)) throw new Error("任务未进入队列");
          submitted += 1;
          continue;
        }

        const response = await apiPost<any>("/keywords", {
          keyword: seed,
          platforms: defaults.platforms?.length ? defaults.platforms : ["shopee", "lazada"],
          results_limit: quickLimit,
          tracking_enabled: false,
          daily_time: defaults.daily_time,
          timezone: defaults.timezone,
        });
        if (response?.queued === false && !ACTIVE_STATUSES.has(response?.run?.status)) throw new Error("任务未进入队列");
        submitted += 1;
      } catch {
        failed.push(seed);
      }
    }

    const parts = [`已提交 ${submitted} 个缺失候选`];
    if (kept) parts.push(`${kept} 个已有结果/任务保持不变`);
    if (failed.length) parts.push(`${failed.length} 个提交失败`);
    setMessage(`${parts.join("，")}。已有深扫结果不会被快速扫描覆盖。`);
    setError(failed.length ? `未提交成功：${failed.join("、")}。其他候选已继续处理，可稍后再次补提交。` : "");
    try { await load(); } catch { /* polling will retry */ }
    setBusy(false);
  };

  const deepScan = async (keyword: Keyword, seed: string, resultsLimit: number) => {
    setBusySeed(seed);
    setError("");
    setMessage("");
    try {
      const response = await apiPost<any>(`/discovery/keywords/${keyword.id}/deep-scan`, {
        results_limit: resultsLimit,
      });
      if (response?.reason === "active_run") {
        setMessage(`${seed} 已有任务在运行，本次没有重复入队。`);
      } else if (response?.queued) {
        setMessage(`${seed} 已开始深度扫描：${resultsLimit} 条/平台。本次覆盖不会修改关键词长期设置。`);
      } else {
        setError(`${seed} 深度扫描没有进入队列，请稍后重试。`);
      }
      await load();
    } catch (err: any) {
      setError(err.message || `${seed} 深度扫描失败`);
    } finally {
      setBusySeed("");
    }
  };

  return <div className="page-stack">
    <section className="hero-split">
      <div>
        <span className="eyebrow">MARKET DISCOVERY MVP</span>
        <h2>不先猜商品。<br /><em>先扫一组市场候选。</em></h2>
        <p>选择一个方向，系统会把预设候选送入现有 Shopee × Lazada 采集/评分链，用 Evidence、校准机会分和商品族可靠度排序。</p>
      </div>
      <div className="hero-signal"><Compass /><strong>{preset.seeds.length}</strong><span>候选关键词<br />快速样本 {quickLimit}/平台</span></div>
    </section>

    <section className="panel">
      <div className="panel-title"><div><span>DISCOVERY PRESETS</span><h3>选择市场方向</h3></div><Sparkles /></div>
      <div className="platform-grid">
        {PRESETS.map((item) => <button type="button" key={item.id} onClick={() => setSelectedId(item.id)} className={`platform-card ${selectedId === item.id ? "selected" : ""}`}>
          <Compass /><div><strong>{item.name}</strong><span>{item.description}</span></div><i />
        </button>)}
      </div>
      <div className="notice-row"><RefreshCw size={17} /><span>当前：{readyCount}/{preset.seeds.length} 个候选已有稳定结果，{activeCount} 个任务进行中，{missingCount} 个尚无结果。</span></div>
      {message && <div className="info-box">{message}</div>}
      {error && <div className="error-box">{error}</div>}
      <button className="primary-button analyze-button" disabled={busy || (missingCount === 0 && activeCount === 0)} onClick={startDiscovery}>
        {busy ? "正在提交候选…" : missingCount > 0 ? `补充 ${missingCount} 个未完成候选` : activeCount > 0 ? "等待当前候选完成" : "这一组已有结果"}<Play size={18} />
      </button>
    </section>

    <section className="panel">
      <div className="panel-title"><div><span>RANKED CANDIDATES</span><h3>{preset.name} · 候选机会榜</h3></div><Sparkles /></div>
      <p className="method-note">第一轮用快速样本补齐候选，已有稳定/深扫结果不会被整组按钮降级覆盖。高价值候选用单行“深扫”提高本次样本上限，不修改长期跟踪配置。</p>
      <div className="table-shell">
        <table>
          <thead><tr><th>#</th><th>候选</th><th>状态</th><th>Evidence</th><th>机会分</th><th>完整度</th><th>中位价</th><th>最高商品族</th><th>商品族可靠度</th><th>操作</th></tr></thead>
          <tbody>{rows.map((row, index) => <tr key={row.seed}>
            <td><b>#{index + 1}</b></td>
            <td><strong>{row.seed}</strong></td>
            <td>{row.latest?.status || "未扫描"}</td>
            <td>{row.evidence ? `${row.evidence.grade} · ${row.evidence.label}` : "—"}</td>
            <td><strong>{row.run?.opportunity_score ?? "—"}</strong><small>{row.run?.verdict || "等待结论"}</small></td>
            <td>{row.run?.confidence == null ? "—" : `${row.run.confidence}%`}</td>
            <td>{row.medianPrice == null ? "—" : `RM ${row.medianPrice.toFixed(2)}`}</td>
            <td>{row.topSegment?.label || "—"}</td>
            <td>{row.topSegment?.ranking_reliability == null ? "—" : `${row.topSegment.ranking_reliability}%`}</td>
            <td>{row.keyword && row.run ? <button
              type="button"
              className="table-action"
              disabled={row.active || busySeed === row.seed}
              onClick={() => deepScan(row.keyword!, row.seed, row.deepLimit)}
            ><ScanSearch size={14} />{busySeed === row.seed ? "提交中" : row.active ? "任务中" : `深扫 ${row.deepLimit}`}</button> : "—"}</td>
          </tr>)}</tbody>
        </table>
      </div>
    </section>
  </div>;
}
