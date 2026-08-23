import { FormEvent, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Clock3, Search, ShieldAlert, Store, Zap } from "lucide-react";
import { apiGet, apiPost } from "@/lib/api";

interface MarketplaceDefaults {
  results_limit: number;
  daily_time: string;
  timezone: string;
  platforms: string[];
}

const FALLBACK_DEFAULTS: MarketplaceDefaults = {
  results_limit: 20,
  daily_time: "20:00",
  timezone: "Asia/Kuala_Lumpur",
  platforms: ["shopee", "lazada"],
};

export default function Analyze() {
  const [keyword, setKeyword] = useState("");
  const [defaults, setDefaults] = useState<MarketplaceDefaults>(FALLBACK_DEFAULTS);
  const [platforms, setPlatforms] = useState<string[]>(FALLBACK_DEFAULTS.platforms);
  const [tracking, setTracking] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const navigate = useNavigate();

  useEffect(() => {
    apiGet<any>("/marketplace-defaults")
      .then((response) => {
        const next = response.data as MarketplaceDefaults;
        setDefaults(next);
        setPlatforms(next.platforms?.length ? next.platforms : FALLBACK_DEFAULTS.platforms);
      })
      .catch(() => {
        // The form remains usable with conservative built-in defaults. Authentication/network
        // errors are already handled centrally by the API helper.
      });
  }, []);

  const toggle = (platform: string) => setPlatforms((items) => items.includes(platform) ? items.filter((x) => x !== platform) : [...items, platform]);
  const submit = async (event: FormEvent) => {
    event.preventDefault(); setError("");
    if (keyword.trim().length < 2 || platforms.length === 0) { setError("请输入至少 2 个字符，并选择一个平台。"); return; }
    setBusy(true);
    try {
      const res = await apiPost<any>("/keywords", {
        keyword,
        platforms,
        results_limit: defaults.results_limit,
        tracking_enabled: tracking,
        daily_time: defaults.daily_time,
        timezone: defaults.timezone,
      });
      navigate(`/tracking?run_id=${res.run.id}`);
    } catch (err: any) { setError(err.message || "创建分析失败"); } finally { setBusy(false); }
  };

  const sampleLimit = defaults.results_limit * platforms.length;
  return <div className="page-stack">
    <section className="hero-split">
      <div><span className="eyebrow">KEYWORD-FIRST WORKFLOW</span><h2>不用商品编号。<br /><em>说出你想卖什么。</em></h2><p>系统会在马来西亚主流平台寻找公开竞品，并把不可比较的口径分开处理。</p></div>
      <div className="hero-signal"><Zap /><strong>{sampleLimit}</strong><span>当前样本上限<br />{defaults.results_limit} × {platforms.length} 平台</span></div>
    </section>
    <form onSubmit={submit} className="analysis-console panel">
      <div className="console-label"><Search size={18} /><span>商品关键词</span><b>01</b></div>
      <input className="keyword-input" autoFocus value={keyword} onChange={(e) => setKeyword(e.target.value)} placeholder="例如：water bottle、botol air、儿童保温杯" />
      <div className="platform-grid">
        {[{ id: "shopee", name: "Shopee Malaysia", tone: "orange" }, { id: "lazada", name: "Lazada Malaysia", tone: "blue" }].map((p) => <button type="button" key={p.id} onClick={() => toggle(p.id)} className={`platform-card ${p.tone} ${platforms.includes(p.id) ? "selected" : ""}`}><Store /><div><strong>{p.name}</strong><span>{platforms.includes(p.id) ? "已加入本次扫描" : "点击加入"}</span></div><i /></button>)}
      </div>
      <label className="tracking-choice"><input type="checkbox" checked={tracking} onChange={(e) => setTracking(e.target.checked)} /><Clock3 /><div><strong>每日 {defaults.daily_time} 自动跟踪</strong><span>{defaults.timezone} · 保存价格、公开已售、评论与搜索排名变化</span></div></label>
      <div className="notice-row"><ShieldAlert size={17} /><span>平台要求验证时任务会暂停，由你手动完成验证；系统不会自动绕过。</span></div>
      {error && <div className="error-box">{error}</div>}
      <button className="primary-button analyze-button" disabled={busy}>{busy ? "正在创建扫描…" : "开始市场分析"}<Search size={18} /></button>
    </form>
  </div>;
}
