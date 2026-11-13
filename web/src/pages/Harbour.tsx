import { BookOpen, Lock, Pause, Play, ShieldAlert } from "lucide-react";
import { Link } from "react-router-dom";
import { DAYS, Device, Overview, OverviewProfile, Policy, Profile, api, useData } from "../api";
import { AlertRow } from "./Alerts";
import { DayStrip, WeekBars } from "../components/charts";
import { Avatar, DeviceIcon, Empty, PauseControl, useApp } from "../components/ui";
import { CATEGORY_LABEL, catColor, clock, dayLabel, duration, houseHour, houseWeekday, lockSentence } from "../lib/format";

const hm = (s: string) => { const [h, m] = s.split(":").map(Number); return h + m / 60; };

/** Hours of today covered by bedtime: last night's tail and tonight's start. */
export function bedtimeSpans(policy: Policy | undefined): [number, number][] {
  if (!policy?.bedtime?.enabled) return [];
  const idx = houseWeekday();
  const today = policy.bedtime.days[DAYS[idx]];
  const yesterday = policy.bedtime.days[DAYS[(idx + 6) % 7]];
  const spans: [number, number][] = [];
  if (yesterday && hm(yesterday[1]) <= hm(yesterday[0]) && hm(yesterday[1]) > 0) spans.push([0, hm(yesterday[1])]);
  if (today) spans.push(hm(today[1]) <= hm(today[0]) ? [hm(today[0]), 24] : [hm(today[0]), hm(today[1])]);
  return spans;
}

function StateLine({ p }: { p: OverviewProfile }) {
  if (p.locked) return <span className="state"><Lock />{lockSentence(p.lock_reason, p.lock_until)}</span>;
  if (p.grant_until * 1000 > Date.now()) return <span className="state"><span className="dot on" />Extra time until {clock(p.grant_until)}</span>;
  if (!p.devices.length) return <span className="state">No devices yet</span>;
  return <span className="state"><span className={`dot ${p.online ? "on" : ""}`} />{p.online ? "Online now" : "Offline"}</span>;
}

function DevicePills({ devices }: { devices: Device[] }) {
  return (
    <>
      {devices.map((d) => (
        <span key={d.id} className={`device-pill ${d.locked ? "locked" : ""}`} title={d.online ? "Online" : "Offline"}>
          <DeviceIcon kind={d.kind} />{d.name}
          {d.locked ? <Lock aria-label="Paused" /> : <span className={`dot ${d.online ? "on" : ""}`} aria-label={d.online ? "online" : "offline"} />}
        </span>
      ))}
    </>
  );
}

function Berth({ p, policy, weekStarts, reload }: { p: OverviewProfile; policy?: Policy; weekStarts: number[]; reload: () => void }) {
  const limit = p.limit_minutes;
  const pct = limit ? Math.min(100, (p.used_seconds / (limit * 60)) * 100) : 0;
  return (
    <article className="berth">
      <div className="berth-who">
        <Avatar name={p.name} color={p.color} size="lg" />
        <div>
          <Link to={`/people/${p.id}`} className="berth-name">{p.name}</Link>
          <div><StateLine p={p} /></div>
        </div>
      </div>
      <div className="berth-time">
        <strong>{duration(p.used_seconds)}</strong>
        <span className="of">{limit ? `of ${duration(limit * 60)} today` : "today"}</span>
        {limit ? (
          <div className={`meter ${pct >= 100 ? "over" : ""}`} role="meter" aria-valuemin={0} aria-valuemax={limit} aria-valuenow={Math.round(p.used_seconds / 60)}
            aria-label={`${duration(p.used_seconds)} of ${duration(limit * 60)} used`}><i style={{ width: `${pct}%` }} /></div>
        ) : <div style={{ height: 8 }} />}
        <div className="berth-cats">
          {p.categories.slice().sort((a, b) => b.seconds - a.seconds).slice(0, 3).map((c) => (
            <span className="key" key={c.category}><i style={{ background: catColor(c.category) }} />{CATEGORY_LABEL[c.category]} {duration(c.seconds)}</span>
          ))}
          {!p.categories.length && <span>Nothing yet today</span>}
        </div>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 3fr) minmax(0, 1fr)", gap: 20, alignItems: "end" }}>
        <DayStrip hours={p.hours} bedtime={bedtimeSpans(policy)} nowHour={houseHour()} label={p.name} />
        <WeekBars values={p.week} starts={weekStarts} limit={limit} />
      </div>
      <div className="berth-actions">
        <PauseControl target="profile" id={p.id} locked={p.locked} reason={p.lock_reason} profileId={p.id} onDone={reload} />
      </div>
      <div className="berth-foot">
        <DevicePills devices={p.devices} />
        {p.homework && <span className="tag"><BookOpen />Homework time</span>}
        {p.blocked > 0 && <Link to={`/people/${p.id}?tab=blocked`} className="tag">{p.blocked} blocked today</Link>}
        {p.alerts > 0 && <Link to="/alerts" className="tag warn"><ShieldAlert />{p.alerts} to look at</Link>}
      </div>
    </article>
  );
}

