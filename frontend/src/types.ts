export interface Run {
  id: number; keyword_id: number; keyword: string; trigger: string;
  status: "pending" | "running" | "completed" | "partial" | "needs_verification" | "failed";
  progress: number; current_step?: string; verification_platform?: string;
  opportunity_score?: number | null; verdict?: string; confidence?: number;
  platform_scores: Record<string, PlatformScore>; analysis: any; error_message?: string;
  created_at?: string; completed_at?: string;
}
export interface PlatformScore {
  score: number | null; verdict: string; eligible?: boolean; confidence: number; sample_size: number;
  raw_sample_size?: number; excluded_irrelevant?: number; eligibility_reasons?: string[];
  exclusion_breakdown?: { accessory?: number; bundle?: number; low_relevance?: number };
  coverage?: Record<string, number>;
  dimensions: { demand: number | null; entry_ease: number | null; price_room: number | null };
  metrics: Record<string, number | null>;
}
export interface Keyword {
  id: number; keyword: string; platforms: string[]; results_limit: number;
  tracking_enabled: boolean; daily_time: string; timezone: string;
  last_run_at?: string; last_success_at?: string; next_run_at?: string; latest_run?: Run | null;
}
export interface Listing {
  id: number; platform: string; item_id: string; title: string; product_url: string; image_url?: string;
  price?: number | null; original_price?: number | null; discount_percent?: number | null;
  sold_count?: number | null; rating?: number | null; review_count?: number | null;
  seller_name?: string; seller_location?: string; is_sponsored?: boolean | null;
  search_rank: number; data_quality: number; collected_at?: string;
}
