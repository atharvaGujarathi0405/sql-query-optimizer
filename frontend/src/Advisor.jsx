import { useEffect, useRef, useState } from "react";
import { api } from "./api.js";

const DATASETS = ["employees", "sakila", "tpch"];
const f = (v, d = 1) => (v == null ? "–" : Number(v).toLocaleString(undefined, { maximumFractionDigits: d }));
const Card = ({ title, children }) => (
  <section className="rounded border border-slate-200 bg-white p-4">
    <h2 className="mb-2 font-semibold">{title}</h2>
    {children}
  </section>
);

function ddl(c) {
  return `CREATE INDEX idx_${c.table}_${c.cols.join("_")} ON ${c.table} (${c.cols.join(", ")});`;
}

function SelectionCard({ name, sel, byId }) {
  const a = sel.actual;
  return (
    <div className="rounded border border-slate-200 p-3 text-sm">
      <p className="font-semibold">{name} <span className="text-xs font-normal text-slate-500">{sel.solver}</span></p>
      {sel.chosen.length === 0 ? <p className="text-slate-500">No index passed the noise threshold within the budget.</p> : (
        <>
          <ul className="my-1 space-y-0.5 font-mono text-xs">{sel.chosen.map((id) => <li key={id}>{ddl(byId[id])}</li>)}</ul>
          <p>{f(sel.size_mb, 2)} MB · predicted <b>{f(sel.predicted_ms, 0)} ms</b> saved · measured together <b className={a.gain_ms > 0 ? "text-emerald-700" : "text-red-600"}>{f(a.gain_ms, 0)} ms</b></p>
          {a.regressions.length > 0 && <p className="text-red-600">Slower with these indexes: {a.regressions.join(", ")}</p>}
        </>
      )}
    </div>
  );
}

function Result({ r }) {
  const byId = Object.fromEntries(r.candidates.map((c) => [c.id, c]));
  const ilp = r.selections.ilp, greedy = r.selections.greedy;
  const per = ilp.actual?.per_query ?? greedy.actual?.per_query;
  const chosen = new Set(ilp.chosen);
  const same = JSON.stringify(ilp.chosen) === JSON.stringify(greedy.chosen);
  const maxMs = Math.max(...Object.values(per ?? {}).map((v) => v.base_ms ?? 0), 1e-9);
  return (
    <div className="space-y-4">
      <p className="text-xs text-slate-500">
        {r.id} · MySQL {r.mysql} · budget {r.budget_mb} MB, at most {r.max_indexes} indexes · {r.protocol.warmup_runs} warm-up + {r.protocol.timed_runs} timed runs ·
        indexes are toggled hidden/shown in {r.protocol.rounds ?? 2} alternating rounds; a change counts only above {r.protocol.min_gain_pct}% (and the IQR) · {r.failures.length} failures logged
      </p>
      <div className="grid gap-3 md:grid-cols-2">
        <SelectionCard name="ILP (PuLP + CBC)" sel={ilp} byId={byId} />
        <SelectionCard name="Greedy gain per MB" sel={greedy} byId={byId} />
      </div>
      {same && <p className="text-xs text-slate-500">Both selectors chose the same set here. They differ only when the budget or cap forces a trade-off (see the unit test with a known gap).</p>}

      {per && (
        <Card title="Workload, before and after the ILP's indexes">
          <table className="w-full text-sm">
            <thead><tr className="border-b text-left text-slate-500"><th className="py-1">query</th><th>before ms</th><th>after ms</th><th>change</th><th></th></tr></thead>
            <tbody>{Object.entries(per).map(([q, v]) => {
              const ratio = v.with_ms != null && v.base_ms ? v.with_ms / v.base_ms : null;
              return (
                <tr key={q} className="border-b">
                  <td className="py-1 font-mono text-xs">{q}</td><td>{f(v.base_ms, 1)}</td><td>{f(v.with_ms, 1)}</td>
                  <td className={ratio != null && ratio < 0.85 ? "text-emerald-700" : ratio > 1.15 ? "text-red-600" : "text-slate-500"}>{ratio == null ? "–" : `×${ratio.toFixed(2)}`}</td>
                  <td><div className="h-2 rounded bg-emerald-500" style={{ width: `${(120 * (v.with_ms ?? 0)) / maxMs}px` }} /></td>
                </tr>);
            })}</tbody>
          </table>
        </Card>
      )}

      <Card title={`Index experiments (${r.candidates.length} candidates, each created, measured and dropped)`}>
        <table className="w-full text-xs">
          <thead><tr className="border-b text-left text-slate-500"><th className="py-1">index</th><th>size MB</th><th>measured change per query (ms)</th><th>ILP</th></tr></thead>
          <tbody>{r.candidates.map((c) => (
            <tr key={c.id} className={`border-b align-top ${chosen.has(c.id) ? "bg-emerald-50" : ""}`}>
              <td className="py-1 font-mono">{c.table}({c.cols.join(", ")})</td><td>{f(c.size_mb, 2)}</td>
              <td>{c.error ? <span className="text-red-600">{c.error}</span> : Object.entries(c.benefit_ms).map(([q, g]) => (
                <span key={q} className={`mr-3 ${g > 0 ? "font-semibold text-emerald-700" : c.regression_ms?.[q] > 0 ? "font-semibold text-red-600" : "text-slate-400"}`}>
                  {q} {g > 0 ? `−${f(g, 0)}` : c.regression_ms?.[q] > 0 ? `+${f(c.regression_ms[q], 0)} slower` : "within noise"}</span>))}</td>
              <td>{chosen.has(c.id) ? "chosen" : ""}</td>
            </tr>))}</tbody>
        </table>
      </Card>

      <p className="text-xs text-slate-500">
        Not modelled: the write and storage cost of keeping an index (read-only workload), and queries outside this workload.
        Unchanged-query drift after all indexes were dropped: {Object.entries(r.baseline_drift_ratio).map(([q, d]) => `${q} ×${d}`).join(", ")}.
      </p>
    </div>
  );
}

