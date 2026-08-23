# Browser Tab Selection

当前正式部署显式使用 `BROWSER_MODE=extension`。MY Market Radar Browser Bridge 作为 managed Chrome extension 运行在用户自己平时使用的 Google Chrome 中，因此能够复用用户已经登录的 Shopee Malaysia / Lazada Malaysia 会话。

这条路径不使用 Playwright，不启动项目专用 Chrome / Chromium，不创建独立 profile，也不让后端直接连接 CDP `9231`。后端与扩展只通过本机 `127.0.0.1:9232` 交换有界命令；扩展再使用 Chrome 的 `tabs` / `debugger` API 操作被选中的真实标签页。

## 前提

- 保持用户自己的 Google Chrome 打开；
- 在该 Chrome 中正常登录 Shopee / Lazada；
- managed MY Market Radar Browser Bridge 已加载；
- 后端进程明确设置 `BROWSER_MODE=extension`。

当前服务器的 `deploy/my-market-radar.service` 已设置 `BROWSER_MODE=extension`，`deploy/chrome-policy.json` 则声明 Browser Bridge 的 managed 安装来源。后端 service 不负责启动或关闭 Chrome。

## 平台 tab 识别

平台 tab 只按 URL 的真实 hostname 判断：

- Shopee：`shopee.com.my` 及其子域，人工验证额外识别 `xiapibuy.com`；
- Lazada：`lazada.com.my` 及其子域，包括验证时可能出现的 `acs-m.*` host。

查询参数或路径文本里出现 `shopee.com.my` / `lazada.com.my` 不会被当成平台 tab。例如：

```text
https://example.test/?next=https://shopee.com.my/verify
```

不会被误选。

## 正常采集：复用并锁定同一 tab

Browser Bridge 为每个平台选择 tab 时使用以下顺序：

1. 已经存在的 Shopee `/search` 或 Lazada `/catalog` tab；
2. 已经存在的平台 challenge tab；
3. 平台首页或其他同 hostname 页面；
4. 完全没有该平台 tab 时，才新建一个搜索 tab。

选中后，Bridge 会把这个 tab 锁定为当前平台采集 tab、激活并聚焦窗口。采集器随后在同一个 tab 中直接导航当前关键词的第 1、2、3 页搜索 URL，不为每一页重复新建 tab。

每一页都有独立的加载 / 滚动时间窗口。采集器会先确认：

- 当前 URL 已经匹配所请求的页码；
- 文档处于 visible 且 interactive / complete；
- 新页面结果已经出现。

确认后才滚动、累计并解析卡片。这样后一页加载较慢时，不会把上一页仍留在虚拟列表中的 DOM 误记为新页面数据。

## 人工验证：激活本次真正卡住的 tab

如果采集中的同一 tab 跳转到了 captcha / challenge / security 页面，run 会进入 `needs_verification`，程序不会自动绕过。

点击界面的“需要人工验证”时，Bridge 1.0.7 会优先激活本次采集锁定的那个平台 tab，而不是重新猜测任意一个同域 tab。用户在自己的 Chrome 中手动完成验证并保留该 tab，然后回到应用点击“继续”。

兼容旧版本 Bridge 时，才使用验证优先级 fallback：

1. captcha / verify / verification / challenge / security-check / punish 页面；
2. Shopee `xiapibuy.com`；
3. Lazada `acs-m.*` security host；
4. 正常 search / catalog；
5. 平台其他页面。

这套逻辑只选择和激活 tab，不修改浏览器指纹，不读取平台私有后台，也不伪造或绕过验证码。

## Bridge 暂时无响应

Chrome 的 Manifest V3 service worker 可能在空闲时暂停。后端会给 Bridge 一个完整的唤醒周期，并对采集 attach 做一次有界重试；因此短暂等待不等于必须重新登录。

如果最终仍提示 Bridge 未连接，请确认普通 Google Chrome 仍在运行、managed extension 已加载，并保留已有 Shopee / Lazada 登录 tab。不要改用无登录态的项目专用浏览器来规避这个提示。

## 可选开发 fallback：CDP 独立 profile

`BROWSER_MODE=cdp` 兼容路径仍保留：它使用 `backend/data/browser-profile`、本地端口 `9231` 和项目专用 Chrome / Chromium，主要用于隔离开发调试。

该 fallback 不复用用户普通 Chrome 的登录态，不是当前 systemd 正式部署，也不应与 `BROWSER_MODE=extension` 同时运行。legacy `deploy/market-verification-browser.service` 只属于这条 CDP fallback。
