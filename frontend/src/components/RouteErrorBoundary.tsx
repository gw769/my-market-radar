import { Component, type ErrorInfo, type ReactNode } from "react";
import { AlertTriangle, RefreshCw } from "lucide-react";

const AUTO_RELOAD_WINDOW_MS = 60_000;
const CHUNK_ERROR_PATTERN = /chunkloaderror|loading chunk|failed to fetch dynamically imported module|error loading dynamically imported module|importing a module script failed/i;

interface RouteErrorBoundaryProps {
  children: ReactNode;
  resetKey: string;
}

interface RouteErrorBoundaryState {
  error: Error | null;
}

function isChunkLoadError(error: Error): boolean {
  return CHUNK_ERROR_PATTERN.test(`${error.name}: ${error.message}`);
}

export default class RouteErrorBoundary extends Component<RouteErrorBoundaryProps, RouteErrorBoundaryState> {
  state: RouteErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): RouteErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, _info: ErrorInfo) {
    if (!isChunkLoadError(error)) return;

    const storageKey = `route-chunk-reload:${this.props.resetKey}`;
    try {
      const lastReload = Number(window.sessionStorage.getItem(storageKey) || 0);
      if (Date.now() - lastReload < AUTO_RELOAD_WINDOW_MS) return;
      window.sessionStorage.setItem(storageKey, String(Date.now()));
      window.location.reload();
    } catch {
      // If sessionStorage is unavailable, avoid an automatic reload loop and show manual recovery.
    }
  }

  componentDidUpdate(previousProps: RouteErrorBoundaryProps) {
    if (previousProps.resetKey !== this.props.resetKey && this.state.error) {
      this.setState({ error: null });
    }
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;

    const chunkFailure = isChunkLoadError(error);
    return <div className="data-state error-state panel" role="alert">
      <AlertTriangle />
      <div>
        <strong>{chunkFailure ? "页面资源已更新" : "这个页面暂时无法显示"}</strong>
        <span>{chunkFailure ? "自动刷新未恢复，请手动重新加载最新页面。" : "页面组件发生异常，重新加载后再试。"}</span>
      </div>
      <button onClick={() => window.location.reload()}><RefreshCw />重新加载页面</button>
    </div>;
  }
}
