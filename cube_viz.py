"""
cube_viz.py  —  Generate a 3D interactive OLAP Data Cube from olist_warehouse.duckdb
Run:  python cube_viz.py
Opens: cube.html in your default browser
"""

import json, webbrowser, os
import duckdb

con = duckdb.connect("olist_warehouse.duckdb", read_only=True)

# ── Pull real data ────────────────────────────────────────────────────────────
# Axes: Top-5 categories  x  Quarter  x  Top-5 states
cats = con.execute("""
    SELECT product_category_name AS cat, SUM(price) AS rev
    FROM "Fact_Sales"
    WHERE product_category_name IS NOT NULL
    GROUP BY 1 ORDER BY 2 DESC LIMIT 5
""").df()

quarters = con.execute("""
    SELECT DISTINCT
        CAST(DATE_PART('year',  CAST(order_purchase_timestamp AS DATE)) AS VARCHAR) ||
        '-Q' ||
        CAST(DATE_PART('quarter', CAST(order_purchase_timestamp AS DATE)) AS VARCHAR) AS qtr,
        MIN(CAST(order_purchase_timestamp AS DATE)) AS sort_date
    FROM "Fact_Sales"
    GROUP BY 1 ORDER BY 2
""").df()

states = con.execute("""
    SELECT c.customer_state AS state, SUM(f.price) AS rev
    FROM "Fact_Sales" f
    JOIN "Dim_Customer" c ON f.customer_id = c.customer_id
    GROUP BY 1 ORDER BY 2 DESC LIMIT 5
""").df()

# Summary KPIs
kpis = con.execute("""
    SELECT
        COUNT(DISTINCT order_id)   AS orders,
        ROUND(SUM(price),0)        AS revenue,
        COUNT(DISTINCT customer_id)AS customers,
        COUNT(DISTINCT product_id) AS products,
        COUNT(DISTINCT seller_id)  AS sellers
    FROM "Fact_Sales"
""").fetchone()

# Full cube data: (category, quarter, state) → revenue
cube_raw = con.execute("""
    SELECT
        f.product_category_name                  AS cat,
        CAST(DATE_PART('year',  CAST(f.order_purchase_timestamp AS DATE)) AS VARCHAR) ||
        '-Q' ||
        CAST(DATE_PART('quarter', CAST(f.order_purchase_timestamp AS DATE)) AS VARCHAR) AS qtr,
        c.customer_state                          AS state,
        ROUND(SUM(f.price), 0)                   AS revenue,
        COUNT(DISTINCT f.order_id)               AS orders
    FROM "Fact_Sales" f
    JOIN "Dim_Customer" c ON f.customer_id = c.customer_id
    WHERE f.product_category_name IN ({cats})
      AND c.customer_state IN ({states})
    GROUP BY 1,2,3
""".format(
    cats=",".join(f"'{c}'" for c in cats["cat"]),
    states=",".join(f"'{s}'" for s in states["state"])
)).df()

cat_list = cats["cat"].tolist()
qtr_list = quarters["qtr"].tolist()
state_list = states["state"].tolist()

# Build lookup
cube_lookup = {}
for _, row in cube_raw.iterrows():
    k = (row["cat"], row["qtr"], row["state"])
    cube_lookup[k] = {"revenue": float(row["revenue"]), "orders": int(row["orders"])}

max_rev = max((v["revenue"] for v in cube_lookup.values()), default=1)

# Serialize to JS array: [{x,y,z,revenue,orders,cat,qtr,state}, ...]
cells = []
for xi, cat in enumerate(cat_list):
    for yi, qtr in enumerate(qtr_list):
        for zi, state in enumerate(state_list):
            v = cube_lookup.get((cat, qtr, state), {"revenue": 0, "orders": 0})
            cells.append({
                "x": xi, "y": yi, "z": zi,
                "revenue": v["revenue"], "orders": v["orders"],
                "cat": cat, "qtr": qtr, "state": state,
                "norm": round(v["revenue"] / max_rev, 4)
            })

con.close()