export default function Advisor() {
  const [queries, setQueries] = useState([]);
  const [cfg, setCfg] = useState(null);
  const [runs, setRuns] = useState([]);
  const [dataset, setDataset] = useState("sakila");
  const [budget, setBudget] = useState(64);
  const [cap, setCap] = useState(4);
  const [job, setJob] = useState(null);
  const [shown, setShown] = useState(null);
  const [err, setErr] = useState("");
  const timer = useRef(null);

  const loadRuns = () => api("/advisor/runs").then((r) => { setRuns(r); setShown((s) => s ?? r[0]?.id ?? null); }).catch((e) => setErr(e.message));
  useEffect(() => {
    api("/queries").then(setQueries).catch((e) => setErr(e.message));
    api("/advisor/settings").then((c) => { setCfg(c); setBudget(c.default_budget_mb); setCap(c.default_max_indexes); }).catch((e) => setErr(e.message));
    loadRuns();
    return () => clearInterval(timer.current);
  }, []);

  async function start() {
    setErr(""); setJob({ status: "running", log: ["starting…"] });
    try {
      const { job_id } = await api("/advisor/run", { dataset, budget_mb: Number(budget), max_indexes: Number(cap) });
      timer.current = setInterval(async () => {
        const j = await api(`/advisor/jobs/${job_id}`).catch(() => null);
        if (!j) return;
        setJob(j);
        if (j.status !== "running") {
          clearInterval(timer.current);
          if (j.result) { await loadRuns(); setShown(j.result.id); }
        }
      }, 2000);
    } catch (e) { setErr(e.message); setJob(null); }
  }

  const running = job?.status === "running";
  const r = runs.find((x) => x.id === shown);
  const wl = queries.filter((q) => q.dataset === dataset);
  return (
    <div className="space-y-4">
      <p className="max-w-3xl text-sm text-slate-700">
        Given a workload, which indexes are worth their storage? The advisor <b>really creates</b> each candidate index, measures the workload against a paired
        baseline, drops it again, then lets an integer program (CBC) pick the best set under a storage budget. A greedy heuristic gets the same measurements for comparison.
      </p>
      <section className="space-y-2 rounded border border-slate-200 bg-white p-4">
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <label>Dataset <select className="rounded border px-2 py-1" value={dataset} onChange={(e) => setDataset(e.target.value)}>{DATASETS.map((d) => <option key={d}>{d}</option>)}</select></label>
          <label>Budget MB <input type="number" min="0.1" step="any" className="w-20 rounded border px-2 py-1" value={budget} onChange={(e) => setBudget(e.target.value)} /></label>
          <label>Max indexes <input type="number" min="1" max="10" className="w-16 rounded border px-2 py-1" value={cap} onChange={(e) => setCap(e.target.value)} /></label>
          <button onClick={start} disabled={running} className="rounded bg-slate-900 px-4 py-1.5 text-white disabled:opacity-40">{running ? "Running…" : "Run advisor"}</button>
        </div>
        <p className="text-xs text-slate-500">Workload: the {wl.length} dev-split {dataset} queries ({wl.map((q) => q.id).join(", ")}). The test split is never used. Candidate indexes
          are created with the admin user and always dropped; measurements use the read-only user. Do not run this while an evaluation run is in progress.</p>
        {err && <p className="text-sm text-red-600">{err}</p>}
        {job && (
          <pre className="max-h-40 overflow-auto rounded bg-slate-900 p-2 text-xs text-slate-100">{job.log.join("\n")}{job.error ? `\nERROR: ${job.error}` : ""}</pre>
        )}
      </section>

      {runs.length > 0 && (
        <div className="flex items-center gap-2 text-sm">
          <label>Saved run <select className="rounded border px-2 py-1" value={shown ?? ""} onChange={(e) => setShown(e.target.value)}>
            {runs.map((x) => <option key={x.id} value={x.id}>{x.id}</option>)}</select></label>
        </div>
      )}
      {r ? <Result r={r} /> : <p className="text-sm text-slate-500">No advisor run yet. Pick a dataset and run one (sakila takes under a minute; tpch several).</p>}
    </div>
  );
}
