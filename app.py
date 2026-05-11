"""
app.py — Olist Customer Churn Prediction Dashboard
Run: streamlit run app.py
"""

import numpy as np
import pandas as pd
import duckdb
import plotly.graph_objects as go
import streamlit as st
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import train_test_split
from sklearn.metrics import (
    classification_report, roc_auc_score,
    confusion_matrix, roc_curve,
)

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Olist Churn Prediction",
    page_icon="🔄",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Custom CSS ─────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&display=swap');

html, body, [class*="css"] { font-family: 'Inter', sans-serif; }

.stApp { background: #0f1117; }

[data-testid="metric-container"] {
    background: #1e2130;
    border: 1px solid #2e3250;
    border-radius: 12px;
    padding: 16px !important;
}
[data-testid="stMetricDelta"] svg { display: none; }

.section-title {
    font-size: 12px;
    font-weight: 700;
    color: #7c8db0;
    text-transform: uppercase;
    letter-spacing: 1.2px;
    margin-bottom: 6px;
}

.hero-card {
    background: linear-gradient(135deg, #1a1f3a 0%, #0d1022 100%);
    border: 1px solid #4C8EDA;
    border-radius: 16px;
    padding: 32px 24px;
    text-align: center;
}
.hero-value {
    font-size: 52px;
    font-weight: 800;
    color: #4C8EDA;
    line-height: 1.1;
}
.hero-label {
    font-size: 13px;
    color: #7c8db0;
    text-transform: uppercase;
    letter-spacing: 1px;
    margin-bottom: 8px;
}

[data-testid="stSidebar"] { background: #161925; }
</style>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# DATA LAYER
# ══════════════════════════════════════════════════════════════════════════════

DB_PATH = "olist_warehouse.duckdb"


@st.cache_resource
def get_connection():
    return duckdb.connect(DB_PATH, read_only=False)


@st.cache_data(ttl=600)
def load_churn_data(threshold: int = 180):
    con = get_connection()
    df = con.execute("""
        SELECT
            f.customer_id,
            COUNT(DISTINCT f.order_id)                         AS total_orders,
            SUM(f.price)                                       AS total_spent,
            AVG(f.price)                                       AS avg_order_value,
            AVG(f.freight_value / NULLIF(f.price, 0))          AS avg_freight_ratio,
            AVG(CAST(f.review_score AS DOUBLE))                AS avg_review_score,
            AVG(CAST(f."Delivery Days" AS DOUBLE))             AS avg_delivery_days,
            COUNT(DISTINCT f.product_category_name)            AS unique_categories,
            COUNT(DISTINCT f.seller_id)                        AS unique_sellers,
            MIN(CAST(f.order_purchase_timestamp AS DATE))      AS first_order_date,
            MAX(CAST(f.order_purchase_timestamp AS DATE))      AS last_order_date,
            DATEDIFF('day',
                MIN(CAST(f.order_purchase_timestamp AS DATE)),
                MAX(CAST(f.order_purchase_timestamp AS DATE))) AS customer_lifespan_days,
            DATEDIFF('day',
                MAX(CAST(f.order_purchase_timestamp AS DATE)),
                (SELECT MAX(CAST(order_purchase_timestamp AS DATE))
                 FROM "Fact_Sales"))                           AS days_since_last_order,
            c.customer_state
        FROM "Fact_Sales" f
        LEFT JOIN "Dim_Customer" c ON f.customer_id = c.customer_id
        GROUP BY f.customer_id, c.customer_state
    """).df()

    # Target: churned = no order in last `threshold` days
    df["churned"] = (df["days_since_last_order"] > threshold).astype(int)

    # Encode top 5 states
    top_states = df["customer_state"].value_counts().head(5).index
    df["customer_state_enc"] = df["customer_state"].apply(
        lambda x: x if x in top_states else "Other"
    )
    df = pd.get_dummies(df, columns=["customer_state_enc"], drop_first=True)
    df = df.drop(columns=["customer_id", "customer_state",
                           "first_order_date", "last_order_date"])

    # Median imputation for any remaining NaNs
    df = df.fillna(df.median(numeric_only=True))
    return df


@st.cache_data(ttl=600)
def train_churn_model(_df):
    # Remove target AND the direct source of the label (leakage prevention)
    X = _df.drop(columns=["churned", "days_since_last_order"])
    y = _df["churned"]

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )

    ch_models = {
        "GBM": GradientBoostingClassifier(
            n_estimators=200, max_depth=3, learning_rate=0.05, random_state=42),
        "Random Forest": RandomForestClassifier(
            n_estimators=200, class_weight="balanced", random_state=42),
        "Logistic Regression": Pipeline([
            ("scaler", StandardScaler()),
            ("model", LogisticRegression(max_iter=500, class_weight="balanced")),
        ]),
    }

    results, preds, probas = {}, {}, {}
    for name, mdl in ch_models.items():
        mdl.fit(X_train, y_train)
        yp  = mdl.predict(X_test)
        ypr = mdl.predict_proba(X_test)[:, 1]
        rep = classification_report(y_test, yp, output_dict=True)
        results[name] = {
            "Accuracy":  round((yp == y_test).mean() * 100, 1),
            "AUC-ROC":   round(roc_auc_score(y_test, ypr), 3),
            "Precision": round(rep["1"]["precision"], 3),
            "Recall":    round(rep["1"]["recall"], 3),
        }
        preds[name], probas[name] = yp, ypr

    best_name = max(results, key=lambda k: results[k]["AUC-ROC"])
    return (ch_models[best_name], best_name, results,
            preds, probas, X_train, X_test, y_train, y_test)


# ══════════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ══════════════════════════════════════════════════════════════════════════════

churn_threshold = 180

with st.sidebar:
    st.markdown("## 🔄 Olist Churn")
    st.markdown("---")
    st.markdown("### ⚙️ Settings")

    churn_threshold = st.slider(
        "Churn Definition (days inactive)",
        min_value=90, max_value=365, value=180, step=30,
        help="Customer is 'churned' if no order within this many days",
    )
    st.caption(f"Currently: no order in >{churn_threshold} days = churned")
    st.divider()

    st.markdown("### 📊 DW Tables")
    try:
        con    = get_connection()
        tables = con.execute("SHOW TABLES").df()
        for t in tables["name"]:
            cnt = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
            st.caption(f"• {t}: {cnt:,} rows")
    except Exception as e:
        st.error(f"DW error: {e}")

    st.divider()
    if st.button("🔄 Re-train Model", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    st.caption("Connected to: olist_warehouse.duckdb")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN PAGE
# ══════════════════════════════════════════════════════════════════════════════

# ── Header ────────────────────────────────────────────────────────────────────
col_h1, col_h2 = st.columns([3, 1])
with col_h1:
    st.title("🔄 Customer Churn Prediction")
    st.caption("Binary classification on DuckDB Data Warehouse — will this customer return?")
with col_h2:
    st.success("✅ DW Connected")
    st.caption(f"Last updated: {pd.Timestamp.now().strftime('%d %b %Y')}")

st.divider()

# ── Load & Train ──────────────────────────────────────────────────────────────
with st.spinner("🔄 Loading customer data from DW..."):
    df = load_churn_data(churn_threshold)

churn_rate  = df["churned"].mean() * 100
total_c     = len(df)
churned_c   = int(df["churned"].sum())
retained_c  = total_c - churned_c

with st.spinner("🤖 Training GBM · Random Forest · Logistic Regression..."):
    (best_mdl, best_name, results, preds, probas,
     X_train, X_test, y_train, y_test) = train_churn_model(df)

# ── KPI Row ───────────────────────────────────────────────────────────────────
k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("👥 Total Customers", f"{total_c:,}")
k2.metric("❌ Churned",         f"{churned_c:,}",
          delta=f"-{churn_rate:.1f}%", delta_color="inverse")
k3.metric("✅ Retained",        f"{retained_c:,}",
          delta=f"+{100 - churn_rate:.1f}%")
k4.metric("🏆 Best Model",      best_name)
k5.metric("📈 AUC-ROC",         f"{results[best_name]['AUC-ROC']:.3f}")

st.divider()

# ── Section · ROC + Confusion Matrix ─────────────────────────────────────────
st.markdown('<p class="section-title">Model Evaluation</p>', unsafe_allow_html=True)
c1, c2 = st.columns(2)

with c1:
    st.markdown("**ROC Curve — All Models**")
    colors = {"GBM": "#4C8EDA", "Random Forest": "#2ecc71",
              "Logistic Regression": "#FF6B35"}
    fig_roc = go.Figure()
    for name in results:
        fpr, tpr, _ = roc_curve(y_test, probas[name])
        fig_roc.add_trace(go.Scatter(
            x=fpr, y=tpr,
            name=f"{name} (AUC={results[name]['AUC-ROC']})",
            line=dict(color=colors[name], width=2),
        ))
    fig_roc.add_shape(type="line", x0=0, y0=0, x1=1, y1=1,
                      line=dict(dash="dash", color="#7c8db0"))
    fig_roc.update_layout(
        template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)", height=360,
        legend=dict(orientation="h", y=-0.28),
        margin=dict(l=0, r=0, t=10, b=0),
        xaxis_title="False Positive Rate", yaxis_title="True Positive Rate",
    )
    st.plotly_chart(fig_roc, use_container_width=True)

with c2:
    st.markdown(f"**Confusion Matrix — {best_name}**")
    import plotly.figure_factory as ff
    cm = confusion_matrix(y_test, preds[best_name])
    fig_cm = ff.create_annotated_heatmap(
        z=cm, x=["Pred: Stay", "Pred: Churn"],
        y=["Actual: Stay", "Actual: Churn"],
        colorscale=[[0, "#1e2130"], [1, "#4C8EDA"]], showscale=False,
    )
    fig_cm.update_layout(
        template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
        height=360, margin=dict(l=0, r=0, t=10, b=0),
    )
    st.plotly_chart(fig_cm, use_container_width=True)

st.divider()

# ── Section · Feature Importance + Model Comparison ───────────────────────────
st.markdown('<p class="section-title">Churn Drivers & Model Comparison</p>',
            unsafe_allow_html=True)
c3, c4 = st.columns([6, 4])

with c3:
    st.markdown(f"**Top 10 Churn Drivers — {best_name}**")
    estimator = (best_mdl.named_steps["model"]
                 if hasattr(best_mdl, "named_steps") else best_mdl)
    imp_vals = (estimator.feature_importances_
                if hasattr(estimator, "feature_importances_")
                else np.abs(estimator.coef_[0]))
    importances = (pd.Series(imp_vals, index=X_train.columns)
                   .sort_values(ascending=False).head(10))
    fig_imp = go.Figure(go.Bar(
        x=importances.values[::-1], y=importances.index[::-1],
        orientation="h",
        marker=dict(color=list(range(len(importances))),
                    colorscale=[[0, "#2ecc71"], [1, "#4C8EDA"]],
                    showscale=False),
    ))
    fig_imp.update_layout(
        template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)", height=350,
        margin=dict(l=0, r=10, t=10, b=0), xaxis_title="Importance",
    )
    st.plotly_chart(fig_imp, use_container_width=True)

with c4:
    st.markdown("**Model Comparison**")
    res_df = pd.DataFrame(results).T.reset_index()
    res_df.columns = ["Model", "Accuracy %", "AUC-ROC", "Precision", "Recall"]
    st.dataframe(res_df, hide_index=True, use_container_width=True,
                 column_config={
                     "Accuracy %": st.column_config.ProgressColumn(
                         min_value=0, max_value=100, format="%.1f%%"),
                     "AUC-ROC": st.column_config.ProgressColumn(
                         min_value=0, max_value=1, format="%.3f"),
                 })
    st.success(
        f"🏆 Best: **{best_name}**  \n"
        f"AUC {results[best_name]['AUC-ROC']} · "
        f"Precision {results[best_name]['Precision']} · "
        f"Recall {results[best_name]['Recall']}"
    )

st.divider()

# ── Section · Churn Risk Distribution ────────────────────────────────────────
st.markdown('<p class="section-title">Churn Risk Distribution</p>',
            unsafe_allow_html=True)

dist_left, dist_right = st.columns([3, 2])

with dist_left:
    proba_s = pd.Series(probas[best_name])
    low    = int((proba_s < 0.33).sum())
    medium = int(((proba_s >= 0.33) & (proba_s < 0.66)).sum())
    high   = int((proba_s >= 0.66).sum())

    fig_dist = go.Figure(go.Bar(
        x=["🟢 Low Risk\n(<33%)", "🟡 Medium Risk\n(33–66%)",
           "🔴 High Risk\n(>66%)"],
        y=[low, medium, high],
        marker_color=["#2ecc71", "#f39c12", "#e74c3c"],
        text=[f"{low:,}", f"{medium:,}", f"{high:,}"],
        textposition="outside",
    ))
    fig_dist.update_layout(
        template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)", height=300, showlegend=False,
        margin=dict(l=0, r=0, t=10, b=0), yaxis_title="Customers",
    )
    st.plotly_chart(fig_dist, use_container_width=True)

with dist_right:
    high_pct  = high  / len(proba_s) * 100
    low_pct   = low   / len(proba_s) * 100
    st.markdown(f"""
    <div class="hero-card">
        <div class="hero-label">High-Risk Customers</div>
        <div class="hero-value">{high:,}</div>
        <div class="hero-label">{high_pct:.1f}% of test set</div>
        <br>
        <div class="hero-label">Safe Customers</div>
        <div style="font-size:28px;font-weight:700;color:#2ecc71">{low:,}</div>
        <div class="hero-label">{low_pct:.1f}% of test set</div>
    </div>
    """, unsafe_allow_html=True)

st.caption(
    f"Churn threshold: >{churn_threshold} days inactive · "
    "Data sourced from olist_warehouse.duckdb"
)
