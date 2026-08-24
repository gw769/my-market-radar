export interface RunSummary {
  id: number; keyword_id: number; keyword?: string | null; trigger?: string;
  status: "pending" | "running" | "completed" | "partial" | "needs_verification" | "failed";
  progress: number; current_step?: string | null; verification_platform?: string | null;
  opportunity_score?: number | null; verdict?: string | null; confidence?: number | null;
  platform_scores?: Record<string, PlatformScore>; analysis?: any; error_message?: string | null;
  created_at?: string | null; started_at?: string | null; completed_at?: string | null;
}
export interface Run extends RunSummary {
  keyword: string;
  trigger: string;
  platform_scores: Record<string, PlatformScore>;
  analysis: any;
}
export interface PlatformScore {
  score: number | null; verdict: string; eligible?: boolean; confidence: number; sample_size: number;
  raw_sample_size?: number; excluded_irrelevant?: number; eligibility_reasons?: string[];
  exclusion_breakdown?: { accessory?: number; bundle?: number; low_relevance?: number };
  coverage?: Record<string, number>;
  dimensions: { demand: number | null; entry_ease: number | null; price_room: number | null };
  metrics: Record<string, number | null>;
}
export interface OpportunitySegment {
  label: string; token?: string | null; opportunity_score: number; raw_opportunity_score?: number;
  verdict: string; confidence: number; ranking_reliability?: number;
  sample_size: number; share: number; platform_coverage: number;
  median_price?: number | null; seller_concentration?: number | null;
  representative_titles: string[]; platform_scores: Record<string, PlatformScore>;
}
export interface KeywordLocalization {
  keyword: string; search_term: string; aliases: string[];
  source: "deterministic" | "ai" | string; model?: string | null;
}
export interface AINextStep {
  stage: "先核验" | "小规模测试" | "上线准备" | "持续复盘" | string;
  title: string; why: string; tasks: string[]; watch: string;
}
export interface AIInsight {
  status: "completed" | "unavailable" | "disabled" | string;
  model?: string | null; generated_at?: string; summary?: string;
  findings?: string[]; risks?: string[]; actions?: string[];
  next_steps?: AINextStep[];
  message?: string; score_changed?: boolean; evidence_scope?: string;
}
export interface Keyword {
  id: number; keyword: string; marketplace_query?: string; platforms: string[]; results_limit: number; search_pages?: number;
  localization?: KeywordLocalization | null;
  tracking_enabled: boolean; daily_time: string; timezone: string;
  last_run_at?: string; last_success_at?: string; next_run_at?: string;
  latest_run?: RunSummary | null; latest_result_run?: RunSummary | null;
}
export interface ShopdoraEnrichment {
  provider: "Shopdora" | string; source: string; estimated: true; item_id: string;
  seller_name?: string | null; seller_type?: string | null; brand?: string | null;
  category_path?: string | null; category_monthly_sales_rank?: number | null;
  listed_at?: string | null; listing_age_days?: number | null; like_count?: number | null;
  sales_1d?: number | null; sales_7d?: number | null; sales_30d?: number | null;
  sales_30d_growth_percent?: number | null; revenue_30d_myr?: number | null;
  total_sales_estimate?: number | null; gmv_estimate_myr?: number | null;
}
export interface Listing {
  id: number; platform: string; item_id: string; title: string; product_url: string; image_url?: string;
  price?: number | null; original_price?: number | null; discount_percent?: number | null;
  sold_count?: number | null; rating?: number | null; review_count?: number | null;
  seller_name?: string; seller_location?: string; is_sponsored?: boolean | null;
  search_rank: number; search_page?: number | null; page_rank?: number | null; page_size?: number | null;
  shopdora?: ShopdoraEnrichment | null;
  data_quality: number; collected_at?: string;
}
