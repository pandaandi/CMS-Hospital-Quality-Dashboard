import csv
import json
import os
from collections import Counter, defaultdict


ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "outputs")
os.makedirs(OUT, exist_ok=True)


def num(value):
    if value is None:
        return None
    text = str(value).replace(",", "").strip()
    if text in {"", "N/A", "Not Available", "Not Applicable", "Too Few to Report"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def mean(values):
    vals = [v for v in values if v is not None]
    return round(sum(vals) / len(vals), 3) if vals else None


def pct(part, whole):
    return round(100 * part / whole, 1) if whole else 0.0


def read_csv(name):
    with open(os.path.join(ROOT, name), encoding="utf-8-sig", newline="") as f:
        yield from csv.DictReader(f)


def short_measure(name):
    mapping = {
        "READM-30-AMI-HRRP": "Heart Attack",
        "READM-30-HF-HRRP": "Heart Failure",
        "READM-30-PN-HRRP": "Pneumonia",
        "READM-30-COPD-HRRP": "COPD",
        "READM-30-HIP-KNEE-HRRP": "Hip/Knee",
        "READM-30-CABG-HRRP": "CABG",
    }
    return mapping.get(name, name.replace("READM-30-", "").replace("-HRRP", ""))


def build():
    hospitals = {}
    for row in read_csv("Hospital_General_Information.csv"):
        fid = row["Facility ID"]
        rating = num(row.get("Hospital overall rating"))
        hospitals[fid] = {
            "facility_id": fid,
            "facility_name": row["Facility Name"],
            "city": row["City/Town"],
            "state": row["State"],
            "county": row["County/Parish"],
            "type": row["Hospital Type"],
            "ownership": row["Hospital Ownership"],
            "emergency": row["Emergency Services"],
            "overall_rating": rating,
            "readm_worse_count": int(num(row.get("Count of READM Measures Worse")) or 0),
            "safety_worse_count": int(num(row.get("Count of Safety Measures Worse")) or 0),
        }

    hrrp_rows = []
    by_hospital = defaultdict(list)
    by_state = defaultdict(list)
    by_measure = defaultdict(list)
    for row in read_csv("FY_2026_HRRP_Hospital.csv"):
        fid = row["Facility ID"]
        ratio = num(row["Excess Readmission Ratio"])
        discharges = num(row["Number of Discharges"])
        predicted = num(row["Predicted Readmission Rate"])
        expected = num(row["Expected Readmission Rate"])
        readmissions = num(row["Number of Readmissions"])
        item = {
            "facility_id": fid,
            "facility_name": row["Facility Name"],
            "state": row["State"],
            "measure": short_measure(row["Measure Name"]),
            "measure_id": row["Measure Name"],
            "excess_ratio": ratio,
            "discharges": discharges,
            "predicted_rate": predicted,
            "expected_rate": expected,
            "readmissions": readmissions,
            "start_date": row["Start Date"],
            "end_date": row["End Date"],
            "above_expected": bool(ratio is not None and ratio > 1),
        }
        hrrp_rows.append(item)
        if ratio is not None:
            by_hospital[fid].append(item)
            by_state[row["State"]].append(item)
            by_measure[item["measure"]].append(item)

    recommend = {}
    for row in read_csv("HCAHPS-Hospital.csv"):
        if row["HCAHPS Measure ID"] == "H_RECMND_DY":
            recommend[row["Facility ID"]] = {
                "recommend_pct": num(row["HCAHPS Answer Percent"]),
                "survey_count": num(row["Number of Completed Surveys"]),
                "survey_response_rate": num(row["Survey Response Rate Percent"]),
            }

    unplanned = defaultdict(list)
    for row in read_csv("Unplanned_Hospital_Visits-Hospital.csv"):
        score = num(row["Score"])
        if score is not None:
            unplanned[row["Facility ID"]].append(
                {
                    "measure_id": row["Measure ID"],
                    "measure_name": row["Measure Name"],
                    "compared": row["Compared to National"],
                    "score": score,
                }
            )

    cards = []
    for fid, hospital in hospitals.items():
        measures = by_hospital.get(fid, [])
        ratios = [x["excess_ratio"] for x in measures]
        worse = sum(1 for x in measures if x["above_expected"])
        max_item = max(measures, key=lambda x: x["excess_ratio"] or -1, default=None)
        rec = recommend.get(fid, {})
        unplanned_bad = sum(1 for x in unplanned.get(fid, []) if "Worse" in x["compared"] or "More" in x["compared"])
        score = 0
        score += worse * 14
        score += max(0, (mean(ratios) or 1) - 1) * 100
        if hospital["overall_rating"] is not None:
            score += max(0, 4 - hospital["overall_rating"]) * 8
        if rec.get("recommend_pct") is not None:
            score += max(0, 70 - rec["recommend_pct"]) * 0.8
        score += unplanned_bad * 5
        cards.append(
            {
                **hospital,
                "avg_excess_ratio": mean(ratios),
                "max_excess_ratio": max(ratios) if ratios else None,
                "above_expected_measures": worse,
                "measure_count": len(ratios),
                "highest_risk_condition": max_item["measure"] if max_item else "N/A",
                "recommend_pct": rec.get("recommend_pct"),
                "survey_count": rec.get("survey_count"),
                "survey_response_rate": rec.get("survey_response_rate"),
                "unplanned_worse_count": unplanned_bad,
                "priority_score": round(score, 1),
            }
        )

    cards.sort(key=lambda x: (x["priority_score"], x["above_expected_measures"], x["max_excess_ratio"] or 0), reverse=True)

    state_summary = []
    for state, rows in by_state.items():
        ratios = [x["excess_ratio"] for x in rows]
        state_cards = [x for x in cards if x["state"] == state]
        state_summary.append(
            {
                "state": state,
                "hospitals": len({x["facility_id"] for x in rows}),
                "measures": len(rows),
                "avg_excess_ratio": mean(ratios),
                "above_expected_pct": pct(sum(1 for x in rows if x["above_expected"]), len(rows)),
                "avg_recommend_pct": mean([x["recommend_pct"] for x in state_cards]),
                "avg_rating": mean([x["overall_rating"] for x in state_cards]),
            }
        )
    state_summary.sort(key=lambda x: (x["above_expected_pct"], x["avg_excess_ratio"] or 0), reverse=True)

    measure_summary = []
    for measure, rows in by_measure.items():
        ratios = [x["excess_ratio"] for x in rows]
        measure_summary.append(
            {
                "measure": measure,
                "hospitals_reporting": len({x["facility_id"] for x in rows}),
                "avg_excess_ratio": mean(ratios),
                "above_expected_pct": pct(sum(1 for x in rows if x["above_expected"]), len(rows)),
                "avg_predicted_rate": mean([x["predicted_rate"] for x in rows]),
                "avg_expected_rate": mean([x["expected_rate"] for x in rows]),
            }
        )
    measure_summary.sort(key=lambda x: x["above_expected_pct"], reverse=True)

    metrics = {
        "title": "CMS FY 2026 Hospital Readmissions & Patient Experience Dashboard",
        "source": "Centers for Medicare & Medicaid Services Provider Data",
        "released": "2026-05-13",
        "updated": "2026-04 to 2026-05 provider-data release cycle",
        "hospitals": len(hospitals),
        "hrrp_measure_rows": len(hrrp_rows),
        "hospitals_with_hrrp": len(by_hospital),
        "states": len(state_summary),
        "avg_excess_ratio": mean([x["excess_ratio"] for x in hrrp_rows]),
        "above_expected_measure_pct": pct(sum(1 for x in hrrp_rows if x["above_expected"]), sum(1 for x in hrrp_rows if x["excess_ratio"] is not None)),
        "avg_recommend_pct": mean([x["recommend_pct"] for x in cards]),
        "avg_rating": mean([x["overall_rating"] for x in cards]),
        "date_range": "07/01/2021 - 06/30/2024 for FY 2026 HRRP measures",
    }

    with open(os.path.join(OUT, "hospital_cards.json"), "w", encoding="utf-8") as f:
        json.dump(cards[:1200], f)
    with open(os.path.join(OUT, "state_summary.json"), "w", encoding="utf-8") as f:
        json.dump(state_summary, f)
    with open(os.path.join(OUT, "measure_summary.json"), "w", encoding="utf-8") as f:
        json.dump(measure_summary, f)
    with open(os.path.join(OUT, "cms_metrics.json"), "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2)
    generate_dashboard(metrics, cards[:1200], state_summary, measure_summary)
    generate_readme(metrics)


def json_script(name, data):
    return f'<script id="{name}" type="application/json">{json.dumps(data)}</script>'


def generate_dashboard(metrics, cards, states, measures):
    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{metrics["title"]}</title>
<style>
:root {{
  --bg:#f7f8fb; --panel:#fff; --ink:#172033; --muted:#687386; --line:#dfe5ee;
  --blue:#2563eb; --cyan:#0891b2; --green:#15803d; --amber:#b45309; --red:#b91c1c;
  --soft-blue:#eaf1ff; --soft-red:#fff1f1; --soft-green:#edf8f1;
}}
* {{ box-sizing:border-box; }}
body {{ margin:0; font-family:Inter, Arial, Helvetica, sans-serif; color:var(--ink); background:var(--bg); }}
.top {{ background:#fff; border-bottom:1px solid var(--line); padding:22px 30px; position:sticky; top:0; z-index:5; }}
.top h1 {{ margin:0 0 6px; font-size:25px; letter-spacing:0; }}
.top p {{ margin:0; color:var(--muted); max-width:1120px; font-size:14px; }}
main {{ max-width:1360px; margin:0 auto; padding:22px 30px 44px; }}
.story {{ display:grid; grid-template-columns:1.2fr .8fr; gap:16px; margin-bottom:16px; }}
.panel,.card {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:16px; }}
.story h2,.panel h2 {{ margin:0 0 10px; font-size:17px; }}
.story ul {{ margin:8px 0 0; padding-left:18px; color:#2d3748; }}
.story li {{ margin:7px 0; }}
.metrics {{ display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:10px; margin-bottom:16px; }}
.metric {{ background:#fff; border:1px solid var(--line); border-radius:8px; padding:14px; min-height:104px; }}
.metric .label {{ color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.04em; display:flex; align-items:center; gap:6px; }}
.metric .value {{ font-size:28px; font-weight:800; margin-top:8px; }}
.metric .note {{ color:var(--muted); font-size:12px; margin-top:5px; }}
.info {{ display:inline-flex; align-items:center; justify-content:center; width:17px; height:17px; border-radius:50%; border:1px solid #9aa6b8; color:#526071; font-size:11px; cursor:help; position:relative; }}
.info:hover::after {{ content:attr(data-tip); position:absolute; left:0; top:22px; width:260px; background:#101828; color:#fff; padding:9px 10px; border-radius:7px; font-size:12px; line-height:1.35; z-index:20; text-transform:none; letter-spacing:0; font-weight:400; }}
.filters {{ display:grid; grid-template-columns:130px 1fr 160px 180px; gap:10px; margin:0 0 16px; }}
select,input {{ width:100%; border:1px solid var(--line); border-radius:8px; padding:10px 11px; font-size:14px; background:#fff; }}
.grid2 {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-bottom:16px; }}
.grid3 {{ display:grid; grid-template-columns:1.1fr .9fr; gap:16px; margin-bottom:16px; }}
.bars {{ display:flex; flex-direction:column; gap:9px; }}
.barrow {{ display:grid; grid-template-columns:110px 1fr 65px; align-items:center; gap:10px; font-size:13px; }}
.bartrack {{ height:18px; border-radius:999px; overflow:hidden; background:#edf1f6; }}
.barfill {{ height:100%; background:linear-gradient(90deg,var(--cyan),var(--blue)); border-radius:999px; }}
.barfill.red {{ background:linear-gradient(90deg,#ef4444,var(--red)); }}
.barfill.amber {{ background:linear-gradient(90deg,#f59e0b,var(--amber)); }}
.scatter {{ height:330px; border:1px solid #edf1f5; border-radius:8px; position:relative; background:linear-gradient(#fff,#fbfcff); overflow:hidden; }}
.dot {{ position:absolute; width:9px; height:9px; border-radius:50%; background:var(--blue); opacity:.72; transform:translate(-50%,-50%); cursor:help; }}
.dot.risk {{ background:var(--red); opacity:.82; }}
.dot:hover {{ width:13px; height:13px; opacity:1; z-index:4; }}
.axis {{ position:absolute; color:#667085; font-size:11px; }}
.axis.x {{ bottom:7px; left:50%; transform:translateX(-50%); }}
.axis.y {{ left:8px; top:50%; transform:rotate(-90deg) translateX(-50%); transform-origin:left top; }}
table {{ width:100%; border-collapse:collapse; font-size:13px; }}
th,td {{ padding:9px 7px; border-bottom:1px solid #edf1f5; text-align:left; vertical-align:top; }}
th {{ color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.035em; cursor:pointer; user-select:none; }}
.pill {{ display:inline-block; padding:3px 7px; border-radius:999px; font-size:12px; font-weight:700; }}
.pill.red {{ color:var(--red); background:var(--soft-red); }}
.pill.green {{ color:var(--green); background:var(--soft-green); }}
.pill.blue {{ color:#1d4ed8; background:var(--soft-blue); }}
.summary {{ font-size:14px; color:#334155; line-height:1.55; }}
.small {{ color:var(--muted); font-size:12px; }}
.tabs {{ display:flex; gap:8px; margin-bottom:10px; flex-wrap:wrap; }}
.tab {{ border:1px solid var(--line); background:#fff; border-radius:999px; padding:7px 11px; cursor:pointer; font-weight:700; color:#435066; }}
.tab.active {{ background:#172033; color:#fff; border-color:#172033; }}
footer {{ color:var(--muted); font-size:12px; padding-top:10px; border-top:1px solid var(--line); }}
@media(max-width:980px) {{ .story,.grid2,.grid3,.metrics,.filters {{ grid-template-columns:1fr; }} .top,main {{ padding-left:16px; padding-right:16px; }} }}
</style>
</head>
<body>
<div class="top">
  <h1>{metrics["title"]}</h1>
  <p>Executive-ready healthcare quality dashboard using current CMS Provider Data. It connects readmission penalty exposure, patient experience, hospital ratings, and unplanned visit signals into one management view.</p>
</div>
<main>
  <section class="story">
    <div class="panel">
      <h2>Management Story</h2>
      <div class="summary">This dashboard answers one practical question: <strong>which hospitals show elevated readmission pressure, weaker patient experience, or broader quality concerns?</strong> It is designed for leaders who need a fast view before asking analysts for deeper drill-down.</div>
      <ul>
        <li><strong>Readmission pressure:</strong> hospitals with excess readmission ratios above 1.0 perform worse than expected after risk adjustment.</li>
        <li><strong>Patient trust:</strong> HCAHPS recommendation scores show whether patients would definitely recommend the hospital.</li>
        <li><strong>Prioritization:</strong> a simple portfolio priority score combines readmission, rating, patient experience, and unplanned visit signals.</li>
      </ul>
    </div>
    <div class="panel">
      <h2>Data Freshness</h2>
      <p class="summary"><strong>Released:</strong> {metrics["released"]}<br><strong>Measure period:</strong> {metrics["date_range"]}<br><strong>Source:</strong> CMS Provider Data public files.</p>
      <p class="small">This is more current for portfolio storytelling than the older patient-level UCI dataset. The tradeoff is that CMS data is hospital-level reporting data, not raw patient-level EHR data.</p>
    </div>
  </section>

  <section class="metrics">
    <div class="metric"><div class="label">Hospitals <span class="info" data-tip="Number of CMS hospital facility records included after joining hospital general information with quality datasets.">?</span></div><div class="value">{metrics["hospitals"]:,}</div><div class="note">CMS hospital facilities</div></div>
    <div class="metric"><div class="label">HRRP Hospitals <span class="info" data-tip="Hospitals with Hospital Readmissions Reduction Program measure records.">?</span></div><div class="value">{metrics["hospitals_with_hrrp"]:,}</div><div class="note">with readmission measures</div></div>
    <div class="metric"><div class="label">Avg Excess Ratio <span class="info" data-tip="Predicted readmissions divided by expected readmissions. Above 1.0 suggests more readmissions than expected after adjustment.">?</span></div><div class="value">{metrics["avg_excess_ratio"]}</div><div class="note">across HRRP measures</div></div>
    <div class="metric"><div class="label">Above Expected <span class="info" data-tip="Share of HRRP measure rows where the excess readmission ratio is greater than 1.0.">?</span></div><div class="value">{metrics["above_expected_measure_pct"]}%</div><div class="note">measure rows above 1.0</div></div>
    <div class="metric"><div class="label">Recommend Score <span class="info" data-tip="HCAHPS percent of patients who would definitely recommend the hospital.">?</span></div><div class="value">{metrics["avg_recommend_pct"]}%</div><div class="note">average definite recommendation</div></div>
  </section>

  <section class="filters">
    <select id="stateFilter"><option value="All">All States</option></select>
    <input id="searchBox" placeholder="Search hospital, city, county, ownership...">
    <select id="riskFilter"><option value="All">All risk levels</option><option value="High">High priority</option><option value="Moderate">Moderate priority</option><option value="Lower">Lower priority</option></select>
    <select id="sortSelect"><option value="priority_score">Sort: Priority Score</option><option value="max_excess_ratio">Sort: Max Excess Ratio</option><option value="recommend_pct">Sort: Recommend %</option><option value="overall_rating">Sort: Overall Rating</option></select>
  </section>

  <section class="grid2">
    <div class="panel">
      <h2>Readmission Pressure by Condition</h2>
      <div id="measureBars" class="bars"></div>
      <p class="small">Bar length shows percent of reporting hospitals above expected readmissions for each condition.</p>
    </div>
    <div class="panel">
      <h2>State Watchlist</h2>
      <div id="stateBars" class="bars"></div>
      <p class="small">Top states are ranked by share of HRRP measure rows above expected.</p>
    </div>
  </section>

  <section class="grid3">
    <div class="panel">
      <h2>Patient Experience vs Readmission Risk</h2>
      <div class="scatter" id="scatter"><span class="axis y">Max Excess Readmission Ratio</span><span class="axis x">Patients Definitely Recommend Hospital (%)</span></div>
      <p class="small">Each dot is a hospital. Red dots show hospitals with multiple above-expected readmission measures.</p>
    </div>
    <div class="panel">
      <h2>How to Read This</h2>
      <p class="summary"><strong>Upper-left hospitals</strong> are the most concerning: readmission ratios are high while patient recommendation is low. <strong>Lower-right hospitals</strong> are generally stronger: lower readmission pressure and better patient trust.</p>
      <p class="summary">This view is intentionally executive-friendly: it does not start with raw clinical detail. It starts with the operational question leaders care about: <strong>where should we investigate first?</strong></p>
    </div>
  </section>

  <section class="panel">
    <h2>Hospital Priority Table</h2>
    <table>
      <thead><tr><th data-sort="facility_name">Hospital</th><th data-sort="state">State</th><th data-sort="priority_score">Priority</th><th data-sort="max_excess_ratio">Max Ratio</th><th data-sort="above_expected_measures">Measures Above Expected</th><th data-sort="recommend_pct">Recommend %</th><th data-sort="overall_rating">Rating</th><th data-sort="highest_risk_condition">Top Condition</th></tr></thead>
      <tbody id="hospitalRows"></tbody>
    </table>
  </section>

  <footer>Data: CMS Provider Data, including FY 2026 Hospital Readmissions Reduction Program, Hospital General Information, HCAHPS Hospital, and Unplanned Hospital Visits files. This portfolio dashboard is for analytics demonstration and not clinical decision support.</footer>
</main>
{json_script("cardsData", cards)}
{json_script("statesData", states)}
{json_script("measuresData", measures)}
<script>
const cards = JSON.parse(document.getElementById('cardsData').textContent);
const states = JSON.parse(document.getElementById('statesData').textContent);
const measures = JSON.parse(document.getElementById('measuresData').textContent);
const fmt = v => v === null || v === undefined ? 'N/A' : (typeof v === 'number' ? (Math.round(v*1000)/1000).toString() : v);
const stateFilter = document.getElementById('stateFilter');
const searchBox = document.getElementById('searchBox');
const riskFilter = document.getElementById('riskFilter');
const sortSelect = document.getElementById('sortSelect');
const uniqueStates = [...new Set(cards.map(d => d.state))].sort();
uniqueStates.forEach(s => {{ const o=document.createElement('option'); o.value=s; o.textContent=s; stateFilter.appendChild(o); }});

function riskLevel(d) {{
  if (d.priority_score >= 45 || d.above_expected_measures >= 3) return 'High';
  if (d.priority_score >= 22 || d.above_expected_measures >= 1) return 'Moderate';
  return 'Lower';
}}
function filtered() {{
  const st = stateFilter.value;
  const q = searchBox.value.toLowerCase().trim();
  const risk = riskFilter.value;
  return cards.filter(d => {{
    const text = [d.facility_name,d.city,d.county,d.ownership,d.type,d.state].join(' ').toLowerCase();
    return (st==='All' || d.state===st) && (!q || text.includes(q)) && (risk==='All' || riskLevel(d)===risk);
  }});
}}
function renderBars() {{
  const mb = document.getElementById('measureBars');
  mb.innerHTML = measures.map(m => `<div class="barrow"><strong>${{m.measure}}</strong><div class="bartrack"><div class="barfill red" style="width:${{m.above_expected_pct}}%"></div></div><span>${{m.above_expected_pct}}%</span></div>`).join('');
  const sb = document.getElementById('stateBars');
  sb.innerHTML = states.slice(0,10).map(s => `<div class="barrow"><strong>${{s.state}}</strong><div class="bartrack"><div class="barfill amber" style="width:${{s.above_expected_pct}}%"></div></div><span>${{s.above_expected_pct}}%</span></div>`).join('');
}}
function renderScatter(data) {{
  const el = document.getElementById('scatter');
  el.querySelectorAll('.dot').forEach(d => d.remove());
  const usable = data.filter(d => d.recommend_pct !== null && d.max_excess_ratio !== null).slice(0,600);
  const xs = usable.map(d=>d.recommend_pct), ys = usable.map(d=>d.max_excess_ratio);
  const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
  usable.forEach(d => {{
    const x = 7 + (d.recommend_pct-minX)/(maxX-minX || 1)*86;
    const y = 92 - (d.max_excess_ratio-minY)/(maxY-minY || 1)*82;
    const dot = document.createElement('span');
    dot.className = 'dot ' + (d.above_expected_measures >= 2 ? 'risk' : '');
    dot.style.left = x + '%';
    dot.style.top = y + '%';
    dot.title = `${{d.facility_name}} (${{d.state}})\\nRecommend: ${{fmt(d.recommend_pct)}}%\\nMax ratio: ${{fmt(d.max_excess_ratio)}}\\nAbove expected measures: ${{d.above_expected_measures}}`;
    el.appendChild(dot);
  }});
}}
function renderTable() {{
  let data = filtered();
  const sort = sortSelect.value;
  data.sort((a,b) => {{
    const av = a[sort], bv = b[sort];
    if (typeof av === 'string') return av.localeCompare(bv || '');
    return (bv ?? -999) - (av ?? -999);
  }});
  document.getElementById('hospitalRows').innerHTML = data.slice(0,80).map(d => {{
    const risk = riskLevel(d);
    const cls = risk === 'High' ? 'red' : risk === 'Moderate' ? 'blue' : 'green';
    return `<tr>
      <td><strong>${{d.facility_name}}</strong><div class="small">${{d.city}}, ${{d.county}} | ${{d.ownership}}</div></td>
      <td>${{d.state}}</td>
      <td><span class="pill ${{cls}}">${{risk}} ${{fmt(d.priority_score)}}</span></td>
      <td>${{fmt(d.max_excess_ratio)}}</td>
      <td>${{d.above_expected_measures}} / ${{d.measure_count}}</td>
      <td>${{fmt(d.recommend_pct)}}</td>
      <td>${{fmt(d.overall_rating)}}</td>
      <td>${{d.highest_risk_condition}}</td>
    </tr>`;
  }}).join('');
  renderScatter(data);
}}
[stateFilter, searchBox, riskFilter, sortSelect].forEach(el => el.addEventListener('input', renderTable));
document.querySelectorAll('th[data-sort]').forEach(th => th.addEventListener('click', () => {{ sortSelect.value = th.dataset.sort; renderTable(); }}));
renderBars();
renderTable();
</script>
</body>
</html>"""
    with open(os.path.join(ROOT, "cms_hospital_quality_dashboard.html"), "w", encoding="utf-8") as f:
        f.write(html)


def generate_readme(metrics):
    text = f"""# CMS Hospital Quality Executive Dashboard

## Project

This portfolio project uses current CMS Provider Data to build an executive-friendly hospital quality dashboard focused on readmissions, patient experience, hospital ratings, and unplanned visit signals.

## Why This Project

The earlier UCI diabetes readmission project is useful for patient-level predictive modeling, but its encounter data ends in 2008. This CMS project is more current and stronger for storytelling because it uses CMS files released in 2026 for FY 2026 hospital quality reporting.

## Data Sources

- CMS Hospital Readmissions Reduction Program, FY 2026
- CMS Hospital General Information
- CMS Patient Survey HCAHPS - Hospital
- CMS Unplanned Hospital Visits - Hospital

CMS source portal: https://data.cms.gov/provider-data/

## Metrics

- Hospitals: {metrics["hospitals"]:,}
- Hospitals with HRRP measures: {metrics["hospitals_with_hrrp"]:,}
- HRRP measure rows: {metrics["hrrp_measure_rows"]:,}
- Average excess readmission ratio: {metrics["avg_excess_ratio"]}
- Share of HRRP rows above expected: {metrics["above_expected_measure_pct"]}%
- Average patient recommendation score: {metrics["avg_recommend_pct"]}%

## Dashboard Features

- State filter
- Hospital search
- Risk-level filter
- Sortable hospital priority table
- Condition-level readmission pressure chart
- State watchlist chart
- Patient experience vs readmission risk scatterplot
- Tooltip definitions for non-technical stakeholders

## Resume Bullets

- Built an executive-facing CMS hospital quality dashboard using FY 2026 CMS Provider Data to analyze readmission penalty exposure, patient experience, hospital ratings, and unplanned visit signals across U.S. hospitals.
- Integrated multiple CMS public datasets, including HRRP, Hospital General Information, HCAHPS, and Unplanned Hospital Visits, to create hospital-level quality profiles and prioritization logic.
- Designed interactive filters, tooltip explanations, condition-level charts, state watchlists, and a sortable priority table to translate complex healthcare quality metrics into management-ready insights.

## Interview Pitch

I built this CMS hospital quality dashboard because I wanted a more current healthcare analytics project than older patient-level datasets. The dashboard combines FY 2026 CMS readmission penalty data, HCAHPS patient recommendation scores, hospital ratings, and unplanned visit indicators. I designed it for both technical and non-technical users: leaders can quickly identify priority hospitals, while analysts can drill into condition-level readmission ratios and quality signals.
"""
    with open(os.path.join(ROOT, "README.md"), "w", encoding="utf-8") as f:
        f.write(text)


if __name__ == "__main__":
    build()
