# -*- coding: utf-8 -*-
"""
================================================================================
基本面因子分析 —— 单文件版
================================================================================
一次运行，产出一份静态 HTML 报告。

    python 因子分析.py

产物：同目录下的「因子分析报告.html」
      —— 内联 SVG、零外部依赖、无需启动服务、双击即可打开、可直接发给别人。

数据来源：同目录 data/train.csv（去重后 23,038 行 × 16 个季度截面）

公开接口：
    build_factors()            因子 SQL 生成（默认用字段名，可覆盖成自定义 SQL）
    配置区在文件最上面，通常只需要改 CSV_DIR / OUT_HTML / FACTORS
================================================================================
"""
import html
import math
import os
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

try:
    import duckdb
except ImportError:
    sys.exit("[错误] 需要 duckdb：在 vnpy 环境里运行，或 pip install duckdb")


# ==============================================================================
# ① 配置区  —— 通常只需要改这里
# ==============================================================================
BASE = Path(__file__).resolve().parent
CSV_DIR = BASE / "data"                       # 放 train.csv 的目录
OUT_HTML = BASE / "因子分析报告.html"           # 输出文件
TRAIN_FILE = "train.csv"

N_GROUPS = 5                                   # 分档数（不含缺失组）
MIN_CROSS_N = 30                               # 一个截面少于这么多只股票就不算 IC
WINSOR_Q = (0.01, 0.99)                        # 标签缩尾分位
FIT_YEARS = (2019, 2020, 2021)                 # 只用这几年定缩尾边界（防泄漏）
DROP_FEATURES = {"sector_code"}                # 不作因子（只用来做行业中性化）

# 因子家族（只影响展示顺序，不影响计算）
FAMILIES = [
    ("估值",     ["pe_ttm", "price_to_book", "price_to_sales", "growth_pe_ratio"]),
    ("盈利能力", ["gross_margin", "operating_margin", "net_margin", "roa", "roe", "rote"]),
    ("成长",     ["revenue_growth_3y", "revenue_growth_yoy"]),
    ("报表科目", ["revenue_ttm", "net_income_ttm", "income_before_tax",
                  "eps_basic", "eps_diluted", "total_assets", "stockholders_equity",
                  "current_assets", "current_liabilities", "long_term_debt",
                  "goodwill", "inventory"]),
    ("股数",     ["shares_outstanding", "shares_diluted"]),
    ("杠杆流动性", ["current_ratio", "quick_ratio", "debt_to_equity"]),
    ("分红",     ["dividend_yield", "dividends_ttm", "dividends_paid_ttm"]),
]

# 自定义因子：想算「字段的组合」就写在这里，键=因子名，值=必须返回 id/value 两列的 SQL。
# 不写的话，系统自动为每个字段生成一条。
CUSTOM_FACTORS = {
    # "roe_减行业中位": """
    #     SELECT id,
    #            roe - MEDIAN(roe) OVER (PARTITION BY era, sector_code) AS value
    #     FROM train_clean
    # """,
}

META_COLS = {"id", "ticker", "start_year", "period_start", "period_end", "return_pct"}
# 脚本内部派生的列，不能当因子（DuckDB 里 SELECT age, * 会生成 era_1，见 build_db 注释）
DERIVED_COLS = {"year", "ret_w", "ret_ex", "ret_ex_sec", "is_tail", "era_1"}
_CN_NUM = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六", 7: "七", 8: "八"}


