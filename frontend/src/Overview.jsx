import { useEffect, useState } from "react";
import { api } from "./api.js";
import { Bar } from "./Results.jsx";

// Phase status is project knowledge, not API data: keep in step with project_progress.txt.
const PHASES = [
  ["0–1", "Infrastructure, datasets, 40-query registry, frozen dev/test split", "done"],
  ["2", "Query Studio core: candidate pool, correctness check, metrics, AHP + TOPSIS", "done"],
  ["3", "Three-arm evaluation harness, dev runs, freeze (tag freeze-v1)", "done"],
  ["3b", "The single frozen TEST run (two attempts aborted by the environment; not yet completed)", "pending"],
  ["4", "Workload Advisor: real index experiments, ILP (CBC) vs greedy selection, validated on the server", "done"],
  ["5", "Scale Simulator (M/M/c + SimPy)", "todo"],
  ["6", "UI: Overview, Query Studio, Workload Advisor, Results (Scale Simulator tab arrives with Phase 5)", "partial"],
];
const TONE = { done: "bg-emerald-100 text-emerald-800", pending: "bg-amber-100 text-amber-800", partial: "bg-sky-100 text-sky-800", todo: "bg-slate-200 text-slate-600" };
const LABEL = { done: "done", pending: "pending", partial: "partial", todo: "not started" };

const ARMS = [
  ["1", "Baseline", "MySQL's own optimizer on the original query."],
  ["2a / 2b", "LLM self-selection", "Qwen2.5-Coder picks a rewrite. 2a sees SQL + schema; 2b also sees the metric table."],
  ["3", "OR engine", "AHP weights + TOPSIS ranking over measured runtime, rows examined and EXPLAIN estimates."],
];

const Card = ({ title, children }) => (
  <section className="rounded border border-slate-200 bg-white p-4">
    <h2 className="mb-2 font-semibold">{title}</h2>
    {children}
  </section>
);

function Weights({ a, title }) {
  return (
    <div className="text-sm">
      <p className="mb-1 text-slate-500">{title} · consistency ratio {a.cr}</p>
      {a.criteria.map((c, i) => (
        <div key={c} className="flex items-center gap-2">
          <span className="w-28 font-mono text-xs">{c}</span>
          <div className="h-2 flex-1 rounded bg-slate-100"><div className="h-2 rounded bg-slate-700" style={{ width: `${a.weights[i] * 100}%` }} /></div>
          <span className="w-10 text-right text-xs">{a.weights[i]}</span>
        </div>
      ))}
    </div>
  );
}

