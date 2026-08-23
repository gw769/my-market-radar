# Static Frontend Serving

MY Market Radar 在本机/单容器模式下由 FastAPI 直接提供 `frontend/dist` 构建产物。

`/assets` 使用 Starlette `StaticFiles`。其他 SPA 路由由 `backend/app/main.py` 的 fallback 处理，例如 favicon、根目录静态文件和 `route.html` fallback。

## 路径约束

所有 fallback 文件路径都先经过：

```text
_safe_frontend_path(relative_path)
```

处理步骤：

1. resolve `frontend/dist` 的真实路径；
2. resolve `frontend/dist / relative_path`；
3. 要求 candidate 仍然可以 `relative_to(frontend/dist)`；
4. 任何 `..`、absolute path 或指向 dist 外部的 symlink 都返回 `None`。

因此类似：

```text
../backend/data/marketplace_ai.db
assets/../../.env
```

不会被 SPA fallback 当成静态文件返回。

API 路径仍在 fallback 前被排除，正常 SPA 路由找不到实体文件时继续返回 `index.html`。

回归测试位于 `backend/tests/test_frontend_paths.py`。