# ==============================================================================
# ② 数据层：读 CSV → 去重 → 造 era → 标签处理
# ==============================================================================
def build_db(csv_dir: Path, verbose=True):
    """把 train.csv 读进 DuckDB，去重、造 era，返回连接（表名 train_clean）。"""
    csv_path = Path(csv_dir) / TRAIN_FILE
    if not csv_path.exists():
        sys.exit(f"[错误] 找不到数据文件：{csv_path}\n"
                 f"       请把 train.csv 放到 {csv_dir}，或修改脚本顶部的 CSV_DIR")

    con = duckdb.connect(":memory:")
    con.execute(
        "CREATE TABLE raw AS SELECT * FROM read_csv(?, header=true, all_varchar=true)",
        [str(csv_path).replace("\\", "/")])

    n_raw = con.execute("SELECT COUNT(*) FROM raw").fetchone()[0]

    # 去掉「除 id 外所有列完全相同」的重复行（ET / PLD 各 16 组，共 32 行）
    cols = [r[0] for r in con.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='raw' ORDER BY ordinal_position").fetchall()]
    key_cols = [c for c in cols if c != "id"]
    # 注意：这里只写 SELECT * EXCLUDE (rn)。
    # 若写成 SELECT era, * EXCLUDE (rn) —— DuckDB 不会报错，而是把重名列
    # 悄悄改名为 era_1，表里就多出一列垃圾。踩过一次。
    con.execute(f"""
        CREATE TABLE train_clean AS
        SELECT * EXCLUDE (rn) FROM (
            SELECT *,
                   year(period_start::DATE) || '-Q'
                       || quarter(period_start::DATE) AS era,
                   ROW_NUMBER() OVER (PARTITION BY {', '.join(key_cols)}
                                      ORDER BY id) AS rn
            FROM raw
        ) WHERE rn = 1
    """)
    n_clean = con.execute("SELECT COUNT(*) FROM train_clean").fetchone()[0]

    if verbose:
        print(f"[数据] 原始 {n_raw:,} 行 → 去重后 {n_clean:,} 行"
              f"（删掉 {n_raw - n_clean} 行重复）")
    return con


def prepare_frame(con, verbose=True):
    """把 train_clean 取到 pandas，算好超额收益与各种标签版本。"""
    df = con.execute("SELECT * FROM train_clean").df()
    # CSV 是按 all_varchar 读进来的，必须显式转数值（否则排序会按字符串比大小）
    df["period_start"] = pd.to_datetime(df["period_start"])
    df["period_end"] = pd.to_datetime(df["period_end"])
    keep_str = {"ticker", "era", "period_start", "period_end"}
    for c in df.columns:
        if c not in keep_str:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df["year"] = df["period_start"].dt.year

    # --- 标签缩尾：边界只用 2019-2021（拟合集）算，避免用到 2022 的信息 ---
    fit = df[df["year"].isin(FIT_YEARS)]["return_pct"]
    lo, hi = fit.quantile(WINSOR_Q[0]), fit.quantile(WINSOR_Q[1])
    df["ret_w"] = df["return_pct"].clip(lo, hi)

    # --- 超额收益：每个截面减去当期均值（不减会被大盘涨跌主导）---
    df["ret_ex"] = df["ret_w"] - df.groupby("era")["ret_w"].transform("mean")
    # --- 行业中性版：在「截面 × 行业」内减均值 ---
    df["ret_ex_sec"] = df["ret_w"] - df.groupby(
        ["era", "sector_code"], dropna=False)["ret_w"].transform("mean")

    # --- 极端样本标记（收益两端各 1%），用于鲁棒性对照 ---
    q1, q99 = df["return_pct"].quantile(0.01), df["return_pct"].quantile(0.99)
    df["is_tail"] = (df["return_pct"] < q1) | (df["return_pct"] > q99)

    if verbose:
        print(f"[标签] 缩尾边界 {lo:,.2f} ~ {hi:,.2f}"
              f"（缩尾前标准差 {df['return_pct'].std():,.1f} → 缩尾后 {df['ret_w'].std():,.1f}）")
        print(f"[截面] {df['era'].nunique()} 个季度，"
              f"每个截面 {df.groupby('era').size().min()}~{df.groupby('era').size().max()} 只股票")
        print(f"[极端] 收益两端各 1% 共 {int(df['is_tail'].sum())} 行")
    return df, (lo, hi)


# ==============================================================================
# ③ 因子层：分档 + 逐截面 IC
# ==============================================================================
def _bucketize(series: pd.Series, n_groups: int) -> pd.Series:
    """按百分位排名分档，返回 1..n_groups。用排名而不是数值 → 对极端值免疫。"""
    r = series.rank(pct=True)
    return np.minimum((r * n_groups).astype(int), n_groups - 1) + 1


def _ic(g: pd.DataFrame, col: str, ret_col: str, method="spearman"):
    """一个截面内的 IC。样本太少或取值太少则返回 NaN。"""
    if len(g) < MIN_CROSS_N or g[col].nunique() < 5:
        return np.nan
    v = g[col].corr(g[ret_col], method=method)
    return float(v) if v == v else np.nan


# 留一年法的四个口径名（对应 LOO_YEARS）
LOO_YEARS = (2019, 2020, 2021, 2022)
LOO_LABELS = tuple(f"去{y}" for y in LOO_YEARS)
LOO_KEYS = ("全样本",) + LOO_LABELS


def leave_one_out(ic_series):
    """留一年法：从逐截面 IC 序列算出 5 个口径的 IC 均值。

    为什么这么做：只看「全样本 IC」会被单一年份劫持。例如 eps_basic 全样本
    +0.062 看着不错，但去掉 2021 年后只剩 +0.001 —— 信号其实全来自那一年。
    反过来，price_to_sales 全样本 −0.107 看着一般，去掉 2019 后变成 −0.212，
    说明 2019 是它的反向异常年，而不是它的信号来源。
    """
    arr = [(e, float(v)) for e, v in ic_series if v == v]
    if len(arr) < 8:
        return None
    out = {"全样本": float(np.mean([v for _, v in arr]))}
    for y in LOO_YEARS:
        rest = [v for e, v in arr if int(str(e)[:4]) != y]
        out[f"去{y}"] = float(np.mean(rest)) if rest else np.nan
    return out


def eval_factor(df: pd.DataFrame, name: str):
    """算一个因子的全部指标。返回 dict（含序列，供画图）。"""
    col = f"_f_{name}"
    sub = df[["era", "ticker", "year", "sector_code", "ret_ex", "ret_ex_sec",
              "ret_w", "return_pct", "is_tail", col]].copy()

    ok = sub[col].notna() & sub["ret_ex"].notna()
    valid = sub[ok].copy()
    miss_rate = 1 - len(valid) / len(sub)

    if len(valid) < 200 or valid["era"].nunique() < 2:
        return None

    # ---- 分档（1..N），缺失单独一组记为 0 ----
    valid["bucket"] = valid.groupby("era")[col].transform(_bucketize, N_GROUPS)
    bstat = valid.groupby("bucket")["ret_ex"].agg(["size", "mean", "median"])
    bucket_rows = []
    for b in range(1, N_GROUPS + 1):
        if b in bstat.index:
            r = bstat.loc[b]
            bucket_rows.append({"bucket": b, "label": f"Q{b}", "n": int(r["size"]),
                                "mean": float(r["mean"]), "median": float(r["median"])})
        else:
            bucket_rows.append({"bucket": b, "label": f"Q{b}", "n": 0,
                                "mean": np.nan, "median": np.nan})

    # 缺失组：字段缺失的那些行，超额收益是多少
    miss_rows = sub[sub[col].isna() & sub["ret_ex"].notna()]
    miss_n = int(len(miss_rows))
    miss_mean = float(miss_rows["ret_ex"].mean()) if miss_n else np.nan
    miss_median = float(miss_rows["ret_ex"].median()) if miss_n else np.nan
    bucket_rows.append({"bucket": 0, "label": "缺失", "n": miss_n,
                        "mean": miss_mean, "median": miss_median})
    bucket_rows = [bucket_rows[-1]] + bucket_rows[:-1]   # 缺失放最前

    # ---- 逐截面 IC（主口径：缩尾+去均值 的 Spearman）----
    ics, eras = [], []
    for era, g in valid.groupby("era"):
        v = _ic(g, col, "ret_ex", "spearman")
        if v == v:
            ics.append(v)
            eras.append(era)
    ics = np.array(ics, dtype=float)
    if len(ics) < 2:
        return None

    ic_mean, ic_std = float(ics.mean()), float(ics.std(ddof=1))
    icir = ic_mean / ic_std if ic_std > 0 else np.nan
    t_stat = icir * math.sqrt(len(ics)) if ic_std > 0 else np.nan
    ic_win = float((ics > 0).mean())

    # ---- 对照口径（检验是否被极端值驱动）----
    ic_raw = _nanmean([_ic(g, col, "return_pct", "pearson")
                       for _, g in valid.groupby("era")])
    nt = valid[~valid["is_tail"]]
    ic_notail = _nanmean([_ic(g, col, "ret_ex", "spearman")
                          for _, g in nt.groupby("era")])
    ic_neutral = _nanmean([_ic(g, col, "ret_ex_sec", "spearman")
                           for _, g in valid.groupby("era")])

    # ---- 分年度 ----
    yearly = {}
    for y, g in valid.groupby("year"):
        v = _ic(g, col, "ret_ex", "spearman")
        yearly[int(y)] = float(v) if v == v else np.nan
    ys = [yearly.get(y, np.nan) for y in sorted(yearly)]
    ys_ok = [v for v in ys if v == v]
    sign_consist = (max(sum(1 for v in ys_ok if v > 0),
                        sum(1 for v in ys_ok if v < 0)) if ys_ok else 0)

    # ---- 单调性 / 多空 ----
    qmeans = [r["mean"] for r in bucket_rows if r["bucket"] > 0]
    qmeds = [r["median"] for r in bucket_rows if r["bucket"] > 0]
    # 单调性：用「实际存在的档位编号」和它们的档收益算秩相关。
    # 均值和中位数各算一遍 —— 两者符号相反时，说明该因子的档均收益被极端值主导，
    # 此时应以中位数口径为准（inventory 就是这种情况：均值单调递减、中位数递增）。
    filled = sorted(int(b) for b in bstat.index if int(b) > 0)

    def _mono(vals):
        if len(filled) >= 3 and all(v == v for v in vals):
            return float(pd.Series(vals).corr(pd.Series(filled), method="spearman"))
        return np.nan

    mono = _mono([bstat.loc[b]["mean"] for b in filled])
    mono_med = _mono([bstat.loc[b]["median"] for b in filled])
    spread = qmeans[-1] - qmeans[0] if all(v == v for v in [qmeans[-1], qmeans[0]]) else np.nan
    spread_m = (qmeds[-1] - qmeds[0]) if all(v == v for v in [qmeds[-1], qmeds[0]]) else np.nan

    sizes = [r["n"] for r in bucket_rows if r["bucket"] > 0]
    ic_list = list(zip(eras, ics.tolist()))

    # ---- 稳健性：留一年法（全样本 + 去掉每一年，共 5 个口径）----
    loo = leave_one_out(ic_list)
    keys = ["全样本", LOO_LABELS[0], LOO_LABELS[1], LOO_LABELS[2], LOO_LABELS[3]]
    five = [loo.get(k, np.nan) for k in keys]
    if loo and all(v == v for v in five):
        loo_same = len({np.sign(v) for v in five}) == 1
        loo_ratio = (min(abs(v) for v in five) / abs(loo["全样本"])
                     if loo["全样本"] else np.nan)
    else:
        loo_same, loo_ratio = False, np.nan
    # 第 3 关：行业中性化 / 去掉极端样本后，符号是否仍与主口径一致
    sgn = np.sign(ic_mean)
    k3 = (np.sign(ic_neutral) == sgn) and (np.sign(ic_notail) == sgn)

    if loo_same and loo_ratio >= 0.5 and k3:
        grade = "通过"
    elif not loo_same:
        grade = "翻号"
    elif loo_ratio < 0.5:
        grade = "靠单年"
    else:
        grade = "算法敏感"

    return {
        "name": name,
        "ic_mean": ic_mean, "ic_std": ic_std, "icir": icir, "t_stat": t_stat,
        "ic_win": ic_win, "n_cross": len(ics),
        "ic_raw": float(ic_raw), "ic_notail": float(ic_notail),
        "ic_neutral": float(ic_neutral),
        "miss_rate": miss_rate, "n_used": int(len(valid)),
        "min_bucket": int(min(sizes)) if sizes else 0,
        "n_buckets": len(filled), "filled_buckets": filled,
        "mono": mono, "mono_med": mono_med,
        "spread": float(spread), "spread_m": float(spread_m),
        "yearly": yearly, "sign_consist": sign_consist,
        "buckets": bucket_rows,
        "ic_series": ic_list,
        "loo": loo, "loo_same": loo_same, "loo_ratio": loo_ratio,
        "k3": k3, "grade": grade,
    }


def analyze_all(df, factor_sqls, verbose=True):
    """跑所有因子。factor_sqls: {因子名: SQL}（SQL 须返回 id / value 两列）。

    合并键必须用 id（行唯一标识），不能用 (era, ticker)：
    有些公司在同一个日历季度里有两个财季末（例如 ARLO 2022-10-02 与 2022-12-31
    同属 2022-Q4），按 (era, ticker) 合并会把这些行叉乘成 4 行，样本量虚增。
    """
    import duckdb as _dd
    con = _dd.connect(":memory:")
    con.register("train_clean", df.drop(columns=[c for c in df.columns
                                                 if c.startswith("_f_")]))

    results = []
    for name, sql in factor_sqls.items():
        try:
            v = con.execute(f"SELECT id, value AS _f_{name} FROM ({sql})").df()
        except Exception as e:
            print(f"  [跳过] {name}: SQL 执行失败 — {str(e)[:120]}")
            continue
        v["id"] = pd.to_numeric(v["id"], errors="coerce")
        merged = df.merge(v, on="id", how="left")
        if len(merged) != len(df):
            print(f"  [跳过] {name}: 合并后行数 {len(merged)} ≠ {len(df)}，"
                  f"SQL 必须每行 id 只出现一次")
            continue
        r = eval_factor(merged, name)
        if r is None:
            print(f"  [跳过] {name}: 有效样本不足")
            continue
        results.append(r)
        if verbose:
            print(f"  ✓ {name:<22} IC {r['ic_mean']:+.4f}  ICIR {r['icir']:+.3f}"
                  f"  缺失 {r['miss_rate']*100:5.1f}%")
    return results


# ==============================================================================
# ④ 绘图层：内联 SVG（零依赖）
# ==============================================================================
POS = "#C0392B"      # 正超额收益 = 红（中国市场习惯）
NEG = "#27AE60"      # 负超额收益 = 绿
INK = "#2C2C2A"
SUB = "#7A7A75"
GRID = "#E3E1DC"


def _esc(s):
    return html.escape(str(s))


def _q(s):
    """生成安全的 HTML 属性值（双引号包裹 + 转义）。"""
    return '"' + html.escape(str(s), quote=True) + '"'


def _nanmean(vals):
    """忽略 NaN 求均值；全 NaN 或空则返回 NaN（不报警告）。"""
    a = np.asarray([v for v in vals if v == v], dtype=float)
    return float(a.mean()) if a.size else np.nan


def _nice(v, digits=2):
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    return f"{v:+.{digits}f}"


def _pct(v, digits=1):
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    return f"{v*100:+.{digits}f}%"


def _color(v):
    if v is None or (isinstance(v, float) and v != v):
        return SUB
    return POS if v > 0 else (NEG if v < 0 else SUB)


def _scale(vals):
    vs = [v for v in vals if v == v and v is not None]
    if not vs:
        return -1.0, 1.0
    lo, hi = min(vs + [0.0]), max(vs + [0.0])
    pad = (hi - lo) * 0.12 or 1.0
    return lo - pad, hi + pad


def svg_buckets(buckets, width=380, height=214):
    """分档柱状图：每档两根柱（实心=均值，浅色=中位数）。"""
    x0, x1 = 46, width - 12
    y0, y1 = 26, height - 46
    vals = [b["mean"] for b in buckets] + [b["median"] for b in buckets]
    lo, hi = _scale(vals)
    sy = lambda v: y1 - (v - lo) / (hi - lo) * (y1 - y0) if hi > lo else (y0 + y1) / 2
    step = (x1 - x0) / len(buckets)

    p = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img">']
    p.append(f'<text x="8" y="14" font-size="12" font-weight="500" fill="{INK}">'
             f'分档平均/中位超额收益</text>')
    # 网格与零线
    for k in range(5):
        v = lo + (hi - lo) * k / 4
        y = sy(v)
        p.append(f'<line x1="{x0}" y1="{y:.1f}" x2="{x1}" y2="{y:.1f}" '
                 f'stroke="{GRID}" stroke-width="0.5"/>')
        p.append(f'<text x="{x0-6}" y="{y+3:.1f}" font-size="9" fill="{SUB}" '
                 f'text-anchor="end">{v:,.0f}%</text>')
    yz = sy(0)
    p.append(f'<line x1="{x0}" y1="{yz:.1f}" x2="{x1}" y2="{yz:.1f}" '
             f'stroke="{SUB}" stroke-width="0.8"/>')

    for i, b in enumerate(buckets):
        cx = x0 + step * (i + 0.5)
        for k, (v, w, alpha, tag) in enumerate(
                [(b["mean"], 17, 1.0, "均值"), (b["median"], 17, 0.42, "中位")]):
            if v != v:
                continue
            x = cx - 19 + k * 20
            ytop, ybot = min(sy(v), yz), max(sy(v), yz)
            h = max(ybot - ytop, 1.0)
            col = _color(v)
            p.append(f'<rect x="{x:.1f}" y="{ytop:.1f}" width="{w}" height="{h:.1f}" '
                     f'fill="{col}" fill-opacity="{alpha}" rx="2">'
                     f'<title>{b["label"]} {tag} {v:+.2f}%  (n={b["n"]})</title></rect>')
        p.append(f'<text x="{cx:.1f}" y="{y1+16}" font-size="10" fill="{SUB}" '
                 f'text-anchor="middle">{b["label"]}</text>')
        p.append(f'<text x="{cx:.1f}" y="{y1+28}" font-size="9" fill="{SUB}" '
                 f'text-anchor="middle">{b["n"]}</text>')
    p.append(f'<text x="{x0}" y="{height-4}" font-size="9" fill="{SUB}">'
             f'深色=均值  浅色=中位数  下方数字=样本数</text>')
    p.append("</svg>")
    return "".join(p)


def svg_yearly(yearly, width=380, height=214):
    """分年度 IC 柱状图。"""
    x0, x1 = 46, width - 12
    y0, y1 = 26, height - 46
    items = sorted(yearly.items())
    if not items:
        return ""
    vals = [v for _, v in items]
    lo, hi = _scale(vals)
    sy = lambda v: y1 - (v - lo) / (hi - lo) * (y1 - y0) if hi > lo else (y0 + y1) / 2
    step = (x1 - x0) / len(items)
    yz = sy(0)

    p = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img">']
    p.append(f'<text x="8" y="14" font-size="12" font-weight="500" fill="{INK}">'
             f'分年度 IC（4 根同号才算稳）</text>')
    for k in range(5):
        v = lo + (hi - lo) * k / 4
        y = sy(v)
        p.append(f'<line x1="{x0}" y1="{y:.1f}" x2="{x1}" y2="{y:.1f}" '
                 f'stroke="{GRID}" stroke-width="0.5"/>')
        p.append(f'<text x="{x0-6}" y="{y+3:.1f}" font-size="9" fill="{SUB}" '
                 f'text-anchor="end">{v:+.2f}</text>')
    p.append(f'<line x1="{x0}" y1="{yz:.1f}" x2="{x1}" y2="{yz:.1f}" '
             f'stroke="{SUB}" stroke-width="0.8"/>')
    for i, (y, v) in enumerate(items):
        cx = x0 + step * (i + 0.5)
        if v != v:
            continue
        ytop, ybot = min(sy(v), yz), max(sy(v), yz)
        p.append(f'<rect x="{cx-17:.1f}" y="{ytop:.1f}" width="34" '
                 f'height="{max(ybot-ytop,1.0):.1f}" fill="{_color(v)}" '
                 f'fill-opacity="0.9" rx="3"><title>{y} 年 IC {v:+.4f}</title></rect>')
        p.append(f'<text x="{cx:.1f}" y="{y1+16}" font-size="10" fill="{SUB}" '
                 f'text-anchor="middle">{y}</text>')
    p.append("</svg>")
    return "".join(p)


def svg_cumic(ic_series, width=380, height=214):
    """累计 IC 折线。"""
    x0, x1 = 46, width - 12
    y0, y1 = 26, height - 46
    if len(ic_series) < 2:
        return ""
    items = sorted(ic_series, key=lambda t: t[0])
    cum, acc = [], 0.0
    for _, v in items:
        acc += v
        cum.append(acc)
    lo, hi = _scale(cum)
    sx = lambda i: x0 + (x1 - x0) * i / (len(cum) - 1)
    sy = lambda v: y1 - (v - lo) / (hi - lo) * (y1 - y0) if hi > lo else (y0 + y1) / 2
    yz = sy(0)

    pts = " ".join(f"{sx(i):.1f},{sy(v):.1f}" for i, v in enumerate(cum))
    area = f"{sx(0):.1f},{yz:.1f} " + pts + f" {sx(len(cum)-1):.1f},{yz:.1f}"
    p = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img">']
    p.append(f'<text x="8" y="14" font-size="12" font-weight="500" fill="{INK}">'
             f'累计 IC（{len(cum)} 个季度）</text>')
    for k in range(5):
        v = lo + (hi - lo) * k / 4
        y = sy(v)
        p.append(f'<line x1="{x0}" y1="{y:.1f}" x2="{x1}" y2="{y:.1f}" '
                 f'stroke="{GRID}" stroke-width="0.5"/>')
        p.append(f'<text x="{x0-6}" y="{y+3:.1f}" font-size="9" fill="{SUB}" '
                 f'text-anchor="end">{v:+.1f}</text>')
    p.append(f'<polygon points="{area}" fill="{POS if cum[-1] > 0 else NEG}" '
             f'fill-opacity="0.10"/>')
    p.append(f'<polyline points="{pts}" fill="none" '
             f'stroke="{POS if cum[-1] > 0 else NEG}" stroke-width="1.8"/>')
    p.append(f'<line x1="{x0}" y1="{yz:.1f}" x2="{x1}" y2="{yz:.1f}" '
             f'stroke="{SUB}" stroke-width="0.6"/>')
    for i in sorted({0, len(items) // 3, 2 * len(items) // 3, len(items) - 1}):
        anchor = "start" if i == 0 else ("end" if i == len(items) - 1 else "middle")
        p.append(f'<text x="{sx(i):.1f}" y="{y1+16}" font-size="9" fill="{SUB}" '
                 f'text-anchor="{anchor}">{items[i][0]}</text>')
    p.append("</svg>")
    return "".join(p)


def heat_color(v, vmax):
    """红-白-绿 发散色。返回 (背景色, 文字色)。

    用接近线性的映射：弱信号就该看起来弱，否则满屏红绿反而读不出差异。
    """
    if v is None or v != v:
        return "#F5F4F1", SUB
    t = max(-1.0, min(1.0, v / vmax)) if vmax else 0.0
    a = abs(t) ** 0.9
    base = (192, 57, 43) if t >= 0 else (39, 174, 96)
    rgb = tuple(int(255 - (255 - c) * a) for c in base)
    fg = "#FFFFFF" if a > 0.68 else INK
    return f"rgb({rgb[0]},{rgb[1]},{rgb[2]})", fg


def svg_forest(groups, width=680):
    """森林图：每行一个因子，把 5 个口径的 IC 画成点并连线，0 处画竖线。

    这是稳健性筛选最直观的一张图 —— 「5 个点是不是都在竖线同一侧」
    用眼睛看比读数字快得多；跨到另一侧的点直接标红。
    """
    NAME_X = 148          # 因子名右对齐位置
    X0, X1 = 158, 578     # 绘图区
    BADGE_X, BADGE_W = 590, 50
    ROW_H, HEAD_H = 22, 26
    TOP = 58

    allv = [v for _, rows in groups for r in rows
            for v in (r["loo"] or {}).values() if v == v]
    vmax = max(abs(v) for v in allv) * 1.05 if allv else 0.1
    mid = (X0 + X1) / 2
    half = (X1 - X0) / 2
    sx = lambda v: mid + v / vmax * half
    yz = sx(0)

    n_rows = sum(len(rows) for _, rows in groups) + len(groups)
    height = TOP + n_rows * ROW_H + len(groups) * (HEAD_H - ROW_H) + 74

    p = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img">']
    p.append(f'<text x="8" y="16" font-size="12" font-weight="500" fill="{INK}">'
             f'森林图 · 留一年法：大点＝全样本，小点＝去掉某一年的结果</text>')

    def _tick(v):
        return "0" if abs(v) < 1e-9 else f"{v:+.2f}"

    # 竖轴刻度（顶部）
    p.append(f'<line x1="{X0}" y1="{TOP-14}" x2="{X1}" y2="{TOP-14}" '
             f'stroke="{GRID}" stroke-width="0.5"/>')
    for v in (-vmax, -vmax / 2, 0, vmax / 2, vmax):
        x = sx(v)
        p.append(f'<line x1="{x:.1f}" y1="{TOP-14}" x2="{x:.1f}" y2="{height-58}" '
                 f'stroke="{"#5F5E5A" if v == 0 else GRID}" '
                 f'stroke-width="{1.0 if v == 0 else 0.5}"/>')
        p.append(f'<text x="{x:.1f}" y="{TOP-18}" font-size="9" fill="{SUB}" '
                 f'text-anchor="middle">{_tick(v)}</text>')

    y = TOP
    for gname, rows in groups:
        p.append(f'<rect x="{X0}" y="{y-13}" width="{X1-X0+72}" height="{HEAD_H-6}" '
                 f'rx="4" fill="#F1F0ED"/>')
        p.append(f'<text x="{X0+6}" y="{y+3}" font-size="11.5" font-weight="500" '
                 f'fill="{INK}">{_esc(gname)}（{len(rows)} 个）</text>')
        y += HEAD_H
        for r in rows:
            loo = r["loo"] or {}
            vals = [loo.get(k, np.nan) for k in LOO_KEYS]
            xs = [sx(v) for v in vals if v == v]
            lg = r["grade"]
            col = {"通过": "#185FA5", "靠单年": "#888780", "翻号": "#888780"}[lg]
            if len(xs) >= 2:
                p.append(f'<line x1="{min(xs):.1f}" y1="{y}" x2="{max(xs):.1f}" '
                         f'y2="{y}" stroke="{col}" stroke-width="1.2" '
                         f'stroke-opacity="0.45"/>')
            sgn = np.sign(r["ic_mean"])
            for k, v in zip(LOO_KEYS, vals):
                if v != v:
                    continue
                x = sx(v)
                if k == "全样本":
                    p.append(f'<circle cx="{x:.1f}" cy="{y}" r="4.4" fill="{col}">'
                             f'<title>{r["name"]} 全样本 IC {v:+.4f}</title></circle>')
                else:
                    cross = np.sign(v) != sgn
                    rr, cc = (5.2, "#A32D2D") if cross else (2.8, col)
                    op = "1" if cross else "0.55"
                    p.append(f'<circle cx="{x:.1f}" cy="{y}" r="{rr}" fill="{cc}" '
                             f'fill-opacity="{op}">'
                             f'<title>{r["name"]} {k} IC {v:+.4f}'
                             f'{"（翻号）" if cross else ""}</title></circle>')
            nm = r["name"]
            p.append(f'<text x="{NAME_X}" y="{y+4}" text-anchor="end" font-size="11" '
                     f'fill="{INK if lg == "通过" else SUB}">{_esc(nm)}</text>')
            chip = {"通过": ("#E1F5EE", "#0F6E56", "通过"),
                    "靠单年": ("#FAEEDA", "#854F0B", "靠单年"),
                    "翻号": ("#FCEBEB", "#A32D2D", "翻号")}[lg]
            p.append(f'<rect x="{BADGE_X}" y="{y-8}" width="{BADGE_W}" height="16" '
                     f'rx="4" fill="{chip[0]}" stroke="{chip[1]}" stroke-width="0.5"/>')
            p.append(f'<text x="{BADGE_X+BADGE_W/2:.0f}" y="{y+3.5}" '
                     f'text-anchor="middle" font-size="10" fill="{chip[1]}">'
                     f'{chip[2]}</text>')
            y += ROW_H
    y += 10
    # 底部再标一遍刻度（图很长，方便对照）
    for v in (-vmax, -vmax / 2, 0, vmax / 2, vmax):
        x = sx(v)
        p.append(f'<text x="{x:.1f}" y="{y}" font-size="9" fill="{SUB}" '
                 f'text-anchor="middle">{_tick(v)}</text>')
    p.append(f'<text x="{X0}" y="{y+18}" font-size="10" fill="{SUB}">'
             f'横轴 = IC 值；竖线在 0。</text>')
    p.append(f'<text x="{X0}" y="{y+33}" font-size="10" fill="#A32D2D">'
             f'红点 = 去掉那一年后方向翻转（该因子被淘汰）；'
             f'小点紧贴竖线 = 信号只来自某一年。</text>')
    p.append("</svg>")
    return "".join(p)


# 稳健性判定 → CSS 类名
_GRADE_CLS = {"通过": "pass", "靠单年": "solo", "算法敏感": "fragile", "翻号": "flip"}


def _fail_reason(r):
    """给没通过的因子生成一句人话原因。"""
    loo = r["loo"] or {}
    if not loo:
        return "有效样本不足"
    base = loo.get("全样本", np.nan)
    sgn = 1 if base > 0 else -1
    flips = [k[1:] for k in LOO_LABELS
             if loo.get(k, np.nan) == loo.get(k, np.nan) and np.sign(loo[k]) != sgn]
    if flips:
        return f"去掉 {'、'.join(flips)} 后方向翻转 —— 不是稳定规律，只是那几年碰巧"
    cand = [(k, abs(loo[k])) for k in LOO_LABELS
            if loo.get(k, np.nan) == loo.get(k, np.nan)]
    if r["loo_ratio"] < 0.5 and cand:
        wk, _ = min(cand, key=lambda t: t[1])
        return (f"去掉 {wk[1:]} 后 IC 只剩 {loo[wk]:+.4f}"
                f"（全样本 {base:+.4f}）—— 信号主要来自那一年")
    if not r.get("k3"):
        return "行业中性化或去掉极端样本后方向改变 —— 结论依赖行业结构或极端值"
    return "—"


def year_corr(results, years):
    """跨因子看：各年度 IC 之间的秩相关矩阵（4×4）。"""
    mat = {}
    for a in years:
        for b in years:
            xs = [(r["yearly"].get(a, np.nan), r["yearly"].get(b, np.nan))
                  for r in results]
            pairs = [(x, y) for x, y in xs if x == x and y == y]
            if len(pairs) >= 5:
                s1 = pd.Series([p[0] for p in pairs])
                s2 = pd.Series([p[1] for p in pairs])
                v = s1.corr(s2, method="spearman")
                mat[(a, b)] = float(v) if v == v else np.nan
            else:
                mat[(a, b)] = np.nan
    return mat


# ==============================================================================
# ⑤ 报告层：拼 HTML
# ==============================================================================
CSS = """
:root{
  --bg:#FFFFFF; --panel:#FAFAF8; --line:#E3E1DC; --ink:#2C2C2A;
  --sub:#7A7A75; --accent:#378ADD; --pos:#C0392B; --neg:#27AE60;
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--ink);
  font-family:-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
  font-size:13.5px;line-height:1.65}
.wrap{max-width:1280px;margin:0 auto;padding:32px 24px 64px}
h1{font-size:24px;font-weight:600;margin-bottom:6px}
h2{font-size:17px;font-weight:600;margin:34px 0 12px;padding-bottom:7px;
   border-bottom:1px solid var(--line)}
h3{font-size:14px;font-weight:600;margin:0 0 8px}
.sub{color:var(--sub);font-size:12.5px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;
      padding:16px 18px;margin-bottom:14px}
.card.warn{background:#FDF6F5;border-color:#F0D6D1}
code{font-family:Consolas,Monaco,monospace;font-size:12px;
     background:#F1F0ED;padding:1px 5px;border-radius:4px}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
th,td{padding:7px 9px;text-align:right;font-size:12.5px}
th{background:#F1F0ED;color:var(--sub);font-weight:500;text-align:right;
   white-space:nowrap}
th.l,td.l{text-align:left}
td{border-top:1px solid var(--line)}
tbody tr:hover{background:#F4F3F0}
.pos{color:var(--pos)} .neg{color:var(--neg)} .dim{color:var(--sub)}
.tablebox{border:1px solid var(--line);border-radius:10px;overflow-x:auto}
details{border:1px solid var(--line);border-radius:10px;margin-bottom:8px;
        background:#fff;overflow:hidden}
details>summary{cursor:pointer;padding:11px 16px;font-weight:600;font-size:13.5px;
  background:var(--panel);list-style:none;display:flex;
  justify-content:space-between;align-items:center;gap:14px}
details>summary::-webkit-details-marker{display:none}
details>summary::before{content:"▸";color:var(--sub);margin-right:8px;
  transition:transform .15s;display:inline-block}
details[open]>summary::before{transform:rotate(90deg)}
details[open]>summary{border-bottom:1px solid var(--line)}
.fam{color:var(--sub);font-size:11.5px;font-weight:400}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(118px,1fr));
      gap:10px;padding:14px 16px 6px}
.kpi{background:var(--panel);border:1px solid var(--line);border-radius:8px;
     padding:9px 11px}
.kpi .k{color:var(--sub);font-size:10.5px;margin-bottom:3px}
.kpi .v{font-size:16px;font-weight:600;font-variant-numeric:tabular-nums}
.charts{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;padding:12px 16px 16px}
.charts>div{border:1px solid var(--line);border-radius:8px;padding:6px;background:#fff}
.hm td{padding:0;border:none;height:23px}
.hm .name{padding:0 9px;text-align:left;font-size:12px;
          white-space:nowrap;border-right:1px solid var(--line)}
.hm .cell{width:132px;text-align:center;font-size:11.5px;
          border-right:1px solid rgba(255,255,255,.55)}
.hm .fam-row td{background:#F1F0ED;font-size:11.5px;color:var(--sub);
                padding:4px 9px;text-align:left;font-weight:600}
.legend{display:flex;gap:16px;align-items:center;font-size:12px;
        color:var(--sub);margin-bottom:10px;flex-wrap:wrap}
.sw{display:inline-block;width:11px;height:11px;border-radius:3px;
    vertical-align:-1px;margin-right:4px}
.toc{display:flex;flex-wrap:wrap;gap:8px;margin:16px 0 6px}
.toc a{background:var(--panel);border:1px solid var(--line);border-radius:20px;
  padding:5px 13px;font-size:12.5px;color:var(--ink);text-decoration:none}
.toc a:hover{border-color:var(--accent);color:var(--accent)}
.funnel{display:flex;flex-direction:column;gap:6px;padding:6px 0}
.fn{display:flex;align-items:center;gap:10px}
.fn .bar{height:30px;border-radius:6px;display:flex;align-items:center;
  padding:0 12px;font-size:12.5px;font-weight:600;color:#fff;white-space:nowrap}
.fn .lbl{font-size:12px;color:var(--sub)}
.grade{display:inline-block;font-size:10.5px;padding:1px 7px;border-radius:4px}
.g-pass{background:#E1F5EE;color:#0F6E56}
.g-solo{background:#FAEEDA;color:#854F0B}
.g-fragile{background:#EEEDFE;color:#534AB7}
.g-flip{background:#FCEBEB;color:#A32D2D}
.cm td{padding:0;border:none}
.cm .ch{padding:5px 9px;text-align:center;font-size:11.5px;color:var(--sub);
        background:#F1F0ED}
.cm .rl{padding:0 10px;text-align:right;font-size:11.5px;color:var(--sub);
        background:#F1F0ED;white-space:nowrap}
.cm .c{width:120px;height:34px;text-align:center;font-size:12.5px;
       font-variant-numeric:tabular-nums}
@media(max-width:1000px){.kpis{grid-template-columns:repeat(3,1fr)}
  .charts{grid-template-columns:1fr}}
"""


def build_html(results, meta_info, families, lo_hi):
    """把所有结果拼成一个静态 HTML 字符串。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    fam_of = {f: fam for fam, fs in families for f in fs}

    # ---- 排序：按 |ICIR| ----
    rows = sorted(results, key=lambda r: -abs(r["icir"] if r["icir"] == r["icir"] else 0))

    # 热力图色阶上限：所有档位均值的 90 分位
    allmeans = [abs(b["mean"]) for r in results for b in r["buckets"] if b["mean"] == b["mean"]]
    vmax = float(np.percentile(allmeans, 90)) if allmeans else 10.0

    H = []
    A = H.append
    A('<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">')
    A('<meta name="viewport" content="width=device-width, initial-scale=1.0">')
    A('<title>基本面因子分析报告</title>')
    A(f"<style>{CSS}</style></head><body><div class='wrap'>")

    # ================= 抬头 =================
    A("<h1>基本面因子分析报告</h1>")
    A(f"<div class='sub'>数据：{_esc(TRAIN_FILE)} · "
      f"{meta_info['n_rows']:,} 行 · {meta_info['n_stocks']:,} 只股票 · "
      f"{meta_info['n_era']} 个季度截面（{meta_info['era_min']} ~ {meta_info['era_max']}）"
      f"<br>生成时间 {now} · 共 {len(results)} 个因子</div>")

    # ================= 目录导航 =================
    n_pass = sum(1 for r in results if r["grade"] == "通过")
    A("<nav class='toc'>")
    for i, t in enumerate(["方法与数据来源", "口径限制", "因子有效性总表",
                           "分档超额收益热力图", "一眼看出来的四件事",
                           f"稳健性检验（{n_pass}/{len(results)} 个通过）",
                           "因子详情"], 1):
        A(f"<a href='#s{i}'>{_CN_NUM[i]}、{t}</a>")
    A("</nav>")

    # ================= 方法与数据来源 =================
    A("<h2 id='s1'>一、方法与数据来源</h2>")
    A("<div class='card'>")
    A("<h3>数据</h3><ul style='margin-left:20px'>")
    A(f"<li>来源：<code>{_esc(TRAIN_FILE)}</code>，原始 {meta_info['n_raw']:,} 行</li>")
    A(f"<li>去重：删掉「除 <code>id</code> 外所有列完全相同」的行，"
      f"共 <b>{meta_info['n_raw']-meta_info['n_rows']}</b> 行"
      f"（<code>ET</code> / <code>PLD</code> 两只股票各 16 组）→ {meta_info['n_rows']:,} 行</li>")
    A(f"<li>截面键：<code>year(period_start) ‖ '-Q' ‖ quarter(period_start)</code>。"
      f"<b>不能直接用 period_start</b> —— 它是各公司自己的财季末，有 381 个不同日期，"
      f"中位数只有 4 只股票；归到季度后是 {meta_info['n_era']} 个截面，每截面 1,200~1,750 只</li>")
    A("</ul><h3 style='margin-top:12px'>标签处理</h3><ul style='margin-left:20px'>")
    A(f"<li>缩尾：<code>return_pct</code> 截断到 1% / 99% 分位，"
      f"边界只用 {FIT_YEARS[0]}-{FIT_YEARS[-1]} 计算（<b>不用 2022，避免泄漏</b>）"
      f"→ {lo_hi[0]:,.2f} ~ {lo_hi[1]:,.2f}</li>")
    A("<li>超额收益：每个截面内减去当期均值。<b>不减会被大盘涨跌主导</b>，"
      "IC 和分组收益甚至会出现符号相反</li>")
    A("</ul><h3 style='margin-top:12px'>因子与分档</h3><ul style='margin-left:20px'>")
    A(f"<li>每个因子：<b>在每个截面内</b>按取值排名，等分成 {N_GROUPS} 档（Q1 最低 → "
      f"Q{N_GROUPS} 最高）</li>")
    A("<li>用<b>排名</b>而不是数值分档 → 对极端值天然免疫"
      "（例如 <code>roe</code> 最大 336 万 %，但排名不受影响）</li>")
    A("<li><b>缺失单独做一组</b>。不填、不删 —— "
      "因为 <code>NTILE</code> 会把缺失当成最大值全塞进最后一档，"
      "实测 <code>roe</code> 的第 5 档 98.5% 是缺失值，结论会完全反过来</li>")
    A("<li><b>若某档根本分不出来</b>（字段存在大量并列值）→ 该档显示「—」，"
      "总表「分档」列会标出实际分出几档。<code>dividends_paid_ttm</code> 就是这种情况："
      "93% 缺失，剩下的一半取值是 0，实际只分得出 4 档</li>")
    A("<li>因子与数据的合并键用 <code>id</code>（每行唯一），"
      "<b>不能用 <code>(era, ticker)</code></b> —— 有些公司在同一个日历季度里有两个财季末"
      "（如 <code>ARLO</code> 的 2022-10-02 与 2022-12-31 都属 2022-Q4），"
      "按它合并会把样本量虚增 332 行</li>")
    A("</ul><h3 style='margin-top:12px'>IC 与统计口径</h3><ul style='margin-left:20px'>")
    A("<li>主口径 <b>Rank IC</b>：每个截面算一次「因子排名」与「超额收益排名」的 "
      "Spearman 相关，再对所有截面取平均</li>")
    A(f"<li>ICIR = IC 均值 ÷ IC 标准差；t 值 = ICIR × √截面数（共 "
      f"{meta_info['n_era']} 个截面）</li>")
    A(f"<li>截面样本数少于 {MIN_CROSS_N} 只股票、或取值少于 5 种时，该截面不计入</li>")
    A("</ul></div>")

    # ================= 限制与提醒 =================
    A("<div class='card warn'>")
    A("<h3 id='s2'>二、口径限制（读数前务必知道）</h3><ul style='margin-left:20px'>")
    A(f"<li><b>只有 {meta_info['n_era']} 个截面</b>（日频数据通常是几百到上千个）"
      f"→ ICIR、t 值、分年度 ICIR 的<b>统计功效很弱</b>，"
      f"只能当方向参考，<b>不要当显著性结论</b></li>")
    A("<li><b>2020 年是个例外年</b>：疫情后「垃圾股暴涨」的年份，"
      "该年多数因子的 IC 会与其它三年反向。分年度 IC 那一栏就是给你看这个的</li>")
    A("<li>持有期固定 <b>1 年</b>，没有多周期。所以本报告<b>没有</b> IC 衰减 / "
      "半衰期这类需要多持有期的指标</li>")
    A("<li><b>本报告只针对训练集（2019-2022）</b>。测试集是 2024 年、"
      "且没有收益标签、<code>ticker</code> 也已匿名 → <b>无法评估</b>。"
      "这里是回测研究结果，不是实盘，也不构成任何投资建议</li>")
    A("</ul></div>")

    A("<div class='card'><h3>附：为什么说这场比赛的榜单区分不出建模能力</h3>")
    A("<div class='sub' style='margin-bottom:8px'>用 2019-2021 训练、2022 验证"
      "（6,634 行），只喂 33 个基本面特征，做了一次完整对照：</div>")
    A("<table style='max-width:620px'><thead><tr>"
      "<th class='l'>做法</th><th>验证集 MSE</th></tr></thead><tbody>")
    for label, mse, tag in [
        ("预测常数 / 训练集均值", "4,276", ""),
        ("Ridge 线性回归", "6,932", "neg"),
        ("梯度提升树", "5,118", "neg"),
        ("梯度提升树 + 截断到 ±50%", "4,181", "pos"),
        ("对照：完美识别公司 + 重构收益误差 ±5 个百分点", "24.9", "pos"),
    ]:
        mark = ("" if tag == "" else
                (" ← 比常数还差 62%" if tag == "neg" and "Ridge" in label else
                 (" ← 最好的模型，只比常数好 2.2%" if tag == "pos" and "±50%" in label else
                  (" ← 三个数量级的差距全在这" if "对照" in label else ""))))
        A(f"<tr><td class='l'>{label}</td>"
          f"<td class='{tag}'><b>{mse}</b>{mark}</td></tr>")
    A("</tbody></table>")
    A("<div class='sub' style='margin-top:8px'>"
      "不加截断时模型<b>比「直接猜一个常数」还差 62%</b> —— 因为它去拟合极端值，"
      "然后在验证集上被反向惩罚。加了截断后，最好的成绩也只是比常数好 <b>2.2%</b>。<br>"
      "而榜一的 MSE 是 2,058、榜六是 4,421 —— <b>榜六基本就是「常数级」水平</b>。"
      "把上面最后一行和它们放在一起看，就知道那三个数量级的差距来自哪里了。</div></div>")

    # ================= 汇总总表 =================
    A("<h2 id='s3'>三、因子有效性总表</h2>")
    A("<div class='legend'>"
      f"<span><i class='sw' style='background:{POS}'></i>正超额收益（红）</span>"
      f"<span><i class='sw' style='background:{NEG}'></i>负超额收益（绿）</span>"
      "<span class='dim'>按 |ICIR| 从大到小排序</span></div>")
    A("<div class='tablebox'><table><thead><tr>")
    heads = ["因子", "家族", "缺失率", "分档", "稳健", "IC均值", "ICIR", "t值", "IC胜率",
             "单调ρ(均)", "单调ρ(中)", f"Q{N_GROUPS}−Q1", "中位差", "缺失组",
             "年度同号", "行业中性IC", "去尾IC", "不缩尾IC"]
    A("".join(f"<th class='l'>{heads[0]}</th>" if i == 0 else
              (f"<th class='l'>{h}</th>" if i == 1 else f"<th>{h}</th>")
              for i, h in enumerate(heads)))
    A("</tr></thead><tbody>")
    for r in rows:
        A("<tr>")
        A(f"<td class='l'><b>{_esc(r['name'])}</b></td>")
        A(f"<td class='l fam'>{_esc(fam_of.get(r['name'], '—'))}</td>")
        A(f"<td class='dim'>{r['miss_rate']*100:.1f}%</td>")
        A(f"<td class='{'dim' if r['n_buckets']==N_GROUPS else 'neg'}' "
          f"title='实际能分出 {r['n_buckets']} 档；档位不足说明该字段有大量并列值'>"
          f"{r['n_buckets']}/{N_GROUPS}</td>")
        A(f"<td><span class='grade g-{_GRADE_CLS[r['grade']]}'>{r['grade']}</span></td>")
        A(f"<td class='{'pos' if r['ic_mean']>0 else 'neg'}'>{_pct(r['ic_mean'],2)}</td>")
        A(f"<td class='{'pos' if r['icir']>0 else 'neg'}'><b>{_nice(r['icir'],2)}</b></td>")
        A(f"<td class='{'pos' if r['t_stat']>0 else 'neg'}'>{_nice(r['t_stat'],2)}</td>")
        A(f"<td>{r['ic_win']*100:.0f}%</td>")
        A(f"<td class='{'pos' if r['mono']>0.5 else 'neg' if r['mono']<-0.5 else 'dim'}'>"
          f"{_nice(r['mono'],2)}</td>")
        A(f"<td class='{'pos' if r['mono_med']>0.5 else 'neg' if r['mono_med']<-0.5 else 'dim'}'>"
          f"{_nice(r['mono_med'],2)}</td>")
        A(f"<td class='{'pos' if r['spread']>0 else 'neg'}'>{_nice(r['spread'],1)}</td>")
        A(f"<td class='{'pos' if r['spread_m']>0 else 'neg'}'>{_nice(r['spread_m'],1)}</td>")
        A(f"<td class='{'pos' if r['buckets'][0]['mean']>0 else 'neg'}'>"
          f"{_nice(r['buckets'][0]['mean'],1)}</td>")
        A(f"<td class='{'pos' if r['sign_consist']>=3 else 'neg'}'>{r['sign_consist']}/4</td>")
        A(f"<td class='{'pos' if r['ic_neutral']>0 else 'neg'}'>{_nice(r['ic_neutral'],3)}</td>")
        A(f"<td class='{'pos' if r['ic_notail']>0 else 'neg'}'>{_nice(r['ic_notail'],3)}</td>")
        A(f"<td class='dim'>{_nice(r['ic_raw'],3)}</td>")
        A("</tr>")
    A("</tbody></table></div>")
    A("<div class='sub' style='margin-top:8px'>"
      "「不缩尾IC」用未缩尾的原始收益算 Pearson IC（数值量级不同，只作对照）；"
      "「去尾IC」剔除收益两端各 1% 的样本后重算；"
      "「行业中性IC」用「截面×行业」内去均值后的收益算。"
      "三者与「IC均值」差得越远，说明该因子的结论越依赖极端值或行业结构。</div>")
    A("<div class='sub' style='margin-top:6px'>"
      "「稳健」列来自第六节的<b>留一年法</b>（故意藏起一整年再算一遍）：<br>"
      "<span class='grade g-pass'>通过</span> 去掉任何一年结论都不变　"
      "<span class='grade g-solo'>靠单年</span> 方向没翻，但信号只来自某一年，"
      "去掉它数值就近乎归零　"
      "<span class='grade g-fragile'>算法敏感</span> 换个口径（行业中性化 / 去掉极端样本）"
      "方向就变　"
      "<span class='grade g-flip'>翻号</span> 去掉某一年后方向直接翻转</div>")

    # ================= 热力图 =================
    A("<h2 id='s4'>四、分档超额收益热力图（全部因子一屏看）</h2>")
    A(f"<div class='sub' style='margin-bottom:8px'>"
      f"行 = 因子，列 = 档位（Q1 最低 → Q{N_GROUPS} 最高）+ 缺失组。"
      f"格子里的数字 = 该档平均超额收益（%）。"
      f"<b>真正有用的因子应该是一行颜色从绿渐变到红（单调）</b>，"
      f"颜色杂乱的就是噪音。色阶上限 ±{vmax:.1f}%</div>")
    A("<table class='hm'>")
    A("<thead><tr><th class='name'>因子</th>" +
      "".join(f"<th class='cell'>{'缺失' if i==-1 else 'Q'+str(i+1)}</th>"
              for i in range(-1, N_GROUPS)) + "</tr></thead><tbody>")
    ordered = []
    for fam, fs in families:
        got = [r for r in rows if r["name"] in fs]
        if got:
            ordered.append((fam, got))
    for fam, got in ordered:
        A(f"<tr class='fam-row'><td colspan='{N_GROUPS+2}'>{_esc(fam)}"
          f"（{len(got)} 个）</td></tr>")
        for r in got:
            A("<tr>")
            A(f"<td class='name'>{_esc(r['name'])}</td>")
            for b in r["buckets"]:
                bg, fg = heat_color(b["mean"], vmax)
                tip = (f"{r['name']} {b['label']} 均值 {_nice(b['mean'])}% / "
                       f"中位 {_nice(b['median'])}%  (n={b['n']})")
                A(f"<td class='cell' style='background:{bg};color:{fg}' "
                  f"title={_q(tip)}>{_nice(b['mean'],1)}</td>")
            A("</tr>")
    A("</tbody></table>")

    # ================= 自动读数（全部由上面的结果统计而来）=================
    N = len(results)
    n_miss_neg = sum(1 for r in results
                     if r["buckets"][0]["mean"] == r["buckets"][0]["mean"]
                     and r["buckets"][0]["mean"] < 0)
    n_q1_pos = sum(1 for r in results
                   if r["buckets"][1]["mean"] == r["buckets"][1]["mean"]
                   and r["buckets"][1]["mean"] > 0)
    n_mono = sum(1 for r in results if r["mono"] == r["mono"] and abs(r["mono"]) >= 0.999)
    n_consist = sum(1 for r in results if r["sign_consist"] == 4)
    # 均值口径 vs 中位数口径，单调方向相反的因子
    conflict = [r["name"] for r in results
                if r["mono"] == r["mono"] and r["mono_med"] == r["mono_med"]
                and r["mono"] != 0 and r["mono_med"] != 0
                and (r["mono"] > 0) != (r["mono_med"] > 0)]
    A("<h2 id='s5'>五、一眼看出来的四件事</h2>")
    A("<div class='card'><ul style='margin-left:20px'>")
    A(f"<li><b>缺失组几乎全为负</b>：{N} 个因子里有 <b>{n_miss_neg}</b> 个的"
      f"「缺失组」超额收益为负。说明<b>「财报数据缺失」本身就是负面信号</b> —— "
      f"这类公司往往经营困难。所以不要图省事直接把缺失样本删掉，那是在丢掉信息</li>")
    A(f"<li><b>Q1（最低档）普遍偏高</b>：<b>{n_q1_pos}/{N}</b> 个因子的 Q1 超额收益为正。"
      f"样本期内「低估值 / 小盘 / 绩差」这一端整体跑赢，主要来自 2020 年"
      f"（详见每个因子的「分年度 IC」图）</li>")
    A(f"<li><b>均值和中位数会打架</b>：<b>{len(conflict)}/{N}</b> 个因子的"
      f"「单调ρ(均)」和「单调ρ(中)」<b>符号相反</b>"
      + (f"（{_esc('、'.join(conflict[:6]))}{'…' if len(conflict) > 6 else ''}）" if conflict else "")
      + "。典型例子 <code>inventory</code>：按均值是从 Q1 到 Q5 递减，"
      "按中位数却是递增 —— 因为最小的那一档里混着几只暴涨股，把均值拉飞了。"
      "<b>这种情况下以中位数口径为准</b></li>")
    A(f"<li><b>真正稳健的因子几乎没有</b>：只有 <b>{n_mono}/{N}</b> 个做到完全单调"
      f"（单调ρ = ±1.00），只有 <b>{n_consist}/{N}</b> 个做到年度方向四年一致。"
      f"单因子在季度频率上的信号本来就很弱</li>")
    A("</ul></div>")

    # ================= 六、稳健性检验 =================
    A("<h2 id='s6'>六、稳健性检验：32 个指标里，哪几个真的能用</h2>")

    A("<div class='card'>")
    A("<h3>判据：留一年法</h3>")
    A("<div class='sub'>一句话：<b>故意藏起一整年，看剩下的三年还算不算得出同样的结论。</b>"
      "每个因子算 5 个口径的 IC —— 全样本 16 个季度，以及去掉 2019 / 2020 / 2021 / 2022 "
      "各一次（各 12 个季度）。</div>")
    A("<ul style='margin-left:20px;margin-top:10px'>")
    A("<li><b>第 1 关｜方向一致</b>：这 5 个数字必须同号。只要有一个翻到另一边，"
      "就说明该因子的结论依赖那一年 → 淘汰</li>")
    A("<li><b>第 2 关｜不靠单一年份</b>：5 个数字里最弱的那个绝对值，"
      "不能低于全样本的一半 —— 防止「5 个都同号，但其中一个是 0.001」</li>")
    A("<li><b>第 3 关｜换个算法还成立</b>：行业中性化后、以及去掉收益两端各 1% 后，"
      "符号仍不变</li>")
    A("</ul>")
    A("<div class='sub' style='margin-top:10px'>"
      "为什么第 2 关不能省？看 <code>eps_basic</code>：全样本 IC +0.062 看着不错，"
      "但去掉 2021 年后只剩 <b>+0.001</b> —— 它的信号其实 100% 来自那一年。"
      "反过来 <code>price_to_sales</code>，全样本 −0.107 看着一般，"
      "去掉 2019 后变成 <b>−0.212</b>，说明 2019 是它的反向异常年、而不是它的信号来源。"
      "<b>只看单年、或只看某个子集，这两种情况会被混在一起分不开。</b></div>")
    A("</div>")

    # ---- 漏斗 ----
    n_dir = sum(1 for r in results if r["loo_same"])
    A("<div class='card'><h3>筛选漏斗</h3><div class='funnel'>")
    for n, label, color in [(len(results), "起点：全部字段", "#B4B2A9"),
                            (n_dir, "第 1 关通过 —— 去掉任何一年，结论都不翻", "#85B7EB"),
                            (n_pass, "第 2、3 关通过 —— 数值也稳，最终名单", "#185FA5")]:
        w = 8 + 78 * (n / max(len(results), 1)) ** 0.6
        A(f"<div class='fn'><div class='bar' style='width:{w:.1f}%;"
          f"background:{color}'>{n} 个</div>"
          f"<div class='lbl'>{label}</div></div>")
    A("</div></div>")

    # ---- 森林图 ----
    def _by_grade(g):
        return sorted([r for r in results if r["grade"] == g],
                      key=lambda r: -abs(r["ic_mean"]))
    forest_groups = [("通过", _by_grade("通过")),
                     ("靠单一年份撑（方向同号，但某个口径几乎为零）",
                      _by_grade("靠单年")),
                     ("算法敏感（中性化或去尾后方向改变）", _by_grade("算法敏感")),
                     ("翻号淘汰（去掉某一年后方向翻转）", _by_grade("翻号"))]
    forest_groups = [(t, g) for t, g in forest_groups if g]
    A("<div class='card'><h3>森林图 · 一屏看完全部因子</h3>")
    A("<div class='sub' style='margin-bottom:6px'>"
      "每行一个因子。大点 = 全样本，4 个小点 = 去掉每一年的结果，竖线在 0。"
      "<b>5 个点全在竖线同一侧才算方向稳</b>；红点表示去掉那一年后翻到了另一边。</div>")
    A(f"<div style='overflow-x:auto'>{svg_forest(forest_groups)}</div></div>")

    # ---- 年度相关性矩阵 ----
    years = sorted({y for r in results for y in r["yearly"]})
    cm = year_corr(results, years)
    v_19_21 = cm.get((2019, 2021), np.nan)
    A("<div class='card'><h3>各年度 IC 之间的相关性（跨全部因子看）</h3>")
    A("<div class='sub' style='margin-bottom:10px'>"
      "如果一个因子的有效性是稳定的，那「它在 2019 年管用」和「它在 2021 年管用」就该一致 "
      "→ 这一格应该接近 <b>+1</b>。<br>"
      f"实测 <b>2019 与 2021 = {v_19_21:+.2f}</b> —— "
      "这两年里「哪几个因子有效」的排序几乎<b>完全颠倒</b>。"
      "这就是为什么不能指望在这份数据上找到长期稳定的因子。</div>")
    A("<table class='cm' style='width:auto'><thead><tr><th></th>"
      + "".join(f"<th class='ch'>{y}</th>" for y in years) + "</tr></thead><tbody>")
    for a in years:
        A(f"<tr><td class='rl'>{a}</td>")
        for b in years:
            v = cm.get((a, b), np.nan)
            if a == b:
                A("<td class='c' style='background:#F1F0ED;color:#7A7A75'>—</td>")
            else:
                bg, fg = heat_color(v, 1.0)
                A(f"<td class='c' style='background:{bg};color:{fg}'>{v:+.2f}</td>")
        A("</tr>")
    A("</tbody></table>")
    A("<div class='sub' style='margin-top:8px'>"
      "读法：<b>绿色 = 两年之间不一致</b>（负相关，颜色越深越不一致）；"
      "<b>红色 = 一致</b>（正相关）。理想情况是这张表除对角线外<b>全是红色</b>。<br>"
      "实际正好相反 —— 除对角线外最深的一格是绿色，"
      "而且<b>没有任何一格是明显的正相关</b>。</div></div>")

    # ---- 留一年法对照表 ----
    order = {"通过": 0, "靠单年": 1, "算法敏感": 2, "翻号": 3}
    A("<div class='card'><h3>留一年法对照表</h3>")
    A("<div class='sub' style='margin-bottom:8px'>"
      "标红的数字 = 与「全样本」方向相反（翻号）。最后一列是 5 个口径里最弱的那个 "
      "占全样本的比例，越低说明越依赖某一年。</div>")
    A("<div class='tablebox'><table><thead><tr><th class='l'>因子</th>"
      + "".join(f"<th>{k}</th>" for k in LOO_KEYS)
      + "<th>最弱/全样本</th><th>判定</th></tr></thead><tbody>")
    for r in sorted(results, key=lambda x: (order[x["grade"]], -abs(x["ic_mean"]))):
        loo = r["loo"] or {}
        sgn = np.sign(r["ic_mean"])
        A("<tr>")
        A(f"<td class='l'><b>{_esc(r['name'])}</b></td>")
        for k in LOO_KEYS:
            v = loo.get(k, np.nan)
            flip = (v == v) and (np.sign(v) != sgn)
            A(f"<td class='{'neg' if flip else ('pos' if v == v and v > 0 else 'neg')}' "
              f"style='{'font-weight:600' if flip else ''}'>{_nice(v, 3)}</td>")
        A(f"<td class='dim'>{r['loo_ratio']*100:.0f}%</td>")
        A(f"<td><span class='grade g-{_GRADE_CLS[r['grade']]}'>{r['grade']}</span></td>")
        A("</tr>")
    A("</tbody></table></div></div>")

    # ---- 结论名单 ----
    A("<div class='card'><h3>结论</h3>")
    passed = sorted([r for r in results if r["grade"] == "通过"],
                    key=lambda r: -abs(r["ic_mean"]))
    A(f"<div class='sub' style='margin-bottom:8px'>"
      f"<b>32 个字段里，只有 {len(passed)} 个经得起留一年法检验。</b>"
      f"其余 {len(results)-len(passed)} 个不能用 —— 有的是靠某一年撑起来的，"
      f"有的是去掉某一年就翻号。</div>")
    A("<table><thead><tr><th class='l'>通过的因子</th><th>IC</th><th>方向</th>"
      "<th>最弱口径/全样本</th><th>缺失率</th><th class='l'>含义</th>"
      "</tr></thead><tbody>")
    MEANING = {
        "price_to_sales": "市销率 → 越贵越差（负向）",
        "roa": "总资产回报率 → 越赚钱越好",
        "growth_pe_ratio": "盈利增速相对估值",
        "dividends_paid_ttm": "已付股利总额",
    }
    for r in passed:
        A(f"<tr><td class='l'><b>{_esc(r['name'])}</b></td>"
          f"<td class='{'pos' if r['ic_mean'] > 0 else 'neg'}'>{_pct(r['ic_mean'], 2)}</td>"
          f"<td>{'正向' if r['ic_mean'] > 0 else '负向'}</td>"
          f"<td>{r['loo_ratio']*100:.0f}%</td>"
          f"<td class='dim'>{r['miss_rate']*100:.1f}%</td>"
          f"<td class='l dim'>{_esc(MEANING.get(r['name'], '—'))}</td></tr>")
    A("</tbody></table>")
    A("<div class='sub' style='margin-top:10px'>"
      "其中 <code>dividends_paid_ttm</code> 要打个折扣：它缺失 <b>93.2%</b>，"
      "而且剩下的样本里一半取值是 0，实际只分得出 4 档 —— "
      "数字上它最稳（最弱口径仍有全样本的 87%），但<b>能用的样本太少，实用性有限</b>。<br>"
      "真正既稳、覆盖又够的是 <code>price_to_sales</code> 和 <code>roa</code>。</div>")
    A("<div class='sub' style='margin-top:14px;margin-bottom:6px'>"
      "<b>淘汰示例</b>（按 |IC| 从大到小，只列前 8 个）：</div>")
    A("<table><thead><tr><th class='l'>因子</th><th>全样本 IC</th>"
      "<th class='l'>为什么不能用</th></tr></thead><tbody>")
    fails = sorted([r for r in results if r["grade"] != "通过"],
                   key=lambda r: -abs(r["ic_mean"]))[:8]
    for r in fails:
        A(f"<tr><td class='l'>{_esc(r['name'])}</td>"
          f"<td class='{'pos' if r['ic_mean'] > 0 else 'neg'}'>{_nice(r['ic_mean'], 4)}</td>"
          f"<td class='l'>{_esc(_fail_reason(r))}</td></tr>")
    A("</tbody></table></div>")

    # ================= 因子详情 =================
    A("<h2 id='s7'>七、因子详情</h2>")
    A("<div class='sub' style='margin-bottom:10px'>点标题展开。"
      "三张图依次是：分档超额收益 / 分年度 IC / 累计 IC。</div>")
    for r in rows:
        A("<details>")
        A("<summary><span>" + _esc(r["name"]) +
          f"<span class='fam'> · {_esc(fam_of.get(r['name'],'—'))}"
          f" · 有效样本 {r['n_used']:,} · 缺失 {r['miss_rate']*100:.1f}%</span></span>"
          f"<span class='sub'>IC {_pct(r['ic_mean'],2)} · ICIR {_nice(r['icir'],2)}"
          f" · 单调 {_nice(r['mono'],2)}</span></summary>")
        A("<div class='kpis'>")
        kpis = [
            ("IC 均值", _pct(r["ic_mean"], 2), r["ic_mean"]),
            ("ICIR", _nice(r["icir"], 2), r["icir"]),
            ("t 值", _nice(r["t_stat"], 2), r["t_stat"]),
            ("IC 胜率", f"{r['ic_win']*100:.0f}%", None),
            ("单调ρ(均)", _nice(r["mono"], 2), r["mono"]),
            ("单调ρ(中)", _nice(r["mono_med"], 2), r["mono_med"]),
            (f"Q{N_GROUPS}−Q1", _nice(r["spread"], 1), r["spread"]),
            ("中位差", _nice(r["spread_m"], 1), r["spread_m"]),
            ("缺失组超额", _nice(r["buckets"][0]["mean"], 1), r["buckets"][0]["mean"]),
            ("年度同号", f"{r['sign_consist']}/4", None),
            ("行业中性 IC", _nice(r["ic_neutral"], 3), r["ic_neutral"]),
            ("去尾 IC", _nice(r["ic_notail"], 3), r["ic_notail"]),
            ("最小档样本", f"{r['min_bucket']:,}", None),
        ]
        for k, v, sgn in kpis:
            cls = "" if sgn is None or sgn != sgn else ("pos" if sgn > 0 else "neg")
            A(f"<div class='kpi'><div class='k'>{k}</div>"
              f"<div class='v {cls}'>{v}</div></div>")
        A("</div><div class='charts'>")
        A(f"<div>{svg_buckets(r['buckets'])}</div>")
        A(f"<div>{svg_yearly(r['yearly'])}</div>")
        A(f"<div>{svg_cumic(r['ic_series'])}</div>")
        A("</div></details>")

    A("<div class='sub' style='margin-top:28px;padding-top:14px;"
      "border-top:1px solid var(--line)'>"
      "本报告由 <code>因子分析.py</code> 自动生成 · 回测研究结论，非实盘策略</div>")
    A("</div></body></html>")
    return "".join(H)


# ==============================================================================
# ⑥ 主流程
# ==============================================================================
def build_factors(df, families):
    """生成因子 SQL：默认「字段名 → 一条 SELECT」，CUSTOM_FACTORS 可覆盖。

    约定：SQL 必须返回 id / value 两列（id 是数据里唯一标识一行的字段）。
    """
    fields = [c for c in df.columns
              if c not in META_COLS and c not in DROP_FEATURES
              and c not in DERIVED_COLS
              and not c.startswith("_") and c != "era"
              and pd.api.types.is_numeric_dtype(df[c])]
    listed = [f for _, fs in families for f in fs if f in fields]
    extra = [f for f in fields if f not in listed]
    sqls = {}
    for f in listed + extra:
        sqls[f] = f"SELECT id, {f} AS value FROM train_clean"
    sqls.update(CUSTOM_FACTORS)
    return sqls


def main():
    print("=" * 74)
    print("  基本面因子分析 —— 单文件版")
    print("=" * 74)

    con = build_db(CSV_DIR)
    df, lo_hi = prepare_frame(con)

    n_raw = con.execute("SELECT COUNT(*) FROM raw").fetchone()[0]
    meta_info = {
        "n_raw": n_raw, "n_rows": len(df),
        "n_stocks": int(df["ticker"].nunique()),
        "n_era": int(df["era"].nunique()),
        "era_min": min(df["era"]), "era_max": max(df["era"]),
    }

    print("\n[因子] 开始计算…")
    sqls = build_factors(df, FAMILIES)
    results = analyze_all(df, sqls)
    print(f"[因子] 完成，共 {len(results)} 个")

    html_str = build_html(results, meta_info, FAMILIES, lo_hi)
    OUT_HTML.write_text(html_str, encoding="utf-8")
    print(f"\n[输出] {OUT_HTML}")
    print(f"       大小 {len(html_str)/1024:.0f} KB · 双击即可打开")
    print("=" * 74)


if __name__ == "__main__":
    main()
