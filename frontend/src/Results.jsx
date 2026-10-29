import { useEffect, useState } from "react";
import { api } from "./api.js";

const COLS = ["3", "3-measured", "3-raw", "2b", "2a"];

function Bar({ a }) {
  const pct = (n) => `${(100 * n) / a.units}%`;
  return (
    <div className="flex h-3 w-40 overflow-hidden rounded bg-slate-100" title={`${a.win} win / ${a.tie} tie / ${a.loss} loss`}>
      <div className="bg-emerald-500" style={{ width: pct(a.win) }} />
      <div className="bg-slate-300" style={{ width: pct(a.tie) }} />
      <div className="bg-red-500" style={{ width: pct(a.loss) }} />
    </div>
  );
}

function cell(c) {
  if (!c) return "–";
  const many = Object.keys(c.picks).length > 1;
  const picks = Object.entries(c.picks).map(([k, n]) => (many ? `${k}×${n}` : k)).join(", ");
  const oc = Object.keys(c.outcomes).join("/");
  const tone = oc.includes("loss") ? "text-red-600" : oc.includes("win") ? "text-emerald-700" : "text-slate-500";
  return <span className={tone}>{picks}{c.ratio != null && ` ×${c.ratio.toFixed(2)}`} {oc}</span>;
}

export default function Results() {
  const [runs, setRuns] = useState([]);
  const [id, setId] = useState("");
  const [run, setRun] = useState(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    api("/results")
      .then((r) => { setRuns(r); setId((r.filter((x) => x.complete && !x.note).at(-1) ?? r.at(-1))?.run_id ?? ""); })
      .catch((e) => setErr(e.message));
  }, []);
  useEffect(() => { if (id) api(`/results/${id}`).then(setRun).catch((e) => setErr(e.message)); }, [id]);

  const m = run?.meta;
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3 text-sm">
        <label>Run{" "}
          <select className="rounded border px-2 py-1" value={id} onChange={(e) => setId(e.target.value)}>
            {runs.map((r) => <option key={r.run_id} value={r.run_id}>{r.run_id} ({r.split}, {r.provider}{r.complete ? "" : ", INCOMPLETE"}{r.note ? ", NOT A RESULT" : ""})</option>)}
          </select>
        </label>
        {err && <span className="text-red-600">{err}</span>}
      </div>

      {run && (
        <>
          {run.note && <p className="rounded bg-red-50 p-3 text-sm text-red-700">Not a result: {run.note}.</p>}
          {!run.complete && !run.note && <p className="rounded bg-red-50 p-3 text-sm text-red-700">This run has no run_end record: it did not finish. Do not read its numbers as a result.</p>}
          <p className="text-xs text-slate-500">
            {m.split} split · {m.query_ids.length} queries · {m.provider}/{m.model} · temp {m.temperature}, seed {m.seed} · {run.failures} failures logged
          </p>

          <section className="overflow-x-auto rounded border border-slate-200 bg-white p-4">
            <h2 className="mb-2 font-semibold">Arms vs baseline</h2>
            <table className="w-full text-sm">
              <thead><tr className="border-b text-left text-slate-500"><th className="py-1">arm</th><th>units</th><th>win</th><th>tie</th><th>loss</th><th>other</th><th></th></tr></thead>
              <tbody>{run.arms.map((a) => (
                <tr key={a.arm} className="border-b"><td className="py-1">{a.name}</td><td>{a.units}</td><td>{a.win}</td><td>{a.tie}</td>
                  <td className={a.loss ? "text-red-600" : ""}>{a.loss}</td><td>{a.other}</td><td><Bar a={a} /></td></tr>))}</tbody>
            </table>
            <p className="mt-2 text-xs text-slate-500">Units are queries for arms 1 and 3, query × repeat for 2a/2b. Ratio = pick time ÷ baseline time (×0.10 is 10× faster). Most queries tie by construction.</p>
          </section>

          <section className="overflow-x-auto rounded border border-slate-200 bg-white p-4">
            <h2 className="mb-2 font-semibold">Per query (contested first)</h2>
            <table className="w-full text-xs">
              <thead><tr className="border-b text-left text-slate-500">
                <th className="py-1">query</th><th>valid</th><th>base ms</th>{COLS.map((c) => <th key={c}>{c}</th>)}
              </tr></thead>
              <tbody>{[...run.queries].sort((a, b) => (b.n_valid > 0) - (a.n_valid > 0)).map((q) => (
                <tr key={q.query_id} className={`border-b align-top ${q.n_valid ? "" : "text-slate-400"}`}>
                  <td className="py-1">{q.query_id}</td><td>{q.n_valid}</td><td>{q.base_ms?.toFixed(1)}</td>
                  {COLS.map((c) => <td key={c}>{cell(q.arms[c])}</td>)}</tr>))}</tbody>
            </table>
          </section>

          <details className="rounded border border-slate-200 bg-white p-4">
            <summary className="cursor-pointer font-semibold">Full text report (paired tests, variance, failures)</summary>
            <pre className="mt-2 overflow-x-auto text-xs">{run.report}</pre>
          </details>
        </>
      )}
    </div>
  );
}
