import { useCallback, useEffect, useMemo, useState } from "react";
import { ExternalLink, Search, SlidersHorizontal } from "lucide-react";
import { apiGet } from "@/lib/api";
import type { Keyword, Listing } from "@/types";

const fmt = (v: number | null | undefined) => v == null ? "—" : v.toLocaleString();

export default function Competitors() {
  const [keywords, setKeywords] = useState<Keyword[]>([]);
  const [keywordId, setKeywordId] = useState<number>(0);
  const [items, setItems] = useState<Listing[]>([]);
  const [platform, setPlatform] = useState("all");
  const [query, setQuery] = useState("");

  const loadKeywords = useCallback(() => apiGet<any>("/keywords").then((r) => {
    const list = r.data || [];
    setKeywords(list);
    setKeywordId((current) => current || list[0]?.id || 0);
  }), []);

  useEffect(() => {
    loadKeywords().catch(() => {});
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") loadKeywords().catch(() => {});
    }, 3000);
    return () => window.clearInterval(timer);
  }, [loadKeywords]);

  const selected = keywords.find((x) => x.id === keywordId);
  const stableRunId = selected?.latest_result_run?.id;
  useEffect(() => {
    if (!stableRunId) { setItems([]); return; }
    let cancelled = false;
    apiGet<any>(`/runs/${stableRunId}/items`)
      .then((r) => { if (!cancelled) setItems(r.data || []); })
      .catch(() => { if (!cancelled) setItems([]); });
    return () => { cancelled = true; };
  }, [stableRunId]);

  const filtered = useMemo(() => items.filter((x) => (platform === "all" || x.platform === platform) && (!query || x.title.toLowerCase().includes(query.toLowerCase()))), [items, platform, query]);
  const activeRun = selected?.latest_run;
  const showingOlderResult = Boolean(activeRun && stableRunId && activeRun.id !== stableRunId);

  return <div className="page-stack">
    <section className="section-heading"><div><span className="eyebrow">PUBLIC LISTING SNAPSHOTS</span><h2>竞品对比</h2><p>展示最近一次完成/部分完成的稳定快照；当前新任务不会用未采完的数据覆盖这里。</p></div></section>
    {showingOlderResult && <div className="info-box">当前任务状态：{activeRun?.status}。下表暂时保留上一份稳定竞品快照；新稳定结果完成后会自动替换。</div>}
    <div className="toolbar panel"><select value={keywordId} onChange={(e) => setKeywordId(Number(e.target.value))}><option value={0}>选择关键词</option>{keywords.map((x) => <option key={x.id} value={x.id}>{x.keyword}</option>)}</select><div className="segmented">{["all","shopee","lazada"].map((p) => <button key={p} className={platform === p ? "active" : ""} onClick={() => setPlatform(p)}>{p === "all" ? "全部" : p}</button>)}</div><label className="table-search"><Search /><input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="搜索商品标题" /></label><SlidersHorizontal /></div>
    <div className="table-shell panel"><table><thead><tr><th>平台 / 位次</th><th>商品</th><th>价格</th><th>公开已售</th><th>评分</th><th>评论</th><th>广告</th><th /></tr></thead><tbody>{filtered.map((item) => <tr key={item.id}><td><span className={`platform-tag ${item.platform}`}>{item.platform}</span><b>#{item.search_rank}</b>{item.search_page != null && item.page_rank != null && <small>P{item.search_page} · 页内 #{item.page_rank}</small>}</td><td><div className="product-cell">{item.image_url ? <img src={item.image_url} alt="" /> : <div className="image-fallback" />}<div><strong>{item.title}</strong><small>{item.seller_location || item.seller_name || "公开店铺信息缺失"}</small></div></div></td><td><strong className="money">{item.price == null ? "—" : `RM ${item.price.toFixed(2)}`}</strong>{item.discount_percent != null && <small>-{item.discount_percent}%</small>}</td><td>{fmt(item.sold_count)}</td><td>{item.rating == null ? "—" : item.rating.toFixed(1)}</td><td>{fmt(item.review_count)}</td><td>{item.is_sponsored == null ? "—" : item.is_sponsored ? "是" : "否"}</td><td><a href={item.product_url} target="_blank" rel="noreferrer"><ExternalLink /></a></td></tr>)}</tbody></table>{filtered.length === 0 && <div className="empty-state">当前没有可展示的稳定商品快照。</div>}</div>
  </div>;
}
