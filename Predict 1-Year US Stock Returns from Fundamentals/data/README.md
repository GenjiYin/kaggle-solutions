# 数据目录

本目录存放比赛提供的原始数据。

**`train.csv` 与 `test.csv` 不纳入版本控制** —— 它们体积较大，且 Kaggle 比赛数据受该比赛 Competition Rules 约束，不便在平台外重新分发。

## 获取方式

先配置 Kaggle API 凭据（放到 `~/.kaggle/access_token`，或设置环境变量 `KAGGLE_API_TOKEN`），然后：

```bash
kaggle competitions download -c predict-1-year-us-stock-returns-from-fundamentals -p data
```

（若客户端不自动解压，手动解压得到的 `.zip` 即可。）

## 文件说明

| 文件 | 行 × 列 | 说明 |
|---|---|---|
| `train.csv` | 23,070 × 39 | 训练集，含标签 `return_pct` 与时间字段 `period_start` / `period_end` |
| `test.csv` | 8,520 × 36 | 测试集，**不含标签**，`ticker` 已匿名（`stock_0000` 形式）、无时间字段 |
| `sample_submission.csv` | 8,520 × 2 | 提交格式样例（**已入库**，可直接查看列名与 id 列表） |

字段含义、类型与缺失率详见上一级目录的 `数据字典.md`。
