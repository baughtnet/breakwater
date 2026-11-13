import { ChevronLeft, ChevronRight, Lock, SlidersHorizontal } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { Activity, DAYS, Overview, Profile, WebEvent, useData } from "../api";
import { CategoryLegend, ChartPanel, Column, Heatmap, SegmentedBar, StackedColumns } from "../components/charts";
import { Avatar, Empty, PauseControl, SiteIcon } from "../components/ui";
import { CATEGORY_LABEL, CATEGORY_ORDER, REASON_LABEL, bytes, clock, dayLabel, duration, hourLabel, houseToday, isoDay, lockSentence } from "../lib/format";

type Tab = "searches" | "videos" | "pages" | "blocked";
const KIND: Record<Exclude<Tab, "blocked">, string> = { searches: "search", videos: "video", pages: "visit" };

function useNarrow(): boolean {
  const [narrow, setNarrow] = useState(() => window.matchMedia("(max-width: 700px)").matches);
  useEffect(() => {
    const mq = window.matchMedia("(max-width: 700px)");
    const on = () => setNarrow(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return narrow;
}

function dayColumns(act: Activity, narrow: boolean): Column[] {
  const per = narrow ? 4 : 1; // quarter-hours per column
  const n = 96 / per;
  const cols: Column[] = Array.from({ length: n }, (_, i) => {
    const hour = (i * per) / 4;
    const end = ((i + 1) * per) / 4;
    const fmt = (h: number) => {
      const mins = Math.round((h % 1) * 60);
      if (!mins) return hourLabel(Math.floor(h));
      const base = hourLabel(Math.floor(h));
      return base.includes(":") ? base.replace(":00", `:${String(mins).padStart(2, "0")}`)
        : `${base.slice(0, -1)}:${String(mins).padStart(2, "0")}${base.slice(-1)}`;
    };
    return { key: i, label: `${fmt(hour)} to ${fmt(end)}`, tick: hour % (narrow ? 6 : 3) === 0 ? hourLabel(hour) : undefined, values: {} };
  });
  for (const row of act.timeline) {
    const c = cols[Math.floor(row.slot / per)];
    if (!c) continue;
    for (const k of CATEGORY_ORDER) if (row[k]) c.values[k] = (c.values[k] ?? 0) + row[k];
  }
  return cols;
}

function EventList({ pid, tab, day }: { pid: number; tab: Exclude<Tab, "blocked">; day: string }) {
  const { data } = useData<WebEvent[]>(`/api/profiles/${pid}/events?kind=${KIND[tab]}&day=${day}`);
  if (!data) return null;
  if (!data.length) {
    return <Empty title={`No ${tab} recorded`}>Only devices with inspection switched on report {tab}.</Empty>;
  }
  return (
    <div className="rows">
      {data.map((e) => (
        <div className="row" key={e.id}>
          {tab === "videos" && e.vid && <img className="thumb" loading="lazy" alt="" src={`https://i.ytimg.com/vi/${e.vid}/mqdefault.jpg`} />}
          <div className="grow">
            {tab === "searches" && <><div className="title">{e.q}</div><div className="sub">{e.engine}</div></>}
            {tab === "videos" && <>
              <div className="title"><a href={`https://www.youtube.com/watch?v=${e.vid}`} target="_blank" rel="noreferrer noopener">{e.title || e.vid}</a></div>
              <div className="sub">{[e.channel, e.dur ? duration(e.dur) : ""].filter(Boolean).join(", ")}</div></>}
            {tab === "pages" && <><div className="title">{e.title || e.host}</div><div className="sub">{e.url}</div></>}
          </div>
          <span className="when">{clock(e.ts)}</span>
        </div>
      ))}
    </div>
  );
}

export default function Person() {
  const pid = Number(useParams().id);
  const [params, setParams] = useSearchParams();
  const today = houseToday();
  const day = params.get("day") ?? today;
  const tab = (params.get("tab") as Tab) ?? "searches";
  const narrow = useNarrow();
  const { data: profiles } = useData<Profile[]>("/api/profiles");
  const { data: overview, reload } = useData<Overview>("/api/overview", 20000);
  const { data: act, loading } = useData<Activity>(`/api/profiles/${pid}/activity?day=${day}`, day === today ? 30000 : 0);
  const profile = profiles?.find((p) => p.id === pid);
  const live = overview?.profiles.find((p) => p.id === pid);
  const setParam = (k: string, v: string | null) => {
    const next = new URLSearchParams(params);
    if (v === null) next.delete(k); else next.set(k, v);
    setParams(next, { replace: true });
  };
  const shift = (days: number) => {
    const d = new Date(`${day}T12:00:00`);
    d.setDate(d.getDate() + days);
    const iso = isoDay(d);
    setParam("day", iso >= today ? null : iso);
  };
  const columns = useMemo(() => (act?.timeline ? dayColumns(act, narrow) : []), [act, narrow]);
  if (!profile) return profiles ? <Empty title="No such person"><Link to="/people">Back to People</Link></Empty> : null;

  const weekday = DAYS[(new Date(`${day}T12:00:00`).getDay() + 6) % 7];
  const limitMin = profile.policy.daily_limit.enabled ? profile.policy.daily_limit.minutes[weekday] : null;
  const head = (
    <div className="page-head">
      <div style={{ display: "flex", gap: 16, alignItems: "center" }}>
        <Avatar name={profile.name} color={profile.color} size="lg" />
        <div>
          <h1>{profile.name}</h1>
          <p>{live?.locked ? <span className="state"><Lock />{lockSentence(live.lock_reason, live.lock_until)}</span>
            : <span className="state"><span className={`dot ${live?.online ? "on" : ""}`} />{live?.online ? "Online now" : "Offline"}</span>}</p>
        </div>
      </div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <Link className="btn" to={`/people/${pid}/rules`}><SlidersHorizontal />Rules</Link>
        {live && live.devices.length > 0 && <PauseControl target="profile" id={pid} locked={live.locked} reason={live.lock_reason} profileId={pid} onDone={reload} />}
      </div>
    </div>
  );

  if (profile.monitoring === "off" || act?.monitoring === "off") {
    return <div className="stack">{head}<section className="panel"><Empty title="Not monitored">
      <p>Nothing is recorded for {profile.name}. You can change that under <Link to={`/people/${pid}/rules`}>Rules</Link>.</p></Empty></section></div>;
  }

  const hourly = (h: number) => CATEGORY_ORDER.map((k) => (act?.timeline ?? []).filter((r) => Math.floor(r.slot / 4) === h).reduce((a, r) => a + (r[k] ?? 0), 0));
  const cats = act?.categories.map((c) => c.category) ?? [];
  const maxSite = Math.max(1, ...(act?.sites ?? []).map((s) => s.seconds));
  const ranked = (act?.sites ?? []).filter((s) => s.seconds >= 30).slice(0, 10);
  const full = profile.monitoring === "full";
  const tabs: Tab[] = full ? ["searches", "videos", "pages", "blocked"] : ["blocked"];
  const activeTab = tabs.includes(tab) ? tab : tabs[0];

  return (
    <div className="stack" style={{ opacity: loading && !act ? 0.6 : 1 }}>
      {head}
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <button className="btn icon" onClick={() => shift(-1)} aria-label="Previous day"><ChevronLeft /></button>
        <input className="input narrow" type="date" value={day} max={today} aria-label="Day" onChange={(e) => e.target.value && setParam("day", e.target.value >= today ? null : e.target.value)} />
        <button className="btn icon" onClick={() => shift(1)} disabled={day >= today} aria-label="Next day"><ChevronRight /></button>
        {day !== today && <button className="btn quiet" onClick={() => setParam("day", null)}>Today</button>}
      </div>

      {act && act.timeline && (
        <>
          <section className="panel stats">
            <div className="stat hero">
              <div className="label">Screen time</div>
              <div className="value">{duration(act.total_seconds)}</div>
              <div className="sub">{limitMin ? `Limit ${duration(limitMin * 60)}` : "No daily limit"}</div>
            </div>
            <div className="stat">
              <div className="label">Active between</div>
              <div className="value">{act.first_active ? `${clock(act.first_active)}` : "–"}</div>
              <div className="sub">{act.last_active ? `and ${clock(Math.min(act.last_active, Date.now() / 1000))}` : "No activity"}</div>
            </div>
            <div className="stat">
              <div className="label">Data used</div>
              <div className="value">{bytes(act.data_bytes)}</div>
              <div className="sub">Through the gateway</div>
            </div>
            <div className="stat">
              <div className="label">Blocked</div>
              <div className="value">{act.blocked.reduce((a, b) => a + b.count, 0)}</div>
              <div className="sub">{act.blocked.length ? `${new Set(act.blocked.map((b) => b.site)).size} sites or apps` : "Nothing blocked"}</div>
            </div>
          </section>

          <ChartPanel title="Through the day" subtitle={`${dayLabel(act.day_start, { weekday: "long", month: "long", day: "numeric" })}, time in use per ${narrow ? "hour" : "15 minutes"}`}
            table={
              <table className="data">
                <thead><tr><th>Hour</th>{CATEGORY_ORDER.filter((k) => cats.includes(k)).map((k) => <th className="n" key={k}>{CATEGORY_LABEL[k]}</th>)}</tr></thead>
                <tbody>{Array.from({ length: 24 }, (_, h) => ({ h, v: hourly(h) })).filter((r) => r.v.some((x) => x > 0)).map(({ h, v }) => (
                  <tr key={h}><td>{hourLabel(h)}</td>{CATEGORY_ORDER.map((k, i) => cats.includes(k) && <td className="n" key={k}>{v[i] ? duration(v[i]) : ""}</td>)}</tr>
                ))}</tbody>
              </table>}>
            {act.total_seconds > 0 ? <>
              <StackedColumns columns={columns} unit="minutes" ariaLabel="Time in use through the day, by kind of activity" />
              <CategoryLegend categories={cats} />
            </> : <Empty title="A quiet day">No screen time was recorded.</Empty>}
          </ChartPanel>

          <div className="cols even">
            <section className="panel">
              <div className="panel-head"><div><h2>Apps and sites</h2><p>Estimated from traffic and lookups</p></div></div>
              {ranked.length ? (
                <div className="ranked">
                  {ranked.map((s) => (
                    <div className="ranked-row" key={s.site}>
                      <SiteIcon site={s.site} label={s.label} />
                      <span className="name" title={s.label}>{s.label}</span>
                      <div><div className="bar" style={{ width: `${(s.seconds / maxSite) * 100}%` }} /></div>
                      <span className="num soft">{duration(s.seconds)}</span>
                    </div>
                  ))}
                </div>
              ) : <Empty title="Nothing to rank yet" />}
            </section>
            <div className="stack">
              {act.categories.length > 0 && (
                <section className="panel">
                  <div className="panel-head"><h2>The mix</h2></div>
                  <SegmentedBar parts={act.categories} />
                </section>
              )}
              <ChartPanel title="Last two weeks" subtitle={limitMin ? `Hours per day. The line marks the ${duration(limitMin * 60)} limit.` : "Hours per day"}
                table={<table className="data"><thead><tr><th>Day</th><th className="n">Screen time</th></tr></thead>
                  <tbody>{act.trend.map((t) => <tr key={t.date}><td>{dayLabel(t.day_start)}</td><td className="n">{duration(t.total)}</td></tr>)}</tbody></table>}>
                <StackedColumns height={170} unit="hours" limit={limitMin ? limitMin * 60 : null} ariaLabel="Screen time per day for the last two weeks"
                  columns={act.trend.map((t) => ({ key: t.date, label: dayLabel(t.day_start), tick: dayLabel(t.day_start, { weekday: "narrow" }),
                    emphasis: t.date === act.day, values: Object.fromEntries(CATEGORY_ORDER.map((k) => [k, Number(t[k] ?? 0)])) }))} />
              </ChartPanel>
            </div>
          </div>

          <ChartPanel title="Usual rhythm" subtitle="Typical screen time by weekday and hour, averaged over four weeks"
            table={<table className="data"><thead><tr><th>Hour</th>{["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((d) => <th className="n" key={d}>{d}</th>)}</tr></thead>
              <tbody>{Array.from({ length: 24 }, (_, h) => h).filter((h) => act.heatmap.some((r) => r[h] >= 30)).map((h) => (
                <tr key={h}><td>{hourLabel(h)}</td>{act.heatmap.map((r, d) => <td className="n" key={d}>{r[h] >= 30 ? duration(r[h]) : ""}</td>)}</tr>))}</tbody></table>}>
            <Heatmap grid={act.heatmap} />
          </ChartPanel>

          <section className="panel">
            <div className="tabs" role="tablist">
              {tabs.map((t) => (
                <button key={t} role="tab" aria-selected={activeTab === t} onClick={() => setParam("tab", t)}>
                  {{ searches: "Searches", videos: "Videos", pages: "Pages", blocked: "Blocked" }[t]}
                  {t === "blocked" && act.blocked.length > 0 && <span className="n">{act.blocked.length}</span>}
                </button>
              ))}
            </div>
            {activeTab === "blocked" ? (
              act.blocked.length ? (
                <div className="rows">
                  {act.blocked.map((b, i) => (
                    <div className="row" key={i}>
                      <SiteIcon site={b.site} label={b.label} />
                      <div className="grow"><div className="title">{b.label}</div><div className="sub">{REASON_LABEL[b.reason] ?? b.reason}{b.count > 1 ? `, ${b.count} attempts` : ""}</div></div>
                      <span className="when">{clock(b.ts)}</span>
                    </div>
                  ))}
                </div>
              ) : <Empty title="Nothing blocked">No attempts to reach blocked sites or apps on this day.</Empty>
            ) : <EventList pid={pid} tab={activeTab} day={act.day} />}
          </section>
        </>
      )}
    </div>
  );
}
