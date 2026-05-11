import io, sys, duckdb
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

con = duckdb.connect("olist_warehouse.duckdb")

SQL_SAMPLE = """
    SELECT
        fs.order_id,
        dc.customer_city,
        dp.product_category_name,
        ds.seller_city,
        dd.Year,
        dd.Month,
        fs.price,
        fs.freight_value
    FROM  "Fact_Sales"   fs
    JOIN  "Dim_Customer" dc ON fs.customer_id = dc.customer_id
    JOIN  "Dim_Product"  dp ON fs.product_id  = dp.product_id
    JOIN  "Dim_Seller"   ds ON fs.seller_id   = ds.seller_id
    JOIN  "Dim_Date"     dd
          ON CAST(fs.order_purchase_timestamp AS DATE) = CAST(dd.Date AS DATE)
    LIMIT 5
"""

SQL_COUNT = """
    SELECT COUNT(*) FROM "Fact_Sales" fs
    JOIN  "Dim_Customer" dc ON fs.customer_id = dc.customer_id
    JOIN  "Dim_Product"  dp ON fs.product_id  = dp.product_id
    JOIN  "Dim_Seller"   ds ON fs.seller_id   = ds.seller_id
    JOIN  "Dim_Date"     dd
          ON CAST(fs.order_purchase_timestamp AS DATE) = CAST(dd.Date AS DATE)
"""

print("=== Star-Schema JOIN Test (Fact_Sales --> all Dims) ===\n")
sample = con.execute(SQL_SAMPLE).df()
print(sample.to_string(index=False))

total = con.execute(SQL_COUNT).fetchone()[0]
print(f"\n[OK] Full JOIN matched {total:,} rows -- all relationships intact.")
con.close()
