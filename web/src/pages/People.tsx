import { BarChart3, Plus, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, NavLink, useNavigate, useParams } from "react-router-dom";
import { Monitoring, Profile, Tier, api, useData } from "../api";
import { PolicyEditor } from "../components/PolicyEditor";
import { Avatar, Choices, Empty, Seg, useApp } from "../components/ui";

const COLORS = ["teal", "coral", "amber", "violet", "blue", "green", "rose", "slate"];

const TIERS: { value: Tier; title: string; text: string }[] = [
  { value: "strict", title: "Family", text: "Adult content, gambling, dating, bypass tools, malware and ads are blocked." },
  { value: "basic", title: "Basic", text: "Ads, trackers and malware are blocked. Nothing else." },
  { value: "none", title: "None", text: "No filtering at all." },
];
const MONITORING: { value: Monitoring; title: string; text: string }[] = [
  { value: "full", title: "Full", text: "Sites and apps, plus pages, searches and videos on inspected devices." },
  { value: "sites", title: "Sites only", text: "Which sites and apps, and for how long. Nothing is decrypted." },
  { value: "off", title: "Off", text: "Nothing is recorded, not even site names." },
];

function Editor({ profile, onSaved, onDeleted }: { profile: Profile; onSaved: () => void; onDeleted: () => void }) {
  const { run, readOnly } = useApp();
  const [draft, setDraft] = useState(profile);
  const [busy, setBusy] = useState(false);
  useEffect(() => setDraft(profile), [profile]);
  const dirty = JSON.stringify(draft) !== JSON.stringify(profile);
  const patch = (p: Partial<Profile>) => setDraft({ ...draft, ...p });
  const save = async () => {
    setBusy(true);
    const { name, kind, color, tier, monitoring, policy } = draft;
    if (await run(() => api(`/api/profiles/${profile.id}`, { method: "PUT", body: { name, kind, color, tier, monitoring, policy } }), "Saved")) onSaved();
    setBusy(false);
  };
  const remove = async () => {
    if (!window.confirm(`Remove ${profile.name}? Their devices become unassigned; their history is kept until it ages out.`)) return;
    if (await run(() => api(`/api/profiles/${profile.id}`, { method: "DELETE" }), `${profile.name} removed`)) onDeleted();
  };
  return (
    <section className="panel">
      <div className="panel-head">
        <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
          <Avatar name={draft.name} color={draft.color} />
          <h2>{draft.name || "New person"}</h2>
        </div>
        <Link className="btn sm" to={`/people/${profile.id}`}><BarChart3 />Activity</Link>
      </div>

      <div className="section" style={{ paddingTop: 4 }}>
        <div className="cols even">
          <label className="field"><span>Name</span>
            <input className="input" value={draft.name} maxLength={40} disabled={readOnly} onChange={(e) => patch({ name: e.target.value })} /></label>
          <div className="field"><span>This is a</span>
            <Seg label="Kind of profile" value={draft.kind} onChange={(kind) => patch({ kind })} options={[{ value: "kid", label: "Kid" }, { value: "adult", label: "Grown-up or shared" }]} /></div>
        </div>
        <div className="field" style={{ marginTop: 14 }}><span>Colour</span>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            {COLORS.map((c) => (
              <button type="button" key={c} className={`avatar sm p-${c}`} aria-label={c} aria-pressed={draft.color === c} disabled={readOnly} onClick={() => patch({ color: c })}
                style={{ border: 0, cursor: "pointer", outline: draft.color === c ? "2px solid var(--ink)" : "none", outlineOffset: 2 }} />
            ))}
          </div>
        </div>
      </div>

      <div className="section">
        <h3>Filter</h3>
        <p>What is blocked for every device that belongs to {draft.name || "this person"}.</p>
        <Choices value={draft.tier} options={TIERS} onChange={(tier) => patch({ tier })} disabled={readOnly} />
      </div>
      <div className="section">
        <h3>Monitoring</h3>
        <p>How much of what they do is visible to you. {draft.kind === "kid" ? "It is worth telling kids what you can see." : ""}</p>
        <Choices value={draft.monitoring} options={MONITORING} onChange={(monitoring) => patch({ monitoring })} disabled={readOnly} />
      </div>

      <PolicyEditor policy={draft.policy} onChange={(policy) => patch({ policy })} disabled={readOnly} filtered={draft.tier !== "none"} />

      <div className="savebar">
        {!readOnly && <button className="btn danger" style={{ marginRight: "auto" }} onClick={remove}><Trash2 />Remove</button>}
        {dirty && !readOnly && <button className="btn quiet" onClick={() => setDraft(profile)}>Discard</button>}
        <button className="btn primary" disabled={!dirty || busy || readOnly || !draft.name.trim()} onClick={save}>Save changes</button>
      </div>
    </section>
  );
}

export default function People() {
  const { run, readOnly } = useApp();
  const params = useParams();
  const navigate = useNavigate();
  const { data: profiles, reload } = useData<Profile[]>("/api/profiles");
  const selected = profiles?.find((p) => p.id === Number(params.id));
  useEffect(() => {
    if (profiles?.length && !params.id && window.matchMedia("(min-width: 901px)").matches) navigate(`/people/${profiles[0].id}/rules`, { replace: true });
  }, [profiles, params.id, navigate]);
  const add = async (kind: "kid" | "adult") => {
    let id = 0;
    const name = kind === "kid" ? "New kid" : "New person";
    if (await run(async () => { id = (await api<{ id: number }>("/api/profiles", { body: { name, kind } })).id; })) {
      await reload();
      navigate(`/people/${id}/rules`);
    }
  };
  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>People</h1>
          <p>Rules belong to a person and follow all of their devices.</p>
        </div>
        {!readOnly && (
          <div style={{ display: "flex", gap: 8 }}>
            <button className="btn" onClick={() => add("adult")}><Plus />Add a grown-up</button>
            <button className="btn primary" onClick={() => add("kid")}><Plus />Add a kid</button>
          </div>
        )}
      </div>
      <div className="people">
        <nav className="people-list panel" style={{ padding: 8 }} aria-label="People">
          {profiles?.map((p) => (
            <NavLink key={p.id} to={`/people/${p.id}/rules`}>
              <Avatar name={p.name} color={p.color} size="sm" />
              <span style={{ flex: 1, minWidth: 0 }}>{p.name}</span>
              <span className="small muted">{p.kind === "kid" ? "Kid" : ""}</span>
            </NavLink>
          ))}
          {profiles && !profiles.length && <Empty title="Nobody yet" />}
        </nav>
        {selected ? <Editor key={selected.id} profile={selected} onSaved={reload} onDeleted={() => { reload(); navigate("/people"); }} />
          : <section className="panel"><Empty title="Pick someone">Choose a person to see and change their rules.</Empty></section>}
      </div>
    </div>
  );
}
