import { Check, Info, ShieldAlert, TriangleAlert } from "lucide-react";
import { useState } from "react";
import { Alert, api, useData } from "../api";
import { Empty, Seg, useApp } from "../components/ui";
import { ago } from "../lib/format";

export function AlertRow({ a, onChange }: { a: Alert; onChange: () => void }) {
  const { run } = useApp();
  const Icon = a.severity === "high" ? ShieldAlert : a.severity === "medium" ? TriangleAlert : Info;
  const severity = { high: "Serious", medium: "Worth a look", info: "For your information" }[a.severity];
  const who = a.profile ? `${a.profile}, on ${a.device}` : a.device;
  const ack = async () => {
    if (await run(() => api("/api/alerts/ack", { body: { key: a.key } }))) {
      window.dispatchEvent(new Event("bw:alerts-changed"));
      onChange();
    }
  };
  return (
    <div className={`row ${a.acked ? "acked" : ""}`}>
      <span className={`alert-icon ${a.severity}`} title={severity}><Icon aria-label={severity} /></span>
      <div className="grow">
        <div className="title">{a.title}</div>
        <div className="sub">{a.detail ? `${a.detail}. ` : ""}{who}</div>
      </div>
      <span className="when">{ago(a.ts)}</span>
      {!a.acked && <button className="btn icon quiet" onClick={ack} aria-label="Mark as seen" title="Mark as seen"><Check /></button>}
    </div>
  );
}

export default function Alerts() {
  const { run } = useApp();
  const [show, setShow] = useState<"new" | "all">("new");
  const { data, reload } = useData<Alert[]>(`/api/alerts?unacked=${show === "new"}`, 20000);
  const ackAll = async () => {
    if (await run(() => api("/api/alerts/ack", { body: { all: true } }), "All marked as seen")) {
      window.dispatchEvent(new Event("bw:alerts-changed"));
      reload();
    }
  };
  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Alerts</h1>
          <p>Searches, blocked attempts and new devices that may deserve a conversation.</p>
        </div>
        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <Seg label="Which alerts" value={show} onChange={setShow} options={[{ value: "new", label: "New" }, { value: "all", label: "All" }]} />
          {show === "new" && !!data?.length && <button className="btn" onClick={ackAll}><Check />Mark all as seen</button>}
        </div>
      </div>
      <section className="panel">
        {data && data.length > 0 ? <div className="rows">{data.map((a) => <AlertRow key={a.id} a={a} onChange={reload} />)}</div>
          : data && <Empty title={show === "new" ? "All clear" : "No alerts yet"}>{show === "new" ? "Nothing new needs your attention." : "Alerts will collect here as they happen."}</Empty>}
      </section>
    </div>
  );
}
