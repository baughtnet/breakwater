import { CircleAlert, CircleCheck, CircleX, Download, LogOut, Send } from "lucide-react";
import { FormEvent, ReactNode, useEffect, useState } from "react";
import { HouseSettings, NodeStatus, SystemInfo, api, useData } from "../api";
import { Seg, SwitchRow, TagInput, useApp } from "../components/ui";
import { ago } from "../lib/format";

function Check({ state, title, children }: { state: "ok" | "bad" | "warn"; title: string; children?: ReactNode }) {
  const Icon = state === "ok" ? CircleCheck : state === "bad" ? CircleX : CircleAlert;
  const word = { ok: "Working", bad: "Problem", warn: "Check this" }[state];
  return (
    <div className="check">
      <Icon className={state} aria-label={word} />
      <div><div className="label">{title}</div>{children && <div className="hint">{children}</div>}</div>
    </div>
  );
}

function Node({ n }: { n: NodeStatus }) {
  const dnsOk = Object.values(n.dns).length > 0 && Object.values(n.dns).every((d) => d.ok);
  const status = !n.online ? "Offline" : n.active ? "Active, handling traffic" : "Standing by";
  return (
    <div className="row" style={{ alignItems: "flex-start" }}>
      <span className="alert-icon" style={n.online ? { background: "var(--accent-wash)", color: "var(--ink)" } : undefined}>
        {n.online ? <CircleCheck /> : <CircleX />}
      </span>
      <div className="grow">
        <div className="title">{n.node_id} <span className="tag">{n.role === "primary" ? "Primary" : "Standby"}</span>{n.self && <span className="tag" style={{ marginLeft: 4 }}>This node</span>}</div>
        <div className="sub">{status}{n.ip ? `, ${n.ip}` : ""}</div>
        {n.online && (
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 6 }}>
            <span className={`tag ${dnsOk ? "" : "warn"}`}>Filters {dnsOk ? "answering" : "not answering"}</span>
            <span className={`tag ${n.proxy ? "" : "warn"}`}>Inspection {n.proxy ? "running" : "stopped"}</span>
            <span className="tag">{n.dhcp ? "Handing out addresses" : "Not handing out addresses"}</span>
          </div>
        )}
      </div>
    </div>
  );
}

const CERT_STEPS: Record<string, ReactNode[]> = {
  Windows: [
    <>On the PC, sign in as an administrator and download the certificate (<code>.cer</code>) from this page.</>,
    <>Double-click it, choose <strong>Install Certificate</strong>, then <strong>Local Machine</strong>.</>,
    <>Pick <strong>Place all certificates in the following store</strong> and select <strong>Trusted Root Certification Authorities</strong>.</>,
    <>Chrome and Edge trust it straight away. For Firefox, set <code>security.enterprise_roots.enabled</code> to true in <code>about:config</code>.</>,
  ],
  Android: [
    <>Open this page on the phone or tablet and download the certificate (<code>.cer</code>).</>,
    <>Go to <strong>Settings, Security, Encryption and credentials, Install a certificate, CA certificate</strong> and choose the file.</>,
    <>Chrome will now be inspected. Most other apps ignore certificates you add, so they stay visible by site name only.</>,
  ],
  Chromebook: [
    <>Download the certificate (<code>.pem</code>) on the Chromebook.</>,
    <>Open <code>chrome://certificate-manager</code>, choose <strong>Local certificates</strong>, then <strong>Installed by you</strong>, and import it under <strong>Trusted certificates</strong>.</>,
    <>If a supervised (Family Link) account won't allow the import, leave inspection off for that device. Filtering still works.</>,
  ],
  Linux: [
    <>Download the certificate (<code>.pem</code>).</>,
    <>Fedora: copy it to <code>/etc/pki/ca-trust/source/anchors/</code> and run <code>sudo update-ca-trust</code>. Debian or Ubuntu: copy it to <code>/usr/local/share/ca-certificates/breakwater.crt</code> and run <code>sudo update-ca-certificates</code>.</>,
    <>Firefox and Chrome keep their own stores; import it under their certificate settings too, or set the Firefox enterprise roots policy.</>,
  ],
};