# ── HTML ──────────────────────────────────────────────────────────────────────
HTML = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Olist OLAP Data Cube</title>
<style>
  *{{margin:0;padding:0;box-sizing:border-box;}}
  body{{background:#0a0d14;font-family:'Segoe UI',sans-serif;color:#e0e6f0;overflow:hidden;}}
  #canvas{{display:block;}}

  /* HUD */
  #hud{{position:fixed;top:20px;left:20px;z-index:10;}}
  #hud h1{{font-size:22px;font-weight:800;background:linear-gradient(90deg,#4C8EDA,#2ecc71);
           -webkit-background-clip:text;-webkit-text-fill-color:transparent;}}
  #hud p{{font-size:12px;color:#7c8db0;margin-top:4px;}}

  /* KPI strip */
  #kpis{{position:fixed;top:20px;right:20px;z-index:10;display:flex;gap:12px;}}
  .kpi{{background:rgba(30,33,48,0.85);border:1px solid #2e3250;border-radius:12px;
        padding:14px 18px;text-align:center;backdrop-filter:blur(8px);}}
  .kpi .val{{font-size:20px;font-weight:800;color:#4C8EDA;}}
  .kpi .lbl{{font-size:10px;color:#7c8db0;text-transform:uppercase;letter-spacing:.8px;margin-top:2px;}}

  /* Tooltip */
  #tip{{position:fixed;display:none;background:rgba(14,18,30,0.95);
        border:1px solid #4C8EDA;border-radius:10px;padding:12px 16px;
        font-size:13px;pointer-events:none;z-index:20;max-width:220px;}}
  #tip .t-title{{color:#4C8EDA;font-weight:700;margin-bottom:6px;font-size:14px;}}
  #tip .t-row{{display:flex;justify-content:space-between;gap:16px;color:#b0bcd0;margin:3px 0;}}
  #tip .t-val{{color:#fff;font-weight:600;}}

  /* Legend */
  #legend{{position:fixed;bottom:24px;left:20px;z-index:10;
           background:rgba(20,23,35,0.9);border:1px solid #2e3250;
           border-radius:10px;padding:14px 18px;}}
  #legend h4{{font-size:11px;color:#7c8db0;text-transform:uppercase;margin-bottom:8px;}}
  .leg-row{{display:flex;align-items:center;gap:8px;font-size:12px;color:#a0b0c0;margin:4px 0;}}
  .leg-dot{{width:12px;height:12px;border-radius:3px;}}

  /* Controls hint */
  #hint{{position:fixed;bottom:24px;right:20px;z-index:10;font-size:11px;color:#4a5468;
         text-align:right;line-height:1.8;}}

  /* Axis labels canvas */
  #labels{{position:fixed;top:0;left:0;pointer-events:none;z-index:5;}}
</style>
</head>
<body>
<canvas id="canvas"></canvas>
<canvas id="labels"></canvas>

<!-- HUD -->
<div id="hud">
  <h1>📦 Olist OLAP Data Cube</h1>
  <p>Category × Quarter × State &nbsp;·&nbsp; Hover a cell to inspect</p>
</div>

<!-- KPIs -->
<div id="kpis">
  <div class="kpi"><div class="val">R$ {kpis[1]/1e6:.1f}M</div><div class="lbl">Total Revenue</div></div>
  <div class="kpi"><div class="val">{kpis[0]:,}</div><div class="lbl">Orders</div></div>
  <div class="kpi"><div class="val">{kpis[2]:,}</div><div class="lbl">Customers</div></div>
  <div class="kpi"><div class="val">{kpis[3]:,}</div><div class="lbl">Products</div></div>
  <div class="kpi"><div class="val">{kpis[4]:,}</div><div class="lbl">Sellers</div></div>
</div>

<!-- Tooltip -->
<div id="tip">
  <div class="t-title" id="t-title"></div>
  <div class="t-row"><span>Revenue</span><span class="t-val" id="t-rev"></span></div>
  <div class="t-row"><span>Orders</span><span class="t-val" id="t-ord"></span></div>
  <div class="t-row"><span>Quarter</span><span class="t-val" id="t-qtr"></span></div>
  <div class="t-row"><span>State</span><span class="t-val" id="t-st"></span></div>
