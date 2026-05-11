import duckdb
con = duckdb.connect("olist_warehouse.duckdb")
for t in ["Dim_Customer", "Dim_Product", "Dim_Seller", "Dim_Date", "Fact_Sales"]:
    print(f"\n=== {t} ===")
    print(con.execute(f'PRAGMA table_info("{t}")').df()[["name","type"]].to_string())
con.close()