function Household() {
  const { run, readOnly } = useApp();
  const { data, reload } = useData<HouseSettings>("/api/settings");
  const [draft, setDraft] = useState<HouseSettings | null>(null);
  useEffect(() => { if (data) setDraft(data); }, [data]);
  if (!draft || !data) return null;
  const dirty = JSON.stringify(draft) !== JSON.stringify(data);
  const patch = (p: Partial<HouseSettings>) => setDraft({ ...draft, ...p });
  const save = async () => { if (await run(() => api("/api/settings", { method: "PUT", body: draft }), "Saved")) reload(); };
  return (
    <section className="panel">
      <h2>Household settings</h2>
      <div className="section" style={{ paddingTop: 8 }}>
        <SwitchRow title="Hold new devices to the kids' filter" checked={draft.quarantine_unknown} disabled={readOnly} onChange={(v) => patch({ quarantine_unknown: v })}
          hint="A device nobody has claimed gets the Family filter and goes through Breakwater until you assign it. Turn off to treat unknown devices as guests." />
      </div>
      <div className="section">
        <h3>Notifications</h3>
        <p>Serious alerts can be pushed to your phone through an ntfy topic or any address that accepts a simple POST.</p>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <input className="input" style={{ maxWidth: 420 }} placeholder="https://ntfy.sh/your-private-topic" aria-label="Notification address" value={draft.ntfy_url} disabled={readOnly}
            onChange={(e) => patch({ ntfy_url: e.target.value })} />
          <button className="btn" disabled={!data.ntfy_url || dirty} onClick={() => run(() => api("/api/settings/test-notification", { method: "POST" }), "Test sent")}><Send />Send a test</button>
        </div>
      </div>
      <div className="section">
        <h3>Watch words</h3>
        <p>Searches containing these raise an alert, in addition to the built-in lists for self-harm, explicit content, drugs, violence and bypass tools. Nothing is blocked.</p>
        <TagInput label="Watch word" placeholder="A word or phrase" values={draft.watch_terms} disabled={readOnly} onChange={(v) => patch({ watch_terms: v })} />
      </div>
      <div className="section">
        <h3>Never inspect</h3>
        <p>Sites that break when inspected (banking, some games). Their traffic passes through untouched and shows by site name only.</p>
        <TagInput label="Site to skip" placeholder="example.com" values={draft.passthrough_hosts} disabled={readOnly} onChange={(v) => patch({ passthrough_hosts: v })} />
      </div>
      <div className="section">
        <h3>How long to keep history</h3>
        <div style={{ display: "flex", gap: 20, flexWrap: "wrap" }}>
          <label className="field"><span>Pages, searches, videos</span>
            <div className="state"><input className="input" type="number" min={1} max={730} style={{ width: 90 }} value={draft.detail_days} disabled={readOnly} onChange={(e) => patch({ detail_days: Number(e.target.value) })} />days</div></label>
          <label className="field"><span>Screen-time totals</span>
            <div className="state"><input className="input" type="number" min={1} max={730} style={{ width: 90 }} value={draft.rollup_days} disabled={readOnly} onChange={(e) => patch({ rollup_days: Number(e.target.value) })} />days</div></label>
        </div>
      </div>
      <div className="savebar">
        {dirty && <button className="btn quiet" onClick={() => setDraft(data)}>Discard</button>}
        <button className="btn primary" disabled={!dirty || readOnly} onClick={save}>Save changes</button>
      </div>
    </section>
  );
}

function Account() {
  const { run, session, readOnly } = useApp();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const change = async (e: FormEvent) => {
    e.preventDefault();
    if (await run(() => api("/api/account/password", { method: "PUT", body: { current, new: next } }), "Password changed")) { setCurrent(""); setNext(""); }
  };
  const [theme, setThemeState] = useState(() => document.documentElement.dataset.theme ?? "auto");
  const setTheme = (t: string) => {
    setThemeState(t);
    if (t === "auto") delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = t;
    try { if (t === "auto") localStorage.removeItem("bw-theme"); else localStorage.setItem("bw-theme", t); } catch { /* not remembered */ }
  };
  const signOut = async () => { await api("/api/logout", { method: "POST" }); window.location.assign("/"); };
  return (
    <section className="panel">
      <div className="panel-head"><div><h2>Account</h2><p>Signed in as {session.username}</p></div>
        <button className="btn" onClick={signOut}><LogOut />Sign out</button></div>
      <div className="switch-row" style={{ paddingTop: 0 }}>
        <div><div className="label">Appearance</div><div className="hint">Remembered on this browser.</div></div>
        <Seg label="Appearance" value={theme} onChange={setTheme} options={[{ value: "auto", label: "Match system" }, { value: "light", label: "Light" }, { value: "dark", label: "Dark" }]} />
      </div>
      {!readOnly && (
        <form onSubmit={change} style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "flex-end" }}>
          <label className="field"><span>Current password</span><input className="input" type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} required /></label>
          <label className="field"><span>New password</span><input className="input" type="password" autoComplete="new-password" minLength={10} value={next} onChange={(e) => setNext(e.target.value)} required /></label>
          <button className="btn">Change password</button>
        </form>
      )}
    </section>
  );
}

