import type { Run } from "@/types";
const LABELS: Record<Run["status"], string> = { pending: "等待", running: "采集中", completed: "完成", partial: "部分完成", needs_verification: "需要验证", failed: "失败" };
export default function StatusBadge({ status }: { status: Run["status"] }) { return <span className={`status-badge status-${status}`}>{LABELS[status]}</span>; }
