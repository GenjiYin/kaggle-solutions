# 数据目录

本目录存放比赛提供的原始数据。

**`train.csv` 与 `test.csv` 不在本仓库里**，需要你自己从 Kaggle 下载。原因：数据体积较大，且 Kaggle 比赛数据受 Competition Rules 约束，不便在平台外重新分发。

---

## 怎么下载（网页方式，推荐）

**第 1 步 · 打开这个网址**

👉 https://www.kaggle.com/competitions/predict-1-year-us-stock-returns-from-fundamentals/data

（这是本场比赛的「数据」页面，不是首页。）

**第 2 步 · 登录 Kaggle**

没有账号的话，用邮箱注册一个，免费，一分钟。

**第 3 步 · 如果页面提示要先接受比赛规则**

某些比赛要求先点 **Join Competition** 同意规则才能下载数据。看到提示就点一下。

**第 4 步 · 点右上角 `Download All`**

会下载一个压缩包 `predict-1-year-us-stock-returns-from-fundamentals.zip`。

**第 5 步 · 解压到本目录**

把压缩包解压后，**让三个 csv 直接躺在本目录下**，最终长这样：

```
data/
├── train.csv
├── test.csv
└── sample_submission.csv
```

> `sample_submission.csv` 本仓库里已经带了一份，如果压缩包里也有，覆盖掉或者跳过都行。

---

## 怎么确认放对了

放好之后，在上一级目录运行分析脚本：

```bash
python 因子分析.py
```

看到 `[数据] 原始 23,070 行 → 去重后 23,038 行` 就说明找对了。

（如果提示「找不到数据文件」，说明路径不对 —— 检查 csv 是不是被套在了一层子文件夹里。）

---

## 备选：命令行下载

如果你已经配置过 Kaggle API 凭据（`~/.kaggle/access_token` 或环境变量 `KAGGLE_API_TOKEN`），也可以直接用命令下载：

```bash
kaggle competitions download -c predict-1-year-us-stock-returns-from-fundamentals -p data
```

**没配过 API 的话，用上面的网页方式更省事** —— 配 API 需要去 Kaggle 设置页生成 token 再放到指定目录，比点两下鼠标麻烦。

---

## 文件说明

| 文件 | 行 × 列 | 说明 |
|---|---|---|
| `train.csv` | 23,070 × 39 | 训练集，含标签 `return_pct` 与时间字段 `period_start` / `period_end` |
| `test.csv` | 8,520 × 36 | 测试集，**不含标签**，`ticker` 已匿名（`stock_0000` 形式）、无时间字段 |
| `sample_submission.csv` | 8,520 × 2 | 提交格式样例（**已入库**，可直接查看列名与 id 列表） |

字段含义、类型与缺失率详见上一级目录的 `数据字典.md`。
