import { Eye, Lock, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Device, Policy, Profile, api, useData } from "../api";
import { PolicyEditor, SectionKey } from "../components/PolicyEditor";
import { Avatar, DeviceIcon, Drawer, Empty, PauseControl, Seg, SwitchRow, useApp } from "../components/ui";
import { ago, lockSentence } from "../lib/format";

const KINDS = [["phone", "Phone"], ["tablet", "Tablet"], ["laptop", "Laptop"], ["desktop", "Desktop"], ["console", "Game console"], ["tv", "TV or streamer"], ["other", "Something else"]];

/** Rule groups a single device may override, and the policy keys each one owns. */
const OVERRIDES: { key: SectionKey; title: string; fields: (keyof Policy)[] }[] = [
  { key: "search", title: "Search and video", fields: ["safesearch", "youtube"] },
  { key: "apps", title: "Apps and services", fields: ["blocked_services"] },
  { key: "bedtime", title: "Bedtime", fields: ["bedtime"] },
];

function DeviceDrawer({ device, profiles, onClose, onChange }: { device: Device; profiles: Profile[]; onClose: () => void; onChange: () => void }) {
  const { run, readOnly } = useApp();
  const [name, setName] = useState(device.name);
  const profile = profiles.find((p) => p.id === device.profile_id);
  const put = async (body: object, done = "Saved") => {
    if (await run(() => api(`/api/devices/${device.id}`, { method: "PUT", body }), done)) onChange();
  };
  const overrides = device.overrides ?? {};
  const effective: Policy | null = profile ? { ...profile.policy, ...overrides } : null;
  const toggleOverride = (fields: (keyof Policy)[], on: boolean) => {
    const next: Partial<Policy> = { ...overrides };
    for (const f of fields) {
      if (on && profile) (next as Record<string, unknown>)[f] = profile.policy[f];
      else delete next[f];
    }
    put({ overrides: next }, on ? "This device now has its own rule" : "Back to the person's rule");
  };
  const forget = async () => {
    if (!window.confirm(`Forget ${device.name}? If it connects again it will show up as a new device.`)) return;
    if (await run(() => api(`/api/devices/${device.id}`, { method: "DELETE" }), "Device forgotten")) { onChange(); onClose(); }
  };
  const canInspect = device.route === "gateway" && device.monitoring === "full";
  return (
    <Drawer title={device.name} onClose={onClose}>
      <div className="stack">
        <div className="state">
          <span className={`dot ${device.online ? "on" : ""}`} />{device.online ? "Online now" : device.last_seen ? `Last seen ${ago(device.last_seen)}` : "Never seen online"}
          {device.locked && <span className="tag"><Lock />{lockSentence(device.lock_reason, device.lock_until)}</span>}
        </div>
        <label className="field"><span>Name</span>
          <div style={{ display: "flex", gap: 8 }}>
            <input className="input" value={name} maxLength={60} disabled={readOnly} onChange={(e) => setName(e.target.value)} />
            <button className="btn" disabled={readOnly || name.trim() === device.name || !name.trim()} onClick={() => put({ name })}>Save name</button>
          </div>
        </label>
        <div className="cols even">
          <label className="field"><span>Belongs to</span>
            <select className="input" value={device.profile_id ?? ""} disabled={readOnly}
              onChange={(e) => put({ profile_id: e.target.value ? Number(e.target.value) : null }, "Device assigned")}>
              <option value="">Nobody yet</option>
              {profiles.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </label>
          <label className="field"><span>Kind of device</span>
            <select className="input" value={device.kind} disabled={readOnly} onChange={(e) => put({ kind: e.target.value })}>
              {KINDS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </label>
        </div>

        <div className="section">
          <h3>Path to the internet</h3>
          <p>Through Breakwater allows real pausing, bypass blocking and inspection. Straight to the router means filtering by DNS only. A change applies the next time the device reconnects.</p>
          <Seg label="Route" value={device.route_setting ?? "auto"} onChange={(v) => put({ route: v === "auto" ? null : v })}
            options={[{ value: "auto", label: `Automatic (${profile?.kind === "kid" || !profile ? "Breakwater" : "router"})` }, { value: "gateway", label: "Through Breakwater" }, { value: "router", label: "Straight to router" }]} />
          <SwitchRow title="Inspect web traffic" disabled={readOnly || !canInspect} checked={device.inspect_setting}
            onChange={(v) => put({ inspect: v }, v ? "Inspection switched on" : "Inspection switched off")}
            hint={canInspect ? <>Shows pages, searches and videos. The device must trust the Breakwater certificate first; see <Link to="/system">System</Link>.</>
              : "Needs full monitoring and a path through Breakwater."} />
        </div>

        {profile && effective && profile.tier !== "none" && (
          <div className="section">
            <h3>Different rules on this device</h3>
            <p>By default a device follows {profile.name}'s rules. Switch one on to set it separately here.</p>
            {OVERRIDES.map((o) => {
              const on = o.fields.some((f) => f in overrides);
              return (
                <div key={o.key}>
                  <SwitchRow title={o.title} checked={on} disabled={readOnly} onChange={(v) => toggleOverride(o.fields, v)} />
                  {on && <PolicyEditor policy={effective} sections={[o.key]} disabled={readOnly}
                    onChange={(p) => put({ overrides: { ...overrides, ...Object.fromEntries(o.fields.map((f) => [f, p[f]])) } })} />}
                </div>
              );
            })}
          </div>
        )}

        <div className="section small soft">
          <div>Address {device.ip || "unknown"}{device.static_ip ? " (reserved)" : ""}</div>
          <div>Hardware address <span className="num">{device.mac}</span></div>
          {device.hostname && <div>Calls itself {device.hostname}</div>}
        </div>
        {!readOnly && <div><button className="btn danger" onClick={forget}><Trash2 />Forget this device</button></div>}
      </div>
    </Drawer>
  );
}

export default function Devices() {
  const { run, readOnly } = useApp();
  const [params, setParams] = useSearchParams();
  const { data: devices, reload } = useData<Device[]>("/api/devices", 15000);
  const { data: profiles } = useData<Profile[]>("/api/profiles");
  const openId = Number(params.get("open")) || null;
  const open = devices?.find((d) => d.id === openId);
  const setOpen = (id: number | null) => setParams(id ? { open: String(id) } : {}, { replace: true });
  useEffect(() => { if (openId && devices && !open) setParams({}, { replace: true }); }, [openId, devices, open, setParams]);
  if (!devices || !profiles) return null;
  const order = (d: Device) => (d.profile_id === null ? -1 : profiles.findIndex((p) => p.id === d.profile_id));
  const sorted = [...devices].sort((a, b) => order(a) - order(b) || a.name.localeCompare(b.name));
  const assign = async (d: Device, value: string) => {
    if (await run(() => api(`/api/devices/${d.id}`, { method: "PUT", body: { profile_id: value ? Number(value) : null } }), "Device assigned")) reload();
  };
  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Devices</h1>
          <p>Everything that has joined the network. New devices appear here by themselves.</p>
        </div>
      </div>
      <section className="panel">
        {sorted.length ? (
          <table className="devices">
            <thead><tr><th>Device</th><th>Belongs to</th><th>Protection</th><th>Status</th><th><span className="sr">Actions</span></th></tr></thead>
            <tbody>
              {sorted.map((d) => {
                const p = profiles.find((x) => x.id === d.profile_id);
                return (
                  <tr key={d.id}>
                    <td className="wide">
                      <div className="dev-name"><DeviceIcon kind={d.kind} />
                        <div style={{ minWidth: 0 }}>
                          <button onClick={() => setOpen(d.id)}>{d.name}</button>
                          <div className="small muted num">{d.ip || d.mac}</div>
                        </div>
                      </div>
                    </td>
                    <td>
                      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                        {p && <Avatar name={p.name} color={p.color} size="sm" />}
                        <select className="input narrow" value={d.profile_id ?? ""} disabled={readOnly} aria-label={`Who ${d.name} belongs to`} onChange={(e) => assign(d, e.target.value)}>
                          <option value="">Nobody yet</option>
                          {profiles.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
                        </select>
                      </div>
                    </td>
                    <td>
                      <div style={{ display: "flex", gap: 5, flexWrap: "wrap" }}>
                        <span className="tag">{{ strict: "Family filter", basic: "Basic filter", none: "No filter" }[d.tier]}</span>
                        <span className="tag">{d.route === "gateway" ? "Through Breakwater" : "Via router"}</span>
                        {d.inspect && <span className="tag"><Eye />Inspected</span>}
                        {!d.known && <span className="tag warn">New</span>}
                      </div>
                    </td>
                    <td>
                      {d.locked ? <span className="state"><Lock />{lockSentence(d.lock_reason, d.lock_until)}</span>
                        : <span className="state"><span className={`dot ${d.online ? "on" : ""}`} />{d.online ? "Online" : d.last_seen ? ago(d.last_seen) : "Not seen"}</span>}
                    </td>
                    <td><PauseControl target="device" id={d.id} locked={d.locked} reason={d.lock_reason} profileId={d.profile_id} onDone={reload} compact /></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        ) : <Empty title="No devices yet">Devices show up here as soon as they get an address from Breakwater.</Empty>}
      </section>
      {open && <DeviceDrawer device={open} profiles={profiles} onClose={() => setOpen(null)} onChange={reload} />}
    </div>
  );
}
