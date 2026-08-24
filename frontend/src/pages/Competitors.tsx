import { useEffect, useRef, useState } from "react";
import { AlertCircle, ChevronLeft, ChevronRight, ExternalLink, LoaderCircle, Puzzle, RefreshCw, Search, SlidersHorizontal } from "lucide-react";
import { useKeywordSummaries } from "@/hooks/useKeywordSummaries";
import { apiGet } from "@/lib/api";
import type { Listing } from "@/types";

const PAGE_SIZE = 30;
const fmt = (value: number | null | undefined) => value == null ? "—" : value.toLocaleString();
const money = (value: number | null | undefined) => value == null ? "—" : `RM ${value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const percent = (value: number | null | undefined) => value == null ? "—" : `${value > 0 ? "+" : ""}${value.toFixed(1)}%`;

function renderableImage(url?: string): boolean {
  if (!url) return false;
  const normalized = url.trim().toLowerCase();
  return normalized.startsWith("https://") || normalized.startsWith("http://");
}

function parseItemsResponse(response: any): { items: Listing[]; total: number } {
  const payload = response?.data;
  const items = Array.isArray(payload)
    ? payload
    : Array.isArray(payload?.items)
      ? payload.items
      : [];
  const pagination = response?.pagination || payload?.pagination || {};
  const total = Number(
    pagination.total
      ?? response?.total
      ?? payload?.total
      ?? items.length,
  );
  return { items, total: Number.isFinite(total) ? Math.max(0, total) : items.length };
}

export default function Competitors() {
  const { keywords, loading: keywordsLoading, error: keywordsError, refresh } = useKeywordSummaries();
  const [keywordId, setKeywordId] = useState(0);
  const [items, setItems] = useState<Listing[]>([]);
  const [total, setTotal] = useState(0);
  const [platform, setPlatform] = useState("all");
  const [query, setQuery] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [page, setPage] = useState(0);
  const [itemsLoading, setItemsLoading] = useState(false);
  const [itemsError, setItemsError] = useState("");
  const [loadedRequestKey, setLoadedRequestKey] = useState("");
  const [reloadKey, setReloadKey] = useState(0);
  const previousStableRunIdRef = useRef<number | undefined>(undefined);

  useEffect(() => {
    setKeywordId((current) => keywords.some((keyword) => keyword.id === current) ? current : (keywords[0]?.id || 0));
  }, [keywords]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setPage(0);
      setSearchQuery(query.trim());
    }, 320);
    return () => window.clearTimeout(timer);
  }, [query]);

  const selected = keywords.find((keyword) => keyword.id === keywordId);
  const stableRunId = selected?.latest_result_run?.id;
  const activeRun = selected?.latest_run;
  const showingOlderResult = Boolean(activeRun && stableRunId && activeRun.id !== stableRunId);
  const requestKey = stableRunId ? [stableRunId, page, platform, searchQuery].join(":") : "";

  useEffect(() => {
    if (stableRunId !== previousStableRunIdRef.current) {
      previousStableRunIdRef.current = stableRunId;
      if (page !== 0) {
        setPage(0);
        return;
      }
    }
    if (!stableRunId) {
      setItems([]);
      setTotal(0);
      setLoadedRequestKey("");
      setItemsLoading(false);
      setItemsError("");
      return;
    }

    const controller = new AbortController();
    const params = new URLSearchParams({
      limit: String(PAGE_SIZE),
      offset: String(page * PAGE_SIZE),
    });
    if (platform !== "all") params.set("platform", platform);
    if (searchQuery) params.set("q", searchQuery);

    setItemsLoading(true);
    setItemsError("");
    apiGet<any>(`/runs/${stableRunId}/items?${params.toString()}`, { signal: controller.signal })
      .then((response) => {
        const parsed = parseItemsResponse(response);
        setItems(parsed.items);
        setTotal(parsed.total);
        setLoadedRequestKey(requestKey);
      })
      .catch((reason: unknown) => {
        if (reason instanceof Error && reason.name === "AbortError") return;
        setItems([]);
        setTotal(0);
        setLoadedRequestKey(requestKey);
        setItemsError(reason instanceof Error ? reason.message : "竞品数据加载失败");
      })
      .finally(() => {
        if (!controller.signal.aborted) setItemsLoading(false);
      });
    return () => controller.abort();
  }, [stableRunId, page, platform, searchQuery, requestKey, reloadKey]);

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const pageStart = total === 0 ? 0 : page * PAGE_SIZE + 1;
  const pageEnd = Math.min(total, (page + 1) * PAGE_SIZE);
  const pageError = keywordsError || itemsError;
  const loading = keywordsLoading || itemsLoading || Boolean(stableRunId && loadedRequestKey !== requestKey);
  const enrichedCount = items.filter((item) => item.shopdora).length;
  const retry = () => {
    if (keywordsError) refresh().catch(() => {});
    setLoadedRequestKey("");
    setItemsLoading(true);
    setItemsError("");
    setReloadKey((value) => value + 1);
  };

  return <div className="page-stack">
    <section className="section-heading"><div><span className="eyebrow">PUBLIC LISTING SNAPSHOTS</span><h2>竞品对比</h2><p>展示最近一次完成/部分完成的稳定快照；当前新任务不会用未采完的数据覆盖这里。</p></div></section>
    {showingOlderResult && <div className="info-box">当前任务状态：{activeRun?.status}。下表暂时保留上一份稳定竞品快照；新稳定结果完成后会自动替换。</div>}
    <div className="toolbar panel">
      <select value={keywordId} onChange={(event) => { setKeywordId(Number(event.target.value)); setPage(0); }} aria-label="选择关键词">
        <option value={0}>选择关键词</option>
        {keywords.map((keyword) => <option key={keyword.id} value={keyword.id}>{keyword.keyword}</option>)}
      </select>
      <div className="segmented">{["all", "shopee", "lazada"].map((value) => <button key={value} className={platform === value ? "active" : ""} onClick={() => { setPlatform(value); setPage(0); }}>{value === "all" ? "全部" : value}</button>)}</div>
      <label className="table-search"><Search /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索商品标题" /></label>
      <SlidersHorizontal />
    </div>

    {loading && <div className="data-state panel" role="status"><LoaderCircle className="state-spinner" /><div><strong>正在加载竞品快照</strong><span>每次只读取 30 条，图片进入视口后再加载。</span></div></div>}
    {!loading && pageError && <div className="data-state error-state panel" role="alert"><AlertCircle /><div><strong>竞品数据加载失败</strong><span>{pageError}</span></div><button onClick={retry}><RefreshCw />重新加载</button></div>}
    {!loading && !pageError && (!selected || !stableRunId) && <div className="empty-state panel">{selected ? "这个关键词还没有可展示的稳定商品快照。" : "还没有可选择的关键词。"}</div>}

    {!loading && !pageError && selected && stableRunId ? <>
      <div className="result-summary panel"><div><span>筛选结果</span><strong>{total.toLocaleString()} 条</strong></div><small>{total ? `当前显示 ${pageStart}–${pageEnd}` : "当前筛选没有匹配商品"}</small></div>
      {enrichedCount > 0 && <div className="extension-notice panel"><Puzzle /><div><strong>当前页有 {enrichedCount} 条 Shopdora 增强数据</strong><span>月销量、销售额、GMV 与增长率均为第三方插件估算；独立展示，不改变公开数据机会分。</span></div><em>SHOPEE ONLY</em></div>}
      <div className="table-shell competitor-table panel"><table><thead><tr><th>平台 / 位次</th><th>商品</th><th>价格</th><th>公开已售</th><th>插件增强（估算）</th><th>评分</th><th>评论数</th><th>广告</th><th /></tr></thead><tbody>{items.map((item) => {
        const shopdora = item.shopdora;
        const seller = item.seller_name || shopdora?.seller_name;
        return <tr key={item.id}><td><span className={`platform-tag ${item.platform}`}>{item.platform}</span><b>#{item.search_rank}</b>{item.search_page != null && item.page_rank != null && <small>P{item.search_page} · 页内 #{item.page_rank}</small>}</td><td><div className="product-cell">{renderableImage(item.image_url) ? <img src={item.image_url} alt="" width={46} height={46} loading="lazy" decoding="async" /> : <div className="image-fallback" />}<div><strong>{item.title}</strong><small>{[seller, item.seller_location].filter(Boolean).join(" · ") || "公开店铺信息缺失"}{!item.seller_name && shopdora?.seller_name ? " · 插件增强" : ""}</small>{shopdora?.category_path && <small className="category-path">{shopdora.category_path}</small>}</div></div></td><td><strong className="money">{money(item.price)}</strong>{item.discount_percent != null && <small>-{item.discount_percent}%</small>}</td><td><strong>{fmt(item.sold_count)}</strong><small>平台公开累计口径</small></td><td>{shopdora ? <div className="extension-metrics"><span><Puzzle /> SHOPDORA · 估算</span><strong>近30日 {fmt(shopdora.sales_30d)}</strong><small className={(shopdora.sales_30d_growth_percent || 0) >= 0 ? "metric-up" : "metric-down"}>增长 {percent(shopdora.sales_30d_growth_percent)}</small><small>销售额 {money(shopdora.revenue_30d_myr)}</small><details><summary>更多增强字段</summary><dl><div><dt>总销量估算</dt><dd>{fmt(shopdora.total_sales_estimate)}</dd></div><div><dt>GMV 估算</dt><dd>{money(shopdora.gmv_estimate_myr)}</dd></div><div><dt>上架</dt><dd>{shopdora.listed_at || "—"}</dd></div><div><dt>点赞</dt><dd>{fmt(shopdora.like_count)}</dd></div><div><dt>品牌</dt><dd>{shopdora.brand || "—"}</dd></div><div><dt>类目月销排名</dt><dd>{fmt(shopdora.category_monthly_sales_rank)}</dd></div></dl></details></div> : <span className="extension-missing">{item.platform === "shopee" ? "本次未采到插件字段" : "Lazada 暂未接入"}</span>}</td><td>{item.rating == null ? <span className="field-unavailable">{item.platform === "lazada" ? "搜索页未公开" : "—"}</span> : item.rating.toFixed(1)}</td><td>{item.review_count == null ? <span className="field-unavailable">{item.platform === "shopee" ? "搜索页未公开" : "—"}</span> : fmt(item.review_count)}</td><td>{item.is_sponsored == null ? "—" : item.is_sponsored ? "是" : "否"}</td><td><a href={item.product_url} target="_blank" rel="noreferrer" aria-label={`打开 ${item.title}`}><ExternalLink /></a></td></tr>;
      })}</tbody></table>{items.length === 0 && <div className="empty-state">当前筛选没有匹配商品。</div>}</div>
      {total > 0 && <nav className="table-pagination panel" aria-label="竞品分页"><button disabled={page === 0} onClick={() => setPage((value) => Math.max(0, value - 1))}><ChevronLeft />上一页</button><span>第 <strong>{page + 1}</strong> / {totalPages} 页</span><button disabled={page + 1 >= totalPages} onClick={() => setPage((value) => value + 1)}>下一页<ChevronRight /></button></nav>}
    </> : null}
  </div>;
}
