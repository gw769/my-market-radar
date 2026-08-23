import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowRight, Radar, ShieldCheck } from "lucide-react";
import { useAuth } from "@/contexts/AuthContext";

export default function Login() {
  const [email, setEmail] = useState("admin@market.my");
  const [password, setPassword] = useState("admin123");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const { login } = useAuth();
  const navigate = useNavigate();
  const submit = async (event: FormEvent) => {
    event.preventDefault(); setBusy(true); setError("");
    try {
      const res = await fetch("/api/auth/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email, password }) });
      const data = await res.json();
      if (!res.ok) throw new Error(data.message || "登录失败");
      login(data.access_token, { name: data.user.username, email: data.user.email }); navigate("/dashboard");
    } catch (err: any) { setError(err.message || "登录失败"); } finally { setBusy(false); }
  };
  return <div className="login-screen">
    <section className="login-story">
      <div className="login-orbit"><Radar size={42} /></div>
      <span className="eyebrow">MALAYSIA MARKET INTELLIGENCE</span>
      <h1>把两个市场，<br />放进同一张雷达图。</h1>
      <p>输入关键词，读取 Shopee 与 Lazada Malaysia 的公开商品快照，追踪价格、排名与竞争门槛。</p>
      <div className="story-points"><span>MYR 定价带</span><span>每日 20:00</span><span>公开数据</span></div>
    </section>
    <section className="login-panel"><form onSubmit={submit} className="login-card">
      <div className="login-title"><ShieldCheck size={22} /><div><h2>进入本地情报台</h2><p>服务仅监听你的电脑</p></div></div>
      <label>邮箱<input value={email} onChange={(e) => setEmail(e.target.value)} type="email" /></label>
      <label>密码<input value={password} onChange={(e) => setPassword(e.target.value)} type="password" /></label>
      {error && <div className="error-box">{error}</div>}
      <button className="primary-button" disabled={busy}>{busy ? "正在登录…" : "进入市场雷达"}<ArrowRight size={17} /></button>
      <small>默认本地账号已填入；不要把此服务公开到互联网。</small>
    </form></section>
  </div>;
}