export default function Overview({ go }) {
  const [ov, setOv] = useState(null);
  const [health, setHealth] = useState(null);
  const [run, setRun] = useState(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    api("/health").then(() => setHealth(true)).catch(() => setHealth(false));
    api("/overview").then(setOv).catch((e) => setErr(e.message));
    // Headline run: the latest finished, valid, non-withheld run (dev-006 until the test run completes).
    api("/results").then((rs) => {
      const r = rs.filter((x) => x.complete && !x.note && !x.withheld).at(-1);
      return r && api(`/results/${r.run_id}`);
    }).then(setRun).catch((e) => setErr(e.message));
  }, []);

  const wins = (run?.queries ?? [])
    .filter((q) => q.arms["3"]?.ratio != null && q.arms["3"].ratio < 0.95)
    .sort((a, b) => a.arms["3"].ratio - b.arms["3"].ratio);
  const or = run?.arms.find((a) => a.arm === "3");

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <p className="max-w-3xl text-sm text-slate-700">
          <b>Thesis:</b> an operations-research decision engine picks better SQL rewrites than an LLM choosing for itself.
          Both choose from the <i>same</i> correctness-verified pool of candidate rewrites, measured on the same MySQL 8.4 server.
        </p>
        <span className={`rounded px-2 py-0.5 text-xs ${health ? "bg-emerald-100 text-emerald-800" : health === false ? "bg-red-100 text-red-700" : "bg-slate-100"}`}>
          API + MySQL: {health == null ? "checking…" : health ? "connected" : "unreachable"}
        </span>
      </div>
      {err && <p className="text-sm text-red-600">{err}</p>}

      <div className="grid gap-3 md:grid-cols-3">
        {ARMS.map(([n, t, d]) => (
          <div key={n} className="rounded border border-slate-200 bg-white p-3">
            <p className="text-xs text-slate-500">Arm {n}</p><p className="font-semibold">{t}</p><p className="text-sm text-slate-600">{d}</p>
          </div>
        ))}
      </div>

      {run && (
        <Card title={`Headline result: ${run.meta.run_id} (${run.meta.split} split, ${run.meta.query_ids.length} queries, ${run.meta.provider})`}>
          {run.meta.split === "dev" && <p className="mb-2 rounded bg-amber-50 p-2 text-xs text-amber-800">Dev split: the set the design was tuned on. It is a reference run, not the reported result; that is the frozen test run.</p>}
          <table className="w-full text-sm">
            <thead><tr className="border-b text-left text-slate-500"><th className="py-1">arm</th><th>win</th><th>tie</th><th>loss</th><th></th></tr></thead>
            <tbody>{run.arms.filter((a) => ["1", "2a", "2b", "3"].includes(a.arm) || a.arm === "3-argmin").map((a) => (
              <tr key={a.arm} className="border-b"><td className="py-1">{a.name}</td><td>{a.win}</td><td>{a.tie}</td><td className={a.loss ? "text-red-600" : ""}>{a.loss}</td><td><Bar a={a} /></td></tr>))}</tbody>
          </table>
          {or && <p className="mt-2 text-sm">OR engine: <b>{or.win}</b> wins, {or.tie} ties, <b>{or.loss}</b> losses against MySQL's plan. Most queries tie by construction: a rewrite has to beat the baseline by more than noise.</p>}
          <button onClick={() => go("Results")} className="mt-1 text-xs text-sky-700 underline">Full report, paired tests, per-query table →</button>
        </Card>
      )}

      {wins.length > 0 && (
        <Card title={`What the OR engine found (${run.meta.run_id})`}>
          <table className="w-full text-sm">
            <thead><tr className="border-b text-left text-slate-500"><th className="py-1">query</th><th>baseline ms</th><th>OR pick</th><th>time vs baseline</th><th></th></tr></thead>
            <tbody>{wins.map((q) => {
              const c = q.arms["3"]; return (
                <tr key={q.query_id} className="border-b">
                  <td className="py-1 font-mono text-xs">{q.query_id}</td><td>{q.base_ms?.toFixed(1)}</td>
                  <td className="font-mono text-xs">{Object.keys(c.picks).join(", ")}</td>
                  <td className="text-emerald-700">×{c.ratio.toFixed(2)} ({(1 / c.ratio).toFixed(1)}× faster)</td>
                  <td><div className="h-2 w-32 rounded bg-slate-100"><div className="h-2 rounded bg-emerald-500" style={{ width: `${c.ratio * 100}%` }} /></div></td>
                </tr>);
            })}</tbody>
          </table>
          <button onClick={() => go("Query Studio")} className="mt-2 text-xs text-sky-700 underline">Re-run any of these live in Query Studio →</button>
        </Card>
      )}

      <div className="grid gap-4 md:grid-cols-2">
        <Card title="Project status">
          <ul className="space-y-1.5 text-sm">
            {PHASES.map(([p, t, s]) => (
              <li key={p} className="flex gap-2"><span className={`h-fit shrink-0 rounded px-1.5 text-xs ${TONE[s]}`}>{LABEL[s]}</span><span><b>Phase {p}</b> {t}</span></li>
            ))}
          </ul>
        </Card>
        <Card title="Inputs and settings">
          {ov ? (
            <div className="space-y-3 text-sm">
              <p>{ov.queries.total} queries: {Object.entries(ov.queries.by_dataset).map(([d, n]) => `${d} ${n}`).join(" · ")}.
                Frozen split: {ov.queries.dev} dev / {ov.queries.test} test (the test split is never shown in this UI).</p>
              <p>Dev pools: {ov.dev_pools.generated} generated, {ov.dev_pools.with_a_real_choice} offer at least one valid rewrite.</p>
              <Weights a={ov.ahp} title="AHP weights, fixed before any run (config/ahp.yaml)" />
              <Weights a={ov.ahp_measured} title="Declared secondary arm 3-measured" />
              <p className="text-xs text-slate-500">
                {ov.run_config.warmup_runs} warm-up + {ov.run_config.timed_runs} timed runs, median and IQR · LLM arms repeated {ov.run_config.llm_repeats}× ·
                tie band {ov.run_config.tie_band_pct}% · {ov.run_config.query_timeout_s}s timeout · read-only MySQL user for every candidate.
              </p>
            </div>
          ) : <p className="text-sm text-slate-500">Loading…</p>}
        </Card>
      </div>
    </div>
  );
}