export default function SystemPage() {
  const { data } = useData<SystemInfo>("/api/system", 10000);
  const [platform, setPlatform] = useState("Windows");
  if (!data) return null;
  const me = data.nodes.find((n) => n.self)!;
  const active = data.nodes.find((n) => n.active);
  const dnsOk = Object.values(me.dns).every((d) => d.ok) && Object.keys(me.dns).length > 0;
  const emptyLists = data.lists.filter((l) => l.enabled && !l.rules);
  const offline = data.nodes.filter((n) => !n.online);
  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>System</h1>
          <p>Network {data.network.lan}. Devices use {data.network.vip} for DNS; the router is {data.network.router}.</p>
        </div>
      </div>

      <div className="cols even">
        <section className="panel">
          <h2>Health</h2>
          <div style={{ marginTop: 6 }}>
            <Check state={active ? "ok" : "bad"} title={active ? `${active.node_id} is handling traffic` : "No node is handling traffic"}>
              {!active && "Kids' devices have no internet until a node is back. Check that the gateway container is running."}
            </Check>
            <Check state={dnsOk ? "ok" : "bad"} title={dnsOk ? "Both filters are answering" : "A filter is not answering"}>
              {!dnsOk && Object.entries(me.dns).filter(([, d]) => !d.ok).map(([t, d]) => `${t}: ${d.error || "starting"}`).join("; ")}
            </Check>
            <Check state={me.proxy ? "ok" : "bad"} title={me.proxy ? "Inspection is running" : "Inspection is stopped"} />
            <Check state={emptyLists.length ? "warn" : "ok"} title={emptyLists.length ? `${emptyLists.length} blocklist${emptyLists.length > 1 ? "s have" : " has"} not downloaded` : "Blocklists are loaded"}>
              {emptyLists.map((l) => l.name).join(", ")}
            </Check>
            <Check state={me.ipv6_leak ? "warn" : "ok"} title={me.ipv6_leak ? "IPv6 is active on the network" : "No IPv6 path around the filter"}>
              {me.ipv6_leak && "Devices may reach the internet over IPv6 without passing through Breakwater. Turn IPv6 off on the router's LAN side."}
            </Check>
            {offline.length > 0 && <Check state="warn" title={`${offline.map((n) => n.node_id).join(", ")} ${offline.length > 1 ? "are" : "is"} offline`}>There is no backup until it returns.</Check>}
          </div>
        </section>
        <section className="panel">
          <h2>Nodes</h2>
          <div className="rows">{data.nodes.map((n) => <Node key={n.node_id} n={n} />)}</div>
          {data.nodes.length === 1 && <p className="hint" style={{ marginTop: 8 }}>Running on one machine. Add a second node and a reboot no longer takes the kids offline; see the deployment guide.</p>}
        </section>
      </div>

      <section className="panel">
        <div className="panel-head">
          <div><h2>Inspection certificate</h2><p>A device must trust this before you switch inspection on for it.</p></div>
          {data.ca_available && (
            <div style={{ display: "flex", gap: 8 }}>
              <a className="btn" href="/ca.cer" download><Download />.cer</a>
              <a className="btn" href="/ca.pem" download><Download />.pem</a>
            </div>
          )}
        </div>
        {!data.ca_available && <p className="error-text">The certificate has not been generated yet. It is created when the gateway first starts.</p>}
        <Seg label="Platform" value={platform} onChange={setPlatform} options={Object.keys(CERT_STEPS).map((k) => ({ value: k, label: k }))} />
        <ol className="steps">{CERT_STEPS[platform].map((s, i) => <li key={i}>{s}</li>)}</ol>
        <p className="hint" style={{ marginTop: 10 }}>On a kid's device, open <code>http://{data.network.vip}:8080/ca.cer</code> directly. The rest of this dashboard is closed to their devices.</p>
      </section>

      <Household />

      <div className="cols even">
        <section className="panel">
          <h2>Blocklists</h2>
          <table className="data" style={{ marginTop: 8 }}>
            <thead><tr><th>List</th><th>Filter</th><th className="n">Rules</th></tr></thead>
            <tbody>{data.lists.map((l, i) => <tr key={i}><td>{l.name}</td><td>{l.tier === "strict" ? "Family" : "Basic"}</td><td className="n">{l.rules.toLocaleString()}</td></tr>)}</tbody>
          </table>
          {!data.lists.length && <p className="hint">The filters have not reported yet.</p>}
        </section>
        <section className="panel">
          <div className="panel-head"><div><h2>Skipped by inspection</h2><p>Apps that refused the certificate in the last day and were let through untouched.</p></div></div>
          {data.pinned.length ? (
            <div className="rows">{data.pinned.slice(0, 12).map((p, i) => (
              <div className="row" key={i}><div className="grow"><div className="title">{p.host}</div></div><span className="when">{ago(p.ts)}</span></div>))}</div>
          ) : <p className="hint">None. If a whole device appears here, its certificate is probably not installed.</p>}
        </section>
      </div>

      <Account />
    </div>
  );
}