function QuietBerth({ p, reload }: { p: OverviewProfile; reload: () => void }) {
  const tier = { strict: "Family filter", basic: "Ads and malware blocked", none: "No filtering" }[p.tier];
  const mon = { full: "full monitoring", sites: "sites only", off: "not monitored" }[p.monitoring];
  return (
    <article className="berth quiet">
      <div className="berth-who">
        <Avatar name={p.name} color={p.color} />
        <div>
          <Link to={`/people/${p.id}/rules`} className="berth-name" style={{ fontSize: 19 }}>{p.name}</Link>
          <div className="small soft">{tier}, {mon}</div>
        </div>
      </div>
      <div className="berth-foot" style={{ gridColumn: "auto" }}>
        <DevicePills devices={p.devices} />
        {!p.devices.length && <span className="small soft">No devices yet</span>}
      </div>
      <div className="berth-actions">
        {p.devices.length > 0 && <PauseControl target="profile" id={p.id} locked={p.locked} reason={p.lock_reason} profileId={p.id} onDone={reload} compact />}
      </div>
    </article>
  );
}

export default function Harbour() {
  const { run } = useApp();
  const { data, reload } = useData<Overview>("/api/overview", 15000);
  const { data: profiles } = useData<Profile[]>("/api/profiles");
  if (!data) return null;
  const policyOf = (id: number) => profiles?.find((x) => x.id === id)?.policy;
  const kids = data.profiles.filter((p) => p.kind === "kid");
  const others = data.profiles.filter((p) => p.kind !== "kid");
  const withDevices = kids.filter((k) => k.devices.length);
  const allPaused = withDevices.length > 0 && withDevices.every((k) => k.locked && k.lock_reason === "manual");
  const everyone = async (path: string, done: string) => {
    if (await run(() => Promise.all(withDevices.map((k) => api(path, { body: { target: "profile", id: k.id } }))), done)) reload();
  };
  const online = data.profiles.flatMap((p) => p.devices).filter((d) => d.online).length;
  const fresh = data.alerts.filter((a) => !a.acked);
  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>{dayLabel(Date.now() / 1000, { weekday: "long", month: "long", day: "numeric" })}</h1>
          <p>{online === 0 ? "Nobody is online right now." : `${online} ${online === 1 ? "device is" : "devices are"} online.`}</p>
        </div>
        {withDevices.length > 0 && (allPaused
          ? <button className="btn primary" onClick={() => everyone("/api/unlock", "Internet resumed for the kids")}><Play />Resume all kids</button>
          : <button className="btn" onClick={() => everyone("/api/lock", "All kids paused until you resume")}><Pause />Pause all kids</button>)}
      </div>

      {kids.length > 0 ? (
        <section className="berths" aria-label="Kids">
          {kids.map((p) => <Berth key={p.id} p={p} policy={policyOf(p.id)} weekStarts={data.week_starts} reload={reload} />)}
        </section>
      ) : (
        <section className="panel"><Empty title="No kids set up yet">
          <p>Add each child under <Link to="/people">People</Link>, then assign their devices under <Link to="/devices">Devices</Link>.</p>
        </Empty></section>
      )}

      <div className="cols">
        <section className="panel">
          <div className="panel-head">
            <h2>Needs a look</h2>
            <Link to="/alerts" className="small">All alerts</Link>
          </div>
          {fresh.length ? <div className="rows">{fresh.slice(0, 6).map((a) => <AlertRow key={a.id} a={a} onChange={reload} />)}</div>
            : <Empty title="All clear">Nothing has needed your attention.</Empty>}
        </section>
        <section className="panel">
          <div className="panel-head"><h2>New devices</h2><Link to="/devices" className="small">All devices</Link></div>
          {data.unassigned.length ? (
            <div className="rows">
              {data.unassigned.map((d) => (
                <div className="row" key={d.id}>
                  <span className="alert-icon"><DeviceIcon kind={d.kind} /></span>
                  <div className="grow">
                    <div className="title">{d.name}</div>
                    <div className="sub">{d.tier === "strict" ? "Held to the kids' filter until you say whose it is" : "Treated as a guest"}</div>
                  </div>
                  <Link to={`/devices?open=${d.id}`} className="btn sm">Assign</Link>
                </div>
              ))}
            </div>
          ) : <Empty title="Nothing new">Every device on the network belongs to someone.</Empty>}
        </section>
      </div>

      {others.length > 0 && (
        <section className="berths" aria-label="Grown-ups and shared devices">
          {others.map((p) => <QuietBerth key={p.id} p={p} reload={reload} />)}
        </section>
      )}
    </div>
  );
}
