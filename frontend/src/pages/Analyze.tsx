import { FormEvent, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Clock3, Search, ShieldAlert, Store, Zap } from "lucide-react";
import { apiGet, apiPost } from "@/lib/api";

interface MarketplaceDefaults {
  results_limit: number;
  search_pages: number;
  max_results_per_platform: number;
  daily_time: string;
  timezone: string;
  platforms: string[];
}

const FALLBACK_DEFAULTS: MarketplaceDefaults = {
  results_limit: 20,
  search_pages: 3,
  max_results_per_platform: 60,
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
        const received = response.data as Partial<MarketplaceDefaults>;
        const resultsLimit = received.results_limit || FALLBACK_DEFAULTS.results_limit;
        const searchPages = received.search_pages || FALLBACK_DEFAULTS.search_pages;
        const next: MarketplaceDefaults = {
          ...FALLBACK_DEFAULTS,
          ...received,
          results_limit: resultsLimit,
          search_pages: searchPages,
          max_results_per_platform: received.max_results_per_platform || resultsLimit * searchPages,
          platforms: received.platforms?.length ? received.platforms : FALLBACK_DEFAULTS.platforms,
        };
        setDefaults(next);
        setPlatforms(next.platforms);
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

  const sampleLimit = defaults.max_results_per_platform * platforms.length;
  const marketplaceLabel = platforms
    .map((platform) => platform === "shopee" ? "Shopee Malaysia" : platform === "lazada" ? "Lazada Malaysia" : platform)
    .join(" 与 ");
  return <div className="page-stack">
    <section className="hero-split">
      <div><span className="eyebrow">KEYWORD-FIRST WORKFLOW</span><h2>不用商品编号。<br /><em>说出你想卖什么。</em></h2><p>系统使用你已登录的 Chrome，依次访问 {marketplaceLabel || "所选平台"} 前 {defaults.search_pages} 页，再把不可比较的口径分开处理。</p></div>
      <div className="hero-signal"><Zap /><strong>{sampleLimit}</strong><span>本次最多采集<br />{defaults.max_results_per_platform} / 平台 × {platforms.length}</span></div>
    </section>
    <form onSubmit={submit} className="analysis-console panel">
      <div className="console-label"><Search size={18} /><span>商品关键词</span><b>01</b></div>
      <input className="keyword-input" autoFocus value={keyword} onChange={(e) => setKeyword(e.target.value)} placeholder="例如：water bottle、botol air、儿童保温杯" />
      <div className="platform-grid">
        {[{ id: "shopee", name: "Shopee Malaysia", tone: "orange" }, { id: "lazada", name: "Lazada Malaysia", tone: "blue" }].map((p) => <button type="button" key={p.id} onClick={() => toggle(p.id)} className={`platform-card ${p.tone} ${platforms.includes(p.id) ? "selected" : ""}`}><Store /><div><strong>{p.name}</strong><span>{platforms.includes(p.id) ? "已加入本次扫描" : "点击加入"}</span></div><i /></button>)}
      </div>
      <section className="scan-blueprint" aria-label="真实翻页扫描范围">
        <div className="scan-blueprint-head">
          <div><span><i /> LIVE BROWSER ROUTE</span><strong>前 {defaults.search_pages} 页真实扫描</strong></div>
          <p>同一 Chrome 标签按平台串行翻页，跨页按商品去重，并保留首次出现的搜索排名。</p>
        </div>
        <div className="scan-plan-grid">
          <div><span>01 · PAGE RANGE</span><strong>前 {defaults.search_pages} 页</strong><small>逐页访问真实结果</small></div>
          <div><span>02 · PAGE LIMIT</span><strong>{defaults.results_limit} 条 / 页</strong><small>每个平台分别计算</small></div>
          <div><span>03 · PLATFORM MAX</span><strong>{defaults.max_results_per_platform} 条</strong><small>单平台采集上限</small></div>
        </div>
        <div className="scan-route-line">{platforms.map((platform, index) => <span key={platform} className="scan-route-platform"><b>{platform === "shopee" ? "Shopee" : "Lazada"}</b><span>第 1–{defaults.search_pages} 页</span>{index < platforms.length - 1 && <em>然后</em>}</span>)}</div>
      </section>
      <label className="tracking-choice"><input type="checkbox" checked={tracking} onChange={(e) => setTracking(e.target.checked)} /><Clock3 /><div><strong>每日 {defaults.daily_time} 自动跟踪</strong><span>{defaults.timezone} · 保存价格、公开已售、评论与搜索排名变化</span></div></label>
      <div className="notice-row"><ShieldAlert size={17} /><span>平台要求验证时任务会暂停，由你手动完成验证；系统不会自动绕过。</span></div>
      {error && <div className="error-box">{error}</div>}
      <button className="primary-button analyze-button" disabled={busy}>{busy ? "正在创建扫描…" : "开始市场分析"}<Search size={18} /></button>
    </form>
  </div>;
}