</div>

<!-- Legend -->
<div id="legend">
  <h4>Revenue Intensity</h4>
  <div class="leg-row"><div class="leg-dot" style="background:#0d3b6e"></div>Low</div>
  <div class="leg-row"><div class="leg-dot" style="background:#1a6fb5"></div>Medium</div>
  <div class="leg-row"><div class="leg-dot" style="background:#4C8EDA"></div>High</div>
  <div class="leg-row"><div class="leg-dot" style="background:#2ecc71"></div>Top</div>
  <br>
  <div class="leg-row" style="margin-top:6px;"><span style="font-size:11px;color:#4a5468;">X → Category &nbsp; Y → Quarter &nbsp; Z → State</span></div>
</div>

<div id="hint">🖱️ Drag to rotate &nbsp;|&nbsp; Scroll to zoom<br>Hover cube to inspect data</div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r134/three.min.js"></script>
<script>
// ── Data ─────────────────────────────────────────────────────────────────────
const CELLS   = {json.dumps(cells)};
const CATS    = {json.dumps(cat_list)};
const QTRS    = {json.dumps(qtr_list)};
const STATES  = {json.dumps(state_list)};
const NX = CATS.length, NY = QTRS.length, NZ = STATES.length;

// ── Scene setup ───────────────────────────────────────────────────────────────
const renderer = new THREE.WebGLRenderer({{canvas: document.getElementById('canvas'), antialias:true}});
renderer.setPixelRatio(window.devicePixelRatio);
renderer.setSize(window.innerWidth, window.innerHeight);
renderer.setClearColor(0x0a0d14);

const scene  = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(45, window.innerWidth/window.innerHeight, 0.1, 200);
camera.position.set(12, 10, 16);
camera.lookAt(0, 0, 0);

// Lighting
scene.add(new THREE.AmbientLight(0x334466, 1.2));
const dir = new THREE.DirectionalLight(0xffffff, 0.8);
dir.position.set(10, 20, 10);
scene.add(dir);

// ── Color mapping ─────────────────────────────────────────────────────────────
function normToColor(n) {{
  if (n < 0.0001) return new THREE.Color(0x111827);  // empty = very dark
  if (n < 0.25)   return new THREE.Color(0x0d3b6e);
  if (n < 0.50)   return new THREE.Color(0x1a6fb5);
  if (n < 0.75)   return new THREE.Color(0x4C8EDA);
  return new THREE.Color(0x2ecc71);
}}

// ── Build instanced cubes ─────────────────────────────────────────────────────
const GAP   = 1.6;
const SIZE  = 1.0;
const geo   = new THREE.BoxGeometry(SIZE, SIZE, SIZE);
const mat   = new THREE.MeshPhongMaterial({{vertexColors: true, transparent:true, opacity:0.92}});

// Compute center offset
const cx = (NX - 1) * GAP / 2;
const cy = (NY - 1) * GAP / 2;
const cz = (NZ - 1) * GAP / 2;

const meshes = [];
const dummy  = new THREE.Object3D();

CELLS.forEach(cell => {{
  const col = normToColor(cell.norm);
  const g   = geo.clone();
  // Per-vertex color
  const colors = [];
  for (let i=0; i<g.attributes.position.count; i++) colors.push(col.r, col.g, col.b);
  g.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));

  const m = new THREE.Mesh(g, mat.clone());
  m.position.set(
    cell.x * GAP - cx,
    cell.y * GAP - cy,
    cell.z * GAP - cz
  );
  m.userData = cell;
  scene.add(m);
  meshes.push(m);
}});

// Wireframe bounding box
const boxGeo = new THREE.BoxGeometry(
  NX*GAP, NY*GAP, NZ*GAP
);
const edges = new THREE.EdgesGeometry(boxGeo);
const lineMat = new THREE.LineBasicMaterial({{color:0x2e3250, opacity:0.5, transparent:true}});
scene.add(new THREE.LineSegments(edges, lineMat));

