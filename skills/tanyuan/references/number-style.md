# 数字、单位与口径

## 读取数据

先读 `ty describe <tool>` 和本次响应的 `provenance.units`、`warnings`。
说明可能适用于整个工具、某个 kind 或单条记录;使用前确认适用范围。
原值与转换值以工具声明为准,字段后缀不是独立证据。单位为空或含义未确认时,
先查来源说明或原始文件;仍不能确定就说明不足,不补猜测值。

## 来源说明

| 工具 / 范围 | 含义与单位 | 依据入口 |
|---|---|---|
| `fundamental.financials` 新浪 | 保留科目名称与原值;金额科目按元理解,每股指标等需看科目说明;`yoy_ratio` 是比例 | 源财报及返回的 source_url;不把金额单位扩展到所有科目 |
| `fundamental.earnings` forecast | 净利润区间为万元;变动区间为百分比 | [Tushare 业绩预告](https://tushare.pro/document/2?doc_id=45) |
| `fundamental.earnings` express | 营收、净利润和 `yoy_net_profit` 为元;后者是去年同期修正后净利润,不是增长率 | [Tushare 业绩快报](https://tushare.pro/document/2?doc_id=46) |
| `industry.ranking` | `amount_wan` 与 `total_mv` 都为万元 | [Tushare 申万行情](https://tushare.pro/document/2?doc_id=327) |
| `market.daily` | 成交额为千元,成交量为手 | [Tushare 日线](https://tushare.pro/document/2?doc_id=27) |
| `market.quote` | 按响应字段说明;成交量使用记录中的 `volume_unit` | 腾讯行情及 `ty describe market.quote` |
| `market.margin` | 金额从元转成亿元,原值保留在 `raw` | [Tushare 两融明细](https://tushare.pro/document/2?doc_id=59) |
| `market.northbound` | 北向净买入与成交额分别返回,适用时期见 `disclosure_change`;南向原字段口径未核验 | [港交所披露调整](https://www.hkex.com.hk/News/Market-Communications/2024/2404122news?sc_lang=en)及本次响应说明 |

源说明与实际数据冲突时,保留两者并披露冲突;不要静默选择一个口径。

## 分析和呈现

- 比较前统一币种、单位、期间和指标含义。成交额与净买入不可拼成同一指标。
- 中间计算保留精度,展示时再选择元、万元、亿元和小数位数。
- 同比与环比使用各自对应期间;负基数下优先同时列出两期原值。
- 区分营业收入与营业总收入、归母与合并净利润、扣非与不扣非、累计与单季。
- 金额/成交量反算均价、市值/股价反算股本可发现异常,但不能单独证明单位或口径。
- 比较价格时统一复权口径;复权价不等于历史真实成交价。
- 数字引用附来源及相关业务日期;`observed_at` 仅表示取得时间。报告期、公告日与历史版本可见性见 `evidence-rules.md`。
