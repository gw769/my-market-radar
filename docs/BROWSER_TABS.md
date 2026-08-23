# Browser Tab Selection

MY Market Radar 使用项目专用 Chrome/Chromium 与本地 CDP 端口，不依赖系统默认浏览器。

## 平台 tab 识别

平台 tab 只按 URL 的真实 hostname 判断：

- Shopee：`shopee.com.my` 及其子域，人工验证额外识别 `xiapibuy.com`；
- Lazada：`lazada.com.my` 及其子域。

查询参数或路径文本里出现 `shopee.com.my` / `lazada.com.my` 不会被当成平台 tab。例如：

```text
https://example.test/?next=https://shopee.com.my/verify
```

不会被误选。

## 正常采集

`ensure_platform_tab()` 明确使用正常采集优先级：

1. Shopee `/search` 或 Lazada `/catalog`；
2. 已存在的 challenge tab；
3. 平台首页/其他页面；
4. 如果没有平台 tab，再创建新的搜索 tab。

采集器随后会在选中的 tab 中导航到当前关键词搜索 URL。

## 人工验证

`find_platform_tab()` 和 `activate_platform_tab()` 默认使用人工验证优先级：

1. captcha / verify / verification / challenge / security-check / punish 页面；
2. Shopee `xiapibuy.com`；
3. Lazada `acs-m.*` security host；
4. 正常 search/catalog；
5. 平台其他页面。

因此项目 Chrome 里即使同时存在首页、搜索页和验证码页，“验证”按钮也会优先激活真正的 challenge tab，而不是依赖 `/json/list` 的随机顺序。

## 为什么分两套优先级

采集需要稳定复用搜索 tab；人工验证需要把用户带到真正需要操作的 challenge tab。以前两者都使用“第一个域名匹配 tab”，多 tab 时会激活错误页面。

这套选择逻辑只处理 tab 选择，不绕过验证码，也不修改浏览器指纹。
