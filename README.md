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

## 目录结构

```
kaggle/
├── README.md                                              # 本文件
├── 量化比赛清单.md                                          # 金融/量化类比赛汇总（含已结束的经典赛）
└── Predict 1-Year US Stock Returns from Fundamentals/      # 单个比赛项目
    ├── README.md                                          # 该比赛的完整说明（含结论、方法与口径）
    ├── 因子分析.py                                          # 分析脚本（改配置区即可复用）
    ├── 因子分析报告.html                                     # 分析结果，双击即可打开
    ├── 数据字典.md                                          # 39 个字段的含义、类型、缺失率
    ├── 方案贴解读/                                          # 优秀方案的中文解读（附英文原文）
    ├── fig/                                               # 报告插图
    └── data/                                              # 原始数据（不入库，见下）
```

---

## 关于数据

**比赛原始数据不入库。** 原因有两点：

1. 数据体积较大（单个比赛约 7 MB），且属于派生资源；
2. Kaggle 比赛数据受该比赛的 Competition Rules 约束，通常不允许在平台外重新分发。

各项目目录下的 `README.md` 里有对应的数据获取命令。以现有项目为例：

```bash
kaggle competitions download -c predict-1-year-us-stock-returns-from-fundamentals -p data
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

---

## 目录里的比赛

| 比赛 | 类型 | 状态 |
|---|---|---|
| Predict 1-Year US Stock Returns from Fundamentals | 基本面因子 → 收益预测 | 分析完成 |

更多金融/量化方向的比赛（含已结束但数据仍可下载的经典赛）见 [`量化比赛清单.md`](./量化比赛清单.md)。
