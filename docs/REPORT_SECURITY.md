# Report Security

MY Market Radar 的 Excel 报告包含两类不可信字符串：

- 用户输入的关键词；
- Shopee/Lazada 页面上的商品标题、店铺、地区等公开文本。

这些文本不能直接当作 Excel 单元格公式写入。

## 公式注入风险

如果公开商品标题类似：

```text
=HYPERLINK("https://example.test","click")
```

openpyxl 会把以 `=` 开头的字符串识别为公式单元格。恶意 marketplace 文本因此可能进入用户下载的 `.xlsx`。

## 当前处理

`backend/app/services/marketplace/report.py` 的所有字符串都通过统一 `_excel_safe()` / `_append()` 写入工作簿。

首个非空白字符是以下任一个时：

```text
=  +  -  @
```

系统会给单元格值加文本前缀，使其保持 literal text，不作为公式执行。

这个处理覆盖：

- 综合结论页的关键词、建议和说明；
- Shopee/Lazada 商品表的标题、店铺、地区、链接；
- 每日价格/排名趋势里的商品标题；
- 数据口径说明页。

数字字段仍按数字写入，不受影响。

## 回归测试

`backend/tests/test_report_security.py` 会导出带恶意公式前缀的关键词、商品标题、店铺和建议，再重新读取工作簿，验证这些单元格不是 `data_type='f'` 的公式。
