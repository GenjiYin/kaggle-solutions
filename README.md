# Kaggle 比赛方案合集

记录我在 Kaggle 上参加的**金融 / 量化类比赛**。每个比赛一个独立目录，包含数据说明、分析脚本、报告与方案复盘。

> **声明**：本仓库全部内容均为**回测研究**，不是实盘策略，不构成任何投资建议。
> 所有结论仅基于比赛提供的训练集数据，未经样本外验证的不作任何有效性承诺。

---

## 项目一览

| 项目 | 对应比赛（Kaggle 网页） | 赛题类型 | 核心产出 |
|---|---|---|---|
| [Predict 1-Year US Stock Returns from Fundamentals](<./Predict 1-Year US Stock Returns from Fundamentals/>) | [Predict 1-Year US Stock Returns from Fundamentals](https://www.kaggle.com/competitions/predict-1-year-us-stock-returns-from-fundamentals) | 用 33 个财务指标预测美股未来一年涨跌幅（回归） | 数据字典、因子有效性分析、稳健性检验（32 个字段筛出 4 个）、可交互的静态 HTML 报告 |

> 新增比赛时，在上表追加一行，并在仓库根目录新建同名文件夹。

---

## 重要：本仓库不含比赛数据，需自行去 Kaggle 下载

**为了让仓库保持轻量、并遵守 Kaggle 的数据分发条款，比赛原始数据（`train.csv` / `test.csv`）没有纳入本仓库。**

各项目 `data/` 目录下只有一份说明文件和提交格式样例，运行脚本前请先自行下载数据。

**下载方式（不需要任何配置，也不需要命令行）：**

1. 打开该比赛的**数据页**（点上表「对应比赛」列的链接进入 Kaggle 比赛页，再切到 **Data** 标签页）；
2. 登录 Kaggle（没有账号就免费注册一个），若页面提示需先接受比赛规则，点一下 **Join Competition**；
3. 点右上角 **Download All** 下载压缩包，**解压后把 csv 文件放进该项目的 `data/` 目录**。

以现有项目为例，直接打开这个网址即可：

👉 **https://www.kaggle.com/competitions/predict-1-year-us-stock-returns-from-fundamentals/data**

每个项目的 `data/README.md` 里都写了该比赛数据页的**直接链接**与分步说明，照着做即可。

> 数据放好之后，直接运行该项目的分析脚本（如 `因子分析.py`），它会自动读取 `data/` 并生成报告。

---

## 目录结构

```
kaggle/
└── Predict 1-Year US Stock Returns from Fundamentals/      # 单个比赛项目
    ├── README.md                                          # 该比赛的完整说明（含结论、方法与口径）
    ├── 因子分析.py                                          # 分析脚本（改配置区即可复用）
    ├── 因子分析报告.html                                     # 分析结果，双击即可打开
    ├── 数据字典.md                                          # 39 个字段的含义、类型、缺失率
    ├── 方案贴解读/                                          # 优秀方案的中文解读（附英文原文）
    ├── fig/                                               # 报告插图
    └── data/                                              # 数据目录（原始数据需自行下载，见上）
```

---

## 运行环境

单个项目**零额外依赖**，只需要 Python 与以下两个库：

```
pandas
duckdb
```

分析脚本不依赖 matplotlib、不依赖 Web 服务、不需要联网 —— 生成的报告是**自包含的 HTML**（图表用内联 SVG 手写），双击即可打开。

```bash
cd "Predict 1-Year US Stock Returns from Fundamentals"
python 因子分析.py          # 重新生成 因子分析报告.html
```