// Axes lines
function axisLine(start, end, color) {{
  const g = new THREE.BufferGeometry().setFromPoints([
    new THREE.Vector3(...start), new THREE.Vector3(...end)
  ]);
  return new THREE.Line(g, new THREE.LineBasicMaterial({{color}}));
}}
const hw = NX*GAP/2, hh = NY*GAP/2, hd = NZ*GAP/2;
scene.add(axisLine([-hw-1,-hh-1,-hd-1],[hw+2,-hh-1,-hd-1], 0x4C8EDA)); // X - Category
scene.add(axisLine([-hw-1,-hh-1,-hd-1],[-hw-1,hh+2,-hd-1], 0x2ecc71)); // Y - Quarter
scene.add(axisLine([-hw-1,-hh-1,-hd-1],[-hw-1,-hh-1,hd+2], 0xFF6B35)); // Z - State

// ── Orbit controls (manual) ───────────────────────────────────────────────────
let isDragging=false, prevX=0, prevY=0;
let rotX=0.4, rotY=0.5;
let autoSpin=true;
const pivot = new THREE.Group();
scene.add(pivot);
// Move all meshes under pivot
meshes.forEach(m => {{ scene.remove(m); pivot.add(m); }});
scene.remove(edges);
pivot.add(edges);
scene.remove(dir);
pivot.add(dir);

document.addEventListener('mousedown', e=>{{ isDragging=true; prevX=e.clientX; prevY=e.clientY; autoSpin=false; }});
document.addEventListener('mouseup',   ()=>{{ isDragging=false; }});
document.addEventListener('mousemove', e=>{{
  if(isDragging){{
    rotY += (e.clientX - prevX) * 0.008;
    rotX += (e.clientY - prevY) * 0.008;
    prevX=e.clientX; prevY=e.clientY;
  }}
  handleHover(e);
}});
document.addEventListener('wheel', e=>{{
  camera.position.multiplyScalar(1 + e.deltaY * 0.001);
}});

// ── Raycaster / Hover ─────────────────────────────────────────────────────────
const raycaster = new THREE.Raycaster();
const mouse     = new THREE.Vector2();
const tip       = document.getElementById('tip');
let   hovered   = null;

function handleHover(e) {{
  mouse.x =  (e.clientX / window.innerWidth)  * 2 - 1;
  mouse.y = -(e.clientY / window.innerHeight) * 2 + 1;
  raycaster.setFromCamera(mouse, camera);
  const hits = raycaster.intersectObjects(pivot.children.filter(c=>c.isMesh));

  if (hits.length > 0) {{
    const obj = hits[0].object;
    const d   = obj.userData;
    if (d.revenue > 0) {{
      document.getElementById('t-title').textContent = d.cat;
      document.getElementById('t-rev').textContent   = 'R$ ' + d.revenue.toLocaleString();
      document.getElementById('t-ord').textContent   = d.orders.toLocaleString();
      document.getElementById('t-qtr').textContent   = d.qtr;
      document.getElementById('t-st').textContent    = d.state;
      tip.style.display  = 'block';
      tip.style.left     = (e.clientX + 14) + 'px';
      tip.style.top      = (e.clientY - 10) + 'px';
      if (hovered !== obj) {{
        if (hovered) hovered.scale.set(1,1,1);
        obj.scale.set(1.25,1.25,1.25);
        hovered = obj;
      }}
      return;
    }}
  }}
  tip.style.display = 'none';
  if (hovered) {{ hovered.scale.set(1,1,1); hovered=null; }}
}}

// ── Render loop ───────────────────────────────────────────────────────────────
function animate() {{
  requestAnimationFrame(animate);
  if (autoSpin) rotY += 0.004;
  pivot.rotation.x = rotX;
  pivot.rotation.y = rotY;
  renderer.render(scene, camera);
}}
animate();

// ── Resize ────────────────────────────────────────────────────────────────────
window.addEventListener('resize', ()=>{{
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
}});
</script>
</body>
</html>"""

out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cube.html")
with open(out_path, "w", encoding="utf-8") as f:
    f.write(HTML)

print(f"[OK] Saved: {out_path}")
webbrowser.open(f"file:///{out_path.replace(chr(92), '/')}")
print("[OK] Opened in browser")
