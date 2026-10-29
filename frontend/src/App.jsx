import { useState } from "react";
import Studio from "./Studio.jsx";
import Results from "./Results.jsx";

// Workload Advisor and Scale Simulator tabs arrive with Phases 4 and 5 (their APIs do not exist yet).
const TABS = { "Query Studio": Studio, Results };

export default function App() {
  const [tab, setTab] = useState("Query Studio");
  const Page = TABS[tab];
  return (
    <div className="mx-auto max-w-6xl p-4">
      <header className="mb-4 flex items-center gap-6 border-b border-slate-200 pb-3">
        <h1 className="text-lg font-semibold">SQL Query Optimizer</h1>
        <nav className="flex gap-2">
          {Object.keys(TABS).map((t) => (
            <button key={t} onClick={() => setTab(t)}
              className={`rounded px-3 py-1 text-sm ${t === tab ? "bg-slate-900 text-white" : "text-slate-600 hover:bg-slate-200"}`}>
              {t}
            </button>
          ))}
        </nav>
      </header>
      <Page />
    </div>
  );
}
