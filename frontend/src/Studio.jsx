import { useEffect, useState } from "react";
import { api } from "./api.js";

const DATASETS = ["employees", "sakila", "tpch"];
const f = (v, d = 1) => (v == null ? "–" : Number(v).toLocaleString(undefined, { maximumFractionDigits: d }));

function Sql({ children }) {
  return <pre className="overflow-x-auto whitespace-pre-wrap break-words rounded bg-slate-900 p-3 text-xs text-slate-100">{children}</pre>;
}

export default function Studio() {
  const [queries, setQueries] = useState([]);
  const [qid, setQid] = useState("");
  const [dataset, setDataset] = useState("sakila");
  const [sql, setSql] = useState("");
  const [provider, setProvider] = useState("ollama");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [res, setRes] = useState(null);

  useEffect(() => { api("/queries").then(setQueries).catch((e) => setErr(e.message)); }, []);

  function pick(id) {
    setQid(id);
    const q = queries.find((x) => x.id === id);
    if (q) { setDataset(q.dataset); setSql(q.sql); }
  }

  async function run() {
    setBusy(true); setErr(""); setRes(null);
    try {
      // An unedited dev-registry query keeps its id (and its cached pool); anything else is ad hoc.
      const q = queries.find((x) => x.id === qid);
      const body = q && q.sql === sql ? { query_id: qid, provider } : { dataset, sql, provider };
      setRes(await api("/studio/optimize", body));
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  }

  const alts = res?.alternatives ?? [];
  const winner = alts.find((a) => a.id === res?.winner);
  const orig = alts.find((a) => a.id === "original");
  const t0 = orig?.metrics.runtime_ms_median, t1 = winner?.metrics.runtime_ms_median;
  const tmax = Math.max(...alts.map((a) => a.metrics?.runtime_ms_median ?? 0), 1e-9);
  return (
    <div className="space-y-4">
      <section className="space-y-2 rounded border border-slate-200 bg-white p-4">
        <div className="flex flex-wrap gap-3 text-sm">
          <label className="flex items-center gap-2">Dev query
            <select className="rounded border px-2 py-1" value={qid} onChange={(e) => pick(e.target.value)}>
              <option value="">(ad hoc)</option>
              {queries.map((q) => <option key={q.id} value={q.id}>{q.id}</option>)}
            </select>
          </label>
          <label className="flex items-center gap-2">Dataset
            <select className="rounded border px-2 py-1" value={dataset} onChange={(e) => setDataset(e.target.value)}>
              {DATASETS.map((d) => <option key={d}>{d}</option>)}
            </select>
          </label>
          <label className="flex items-center gap-2">LLM provider
            <select className="rounded border px-2 py-1" value={provider} onChange={(e) => setProvider(e.target.value)}>
              <option>ollama</option><option>groq</option>
            </select>
          </label>
        </div>
        <textarea className="h-40 w-full rounded border p-2 font-mono text-xs" value={sql} placeholder="SELECT ..."
          onChange={(e) => setSql(e.target.value)} />
        <button onClick={run} disabled={busy || !sql.trim()}
          className="rounded bg-slate-900 px-4 py-1.5 text-sm text-white disabled:opacity-40">
          {busy ? "Optimizing… (can take minutes: LLM + timed runs)" : "Optimize"}
        </button>
        {err && <p className="text-sm text-red-600">{err}</p>}
      </section>

      {res?.error && <p className="rounded bg-amber-50 p-3 text-sm text-amber-800">Pool not created: {res.error}</p>}

      {res && !res.error && (
        <>
          <section className="rounded border border-slate-200 bg-white p-4">
            <h2 className="mb-1 font-semibold">
              OR engine pick: <span className="text-emerald-700">{res.winner}</span>
              {res.winner_raw !== res.winner && <span className="ml-2 text-sm font-normal text-slate-500">(TOPSIS alone: {res.winner_raw})</span>}
            </h2>
            {res.noise_guard.applied && <p className="mb-2 text-sm text-amber-700">Noise guard kept the original: {res.noise_guard.reason}</p>}
            <p className="mb-2 text-xs text-slate-500">
              AHP weights {res.criteria.map((c, i) => `${c} ${res.weights[i]}`).join(" · ")} · CR {res.ahp.consistency_ratio} ·
              pool by {res.pool.generator.provider}/{res.pool.generator.model} ({res.pool.cached ? "cached" : "new"})
            </p>
            {winner && res.winner === "original" && <p className="text-sm text-slate-600">No rewrite beat the original, which is a valid outcome and scores as the baseline.</p>}
            {winner && res.winner !== "original" && (
              <>
                <p className="mb-2 text-sm">
                  <b>{f(t0, 2)} ms → {f(t1, 2)} ms</b>{" "}
                  <span className={t1 < t0 ? "text-emerald-700" : "text-red-600"}>(×{f(t1 / t0, 2)}, {f(t0 / t1, 1)}× {t1 <= t0 ? "faster" : "slower"})</span>
                  {" "}· rows examined {f(orig.metrics.rows_examined, 0)} → {f(winner.metrics.rows_examined, 0)}
                </p>
                <p className="mb-2 text-sm text-slate-600">{winner.rationale}</p>
                <div className="grid gap-2 md:grid-cols-2">
                  <div><p className="text-xs text-slate-500">original</p><Sql>{orig.sql}</Sql></div>
                  <div><p className="text-xs text-slate-500">OR pick: {winner.id}</p><Sql>{winner.sql}</Sql></div>
                </div>
              </>
            )}
          </section>

          <section className="overflow-x-auto rounded border border-slate-200 bg-white p-4">
            <h2 className="mb-2 font-semibold">Measured alternatives</h2>
            <table className="w-full text-sm">
              <thead><tr className="border-b text-left text-slate-500">
                <th className="py-1">id</th><th>runtime ms (median)</th><th>IQR</th><th>rows examined</th><th>est. cost</th><th>est. rows</th><th>TOPSIS</th><th>rank</th>
              </tr></thead>
              <tbody>{alts.map((a) => (
                <tr key={a.id} className={`border-b ${a.id === res.winner ? "bg-emerald-50" : ""}`}>
                  <td className="py-1 font-mono">{a.id}</td>
                  <td><div className="flex items-center gap-2">{f(a.metrics.runtime_ms_median, 2)}
                    <div className="h-2 rounded bg-slate-400" style={{ width: `${(60 * (a.metrics.runtime_ms_median ?? 0)) / tmax}px` }} /></div></td><td>{f(a.metrics.runtime_ms_iqr, 2)}</td>
                  <td>{f(a.metrics.rows_examined, 0)}</td><td>{f(a.metrics.est_cost, 0)}</td><td>{f(a.metrics.est_rows, 0)}</td>
                  <td>{a.ranked ? f(a.topsis_closeness, 3) : <span className="text-red-600">{a.excluded_reason}</span>}</td>
                  <td>{a.ranked ? res.ranking.indexOf(a.id) + 1 : "–"}</td>
                </tr>))}</tbody>
            </table>
          </section>

          <section className="rounded border border-slate-200 bg-white p-4">
            <h2 className="mb-2 font-semibold">All candidates ({res.pool.n_valid} valid of {res.pool.n_candidates})</h2>
            <ul className="space-y-2 text-sm">
              {res.all_candidates.map((c) => (
                <li key={c.id}>
                  <span className="font-mono">{c.id}</span>{" "}
                  <span className={`rounded px-1.5 py-0.5 text-xs ${c.status === "valid" ? "bg-emerald-100 text-emerald-800" : "bg-slate-200 text-slate-700"}`}>
                    {c.status === "invalid" ? `invalid: ${c.check.reason}` : c.status}
                  </span>
                  <span className="ml-2 text-slate-600">{c.rationale}</span>
                  <details><summary className="cursor-pointer text-xs text-slate-500">SQL</summary><Sql>{c.sql}</Sql></details>
                </li>))}
            </ul>
          </section>
        </>
      )}
    </div>
  );
}
