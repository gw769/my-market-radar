import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { BarChart3, BrainCircuit, Compass, FileSpreadsheet, LayoutDashboard, LogOut, Radar, RefreshCw, Search, Store, User } from "lucide-react";
import { useAuth } from "@/contexts/AuthContext";
import "./layout-overrides.css";

const NAV = [
  { to: "/dashboard", label: "总览", mobileLabel: "总览", Icon: LayoutDashboard },
  { to: "/discovery", label: "机会发现", mobileLabel: "发现", Icon: Compass },
  { to: "/analyze", label: "关键词分析", mobileLabel: "分析", Icon: Search },
  { to: "/tracking", label: "每日跟踪", mobileLabel: "跟踪", Icon: RefreshCw },
  { to: "/competitors", label: "竞品对比", mobileLabel: "竞品", Icon: Store },
  { to: "/analysis", label: "分析建议", mobileLabel: "建议", Icon: BrainCircuit },
  { to: "/reports", label: "Excel 报告", mobileLabel: "报告", Icon: FileSpreadsheet },
];

export default function Layout() {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const { user, logout } = useAuth();
  const current = NAV.find((item) => item.to === pathname)?.label || "市场情报";
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand-block"><div className="brand-mark"><Radar size={22} /></div><div><strong>MY Market Radar</strong><span>Shopee × Lazada</span></div></div>
        <div className="market-chip"><span /> MALAYSIA · MYR</div>
        <nav>{NAV.map(({ to, label, mobileLabel, Icon }) => <NavLink key={to} to={to} className={({ isActive }) => isActive ? "nav-item active" : "nav-item"}><Icon size={18} /><span className="nav-label-full">{label}</span><span className="nav-label-mobile">{mobileLabel}</span></NavLink>)}</nav>
        <div className="sidebar-foot"><div className="pulse-line"><span /> 按关键词计划自动更新</div><small>公开数据情报 · Malaysia UTC+8</small></div>
      </aside>
      <div className="main-column">
        <header className="topbar">
          <div><span className="eyebrow">MARKET INTELLIGENCE</span><h1>{current}</h1></div>
          <div className="top-actions"><div className="country-pill"><BarChart3 size={15} /> 双平台实时快照</div><div className="user-pill"><User size={16} /><span>{user?.name || "本地用户"}</span><button aria-label="退出" onClick={() => { logout(); navigate("/login"); }}><LogOut size={15} /></button></div></div>
        </header>
        <main className="content"><Outlet /></main>
      </div>
    </div>
  );
}
