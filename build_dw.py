"""
build_dw.py
-----------
Builds a DuckDB Data Warehouse (olist_warehouse.duckdb) from all CSV files
located in the same directory as this script.

Usage:
    python build_dw.py

Requirements:
    pip install duckdb pandas
"""

import io
import os
import sys
import duckdb
import pandas as pd

# Force UTF-8 output on Windows terminals (avoids cp1256 issues)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── Config ────────────────────────────────────────────────────────────────────
SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
DB_PATH     = os.path.join(SCRIPT_DIR, "olist_warehouse.duckdb")

# ── Helpers ───────────────────────────────────────────────────────────────────
def banner(text: str) -> None:
    width = 52
    print("\n" + "=" * width)
    print(f"  {text}")
    print("=" * width)


def separator(char: str = "-", width: int = 48) -> str:
    return char * width


# ── 1. Discover CSVs ──────────────────────────────────────────────────────────
csv_files = sorted(
    f for f in os.listdir(SCRIPT_DIR)
    if f.lower().endswith(".csv")
)

if not csv_files:
    print("❌  No CSV files found in:", SCRIPT_DIR)
    sys.exit(1)

banner(f"Found {len(csv_files)} CSV file(s) to load")
for f in csv_files:
    size_mb = os.path.getsize(os.path.join(SCRIPT_DIR, f)) / (1024 ** 2)
    print(f"  * {f:<55}  {size_mb:>6.2f} MB")


# ── 2. Connect to DuckDB ──────────────────────────────────────────────────────
banner(f"Connecting to DuckDB → {os.path.basename(DB_PATH)}")
con = duckdb.connect(DB_PATH)


# ── 3. Load every CSV into its own table ─────────────────────────────────────
banner("Loading tables …")

loaded: list[tuple[str, int]] = []

for filename in csv_files:
    table_name = os.path.splitext(filename)[0]          # strip .csv
    csv_path   = os.path.join(SCRIPT_DIR, filename).replace("\\", "/")

    # Drop & recreate so re-runs are idempotent
    con.execute(f'DROP TABLE IF EXISTS "{table_name}"')

    # Pass 1 – fast path: let DuckDB infer column types
    try:
        con.execute(f"""
            CREATE TABLE "{table_name}" AS
            SELECT * FROM read_csv_auto(
                '{csv_path}',
                header=True,
                all_varchar=False,
                sample_size=-1
            )
        """)
    except Exception:
        # Pass 2 – safe fallback: load everything as VARCHAR (no data loss)
        con.execute(f'DROP TABLE IF EXISTS "{table_name}"')
        con.execute(f"""
            CREATE TABLE "{table_name}" AS
            SELECT * FROM read_csv_auto(
                '{csv_path}',
                header=True,
                all_varchar=True
            )
        """)

    row_count = con.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0]
    loaded.append((table_name, row_count))
    print(f"  [OK] {table_name:<48}  {row_count:>10,} rows")


# ── 4. Summary table ──────────────────────────────────────────────────────────
banner("Data Warehouse Summary")

col_w = 40
num_w = 12

header = f"{'Table':<{col_w}}{'Rows':>{num_w}}"
print(header)
print(separator())

for table_name, row_count in loaded:
    print(f"{table_name:<{col_w}}{row_count:>{num_w},}")

print(separator())
print(f"\n  DW saved to: {DB_PATH}")
print()


# ── 5. JOIN verification test ─────────────────────────────────────────────────
banner("JOIN Verification Test")

# Only run the JOIN if the expected star-schema tables exist
star_tables = {"Fact_Sales", "Dim_Customer", "Dim_Product", "Dim_Seller", "Dim_Date"}
available   = {t for t, _ in loaded}

if star_tables.issubset(available):
    print("  Running star-schema JOIN (Fact_Sales --> all Dims) ...\n")

    result = con.execute("""
        SELECT
            fs.order_id,
            dc.customer_city,
            dp.product_category_name,
            ds.seller_city,
            dd.Year,
            dd.Month,
            fs.price,
            fs.freight_value
        FROM  "Fact_Sales"    fs
        JOIN  "Dim_Customer"  dc ON fs.customer_id               = dc.customer_id
        JOIN  "Dim_Product"   dp ON fs.product_id                = dp.product_id
        JOIN  "Dim_Seller"    ds ON fs.seller_id                 = ds.seller_id
        JOIN  "Dim_Date"      dd ON CAST(fs.order_purchase_timestamp AS DATE) = CAST(dd.Date AS DATE)
        LIMIT 5
    """).df()

    print(result.to_string(index=False))
    print(f"\n  [OK] JOIN returned {len(result)} sample row(s) -- relationships intact.")

else:
    missing = star_tables - available
    print(f"  [WARN] Skipping full star-schema JOIN -- missing tables: {missing}")
    print("     Running a simple sanity JOIN on available olist tables …\n")

    # Fallback: join orders → order_items (always present)
    fallback_tables = {
        "olist_orders_dataset",
        "olist_order_items_dataset",
    }
    if fallback_tables.issubset(available):
        result = con.execute("""
            SELECT
                o.order_id,
                o.customer_id,
                o.order_status,
                oi.product_id,
                oi.price
            FROM  "olist_orders_dataset"      o
            JOIN  "olist_order_items_dataset" oi USING (order_id)
            LIMIT 5
        """).df()
        print(result.to_string(index=False))
        print(f"\n  [OK] Fallback JOIN returned {len(result)} row(s) -- relationships intact.")
    else:
        print("  [WARN] No suitable tables for JOIN verification.")


# ── 6. List all tables stored in DuckDB ──────────────────────────────────────
banner("Tables persisted in olist_warehouse.duckdb")
all_tables = con.execute("SHOW TABLES").fetchall()
for (t,) in all_tables:
    print(f"  * {t}")

con.close()
print("\n[DONE]\n")
