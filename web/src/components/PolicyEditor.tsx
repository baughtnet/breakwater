import { Ban, Search } from "lucide-react";
import { useMemo, useState } from "react";
import { DAYS, Day, Policy } from "../api";
import { Seg, SiteIcon, Switch, SwitchRow, TagInput, useApp } from "./ui";

export type SectionKey = "search" | "apps" | "bedtime" | "homework" | "limit" | "sites" | "keywords";
export const ALL_SECTIONS: SectionKey[] = ["search", "apps", "bedtime", "homework", "limit", "sites", "keywords"];

const DAY_NAME: Record<Day, string> = { mon: "Mon", tue: "Tue", wed: "Wed", thu: "Thu", fri: "Fri", sat: "Sat", sun: "Sun" };
const GROUP_NAME: Record<string, string> = {
  social_network: "Social media", streaming: "Video and music", gaming: "Games", messenger: "Messaging", ai: "AI assistants",
  shopping: "Shopping", dating: "Dating", gambling: "Gambling", hosting: "File sharing", privacy: "Privacy tools", software: "App stores",
};
const GROUP_ORDER = Object.keys(GROUP_NAME);

function Apps({ value, onChange, disabled }: { value: string[]; onChange: (v: string[]) => void; disabled?: boolean }) {
  const { catalogue } = useApp();
  const [query, setQuery] = useState("");
  const set = useMemo(() => new Set(value), [value]);
  if (!catalogue) return null;
  const toggle = (id: string) => onChange(set.has(id) ? value.filter((v) => v !== id) : [...value, id]);
  const q = query.trim().toLowerCase();
  return (
    <div>
      <div style={{ position: "relative", maxWidth: 320 }}>
        <Search style={{ position: "absolute", left: 10, top: 10, width: 17, height: 17, color: "var(--muted)" }} aria-hidden="true" />
        <input className="input" style={{ paddingLeft: 34 }} placeholder="Find an app" aria-label="Find an app" value={query} onChange={(e) => setQuery(e.target.value)} />
      </div>
      {GROUP_ORDER.map((g) => {
        const group = catalogue.groups.find((x) => x.id === g);
        if (!group) return null;
        const whole = set.has(`group:${g}`);
        const services = catalogue.services.filter((s) => s.group === g && (!q || s.name.toLowerCase().includes(q)));
        if (!services.length) return null;
        const blocked = services.filter((s) => whole || set.has(s.id)).length;
        return (
          <div key={g}>
            <div className="app-group">
              <h3>{GROUP_NAME[g]} <span className="muted small" style={{ fontWeight: 400 }}>{blocked ? `${blocked} blocked` : ""}</span></h3>
              <label className="state">Block all<Switch checked={whole} onChange={() => toggle(`group:${g}`)} label={`Block all ${GROUP_NAME[g]}`} disabled={disabled} /></label>
            </div>
            <div className="apps">
              {services.map((s) => {
                const on = whole || set.has(s.id);
                return (
                  <button type="button" key={s.id} className="app" aria-pressed={on} disabled={disabled || whole} onClick={() => toggle(s.id)}
                    title={on ? `${s.name} is blocked` : `Block ${s.name}`}>
                    <SiteIcon site={`@${s.id}`} label={s.name} />
                    <span className="name">{s.name}</span>
                    {on && <Ban className="mark" aria-label="Blocked" />}
                  </button>
                );
              })}
            </div>
          </div>
        );
      })}
    </div>
  );
}

export function PolicyEditor({ policy, onChange, sections = ALL_SECTIONS, disabled, filtered = true }: {
  policy: Policy; onChange: (p: Policy) => void; sections?: SectionKey[]; disabled?: boolean; filtered?: boolean;
}) {
  const set = <K extends keyof Policy>(k: K, v: Policy[K]) => onChange({ ...policy, [k]: v });
  const has = (s: SectionKey) => sections.includes(s);
  const bed = policy.bedtime, hw = policy.homework, lim = policy.daily_limit;
  return (
    <>
      {has("search") && filtered && (
        <div className="section">
          <h3>Search and video</h3>
          <SwitchRow title="Safe search" hint="Forces the strict setting on Google, Bing, DuckDuckGo and others." checked={policy.safesearch}
            onChange={(v) => set("safesearch", v)} disabled={disabled} />
          <div className="switch-row">
            <div><div className="label">YouTube</div><div className="hint">Restricted Mode hides mature videos. Strict hides more, including comments.</div></div>
            <Seg label="YouTube restriction" value={policy.youtube} onChange={(v) => set("youtube", v)}
              options={[{ value: "off", label: "Open" }, { value: "moderate", label: "Restricted" }, { value: "strict", label: "Strict" }]} />
          </div>
        </div>
      )}

      {has("apps") && filtered && (
        <div className="section">
          <h3>Apps and services</h3>
          <p>Tap an app to block it everywhere, on every device this applies to.</p>
          <Apps value={policy.blocked_services} onChange={(v) => set("blocked_services", v)} disabled={disabled} />
        </div>
      )}

      {has("bedtime") && (
        <div className="section">
          <h3>Bedtime</h3>
          <p>The internet pauses at night and comes back in the morning. Each row is the night that starts on that day.</p>
          <SwitchRow title="Use a bedtime" checked={bed.enabled} onChange={(v) => set("bedtime", { ...bed, enabled: v })} disabled={disabled} />
          {bed.enabled && (
            <div className="week-grid">
              {DAYS.map((d) => {
                const w = bed.days[d];
                const put = (v: [string, string] | null) => set("bedtime", { ...bed, days: { ...bed.days, [d]: v } });
                return (
                  <div className="week-row" key={d}>
                    <label className="state"><input type="checkbox" checked={!!w} disabled={disabled} onChange={(e) => put(e.target.checked ? ["21:00", "07:00"] : null)} />{DAY_NAME[d]}</label>
                    {w ? (
                      <div className="times">
                        <input className="input" type="time" value={w[0]} disabled={disabled} aria-label={`${DAY_NAME[d]} bedtime starts`} onChange={(e) => e.target.value && put([e.target.value, w[1]])} />
                        <span className="soft">until</span>
                        <input className="input" type="time" value={w[1]} disabled={disabled} aria-label={`${DAY_NAME[d]} bedtime ends`} onChange={(e) => e.target.value && put([w[0], e.target.value])} />
                      </div>
                    ) : <span className="soft small">No bedtime</span>}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      {has("limit") && (
        <div className="section">
          <h3>Daily time limit</h3>
          <p>Counts time actually in use across all of this person's devices. When it runs out, the internet pauses until midnight.</p>
          <SwitchRow title="Use a daily limit" checked={lim.enabled} onChange={(v) => set("daily_limit", { ...lim, enabled: v })} disabled={disabled} />
          {lim.enabled && (
            <div className="week-grid">
              {DAYS.map((d) => (
                <div className="week-row" key={d}>
                  <span className="label">{DAY_NAME[d]}</span>
                  <div className="times">
                    <input className="input" type="number" min={0} max={1440} step={15} style={{ width: 96 }} disabled={disabled} value={lim.minutes[d] ?? ""}
                      placeholder="No limit" aria-label={`${DAY_NAME[d]} limit in minutes`}
                      onChange={(e) => set("daily_limit", { ...lim, minutes: { ...lim.minutes, [d]: e.target.value === "" ? null : Number(e.target.value) } })} />
                    <span className="soft">minutes</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {has("homework") && filtered && (
        <div className="section">
          <h3>Homework time</h3>
          <p>During these hours the internet stays on, but the kinds of apps you pick are blocked.</p>
          <SwitchRow title="Use homework time" checked={hw.enabled} onChange={(v) => set("homework", { ...hw, enabled: v })} disabled={disabled} />
          {hw.enabled && (
            <>
              <div className="chips" style={{ marginBottom: 12 }}>
                {["gaming", "streaming", "social_network", "messenger", "shopping"].map((g) => {
                  const id = `group:${g}`, on = hw.services.includes(id);
                  return <button type="button" key={g} className="app" style={{ width: "auto" }} aria-pressed={on} disabled={disabled}
                    onClick={() => set("homework", { ...hw, services: on ? hw.services.filter((s) => s !== id) : [...hw.services, id] })}>
                    <span className="name">{GROUP_NAME[g]}</span>{on && <Ban className="mark" aria-label="Blocked" />}</button>;
                })}
              </div>
              <div className="week-grid">
                {DAYS.map((d) => {
                  const w = hw.windows[d]?.[0];
                  const put = (v: [string, string] | null) => set("homework", { ...hw, windows: { ...hw.windows, [d]: v ? [v] : [] } });
                  return (
                    <div className="week-row" key={d}>
                      <label className="state"><input type="checkbox" checked={!!w} disabled={disabled} onChange={(e) => put(e.target.checked ? ["16:00", "18:00"] : null)} />{DAY_NAME[d]}</label>
                      {w ? (
                        <div className="times">
                          <input className="input" type="time" value={w[0]} disabled={disabled} aria-label={`${DAY_NAME[d]} homework starts`} onChange={(e) => e.target.value && put([e.target.value, w[1]])} />
                          <span className="soft">until</span>
                          <input className="input" type="time" value={w[1]} disabled={disabled} aria-label={`${DAY_NAME[d]} homework ends`} onChange={(e) => e.target.value && put([w[0], e.target.value])} />
                        </div>
                      ) : <span className="soft small">None</span>}
                    </div>
                  );
                })}
              </div>
            </>
          )}
        </div>
      )}

      {has("sites") && filtered && (
        <div className="section">
          <h3>Sites</h3>
          <p>Block or allow specific sites by address. An allowed site stays reachable even if a list would block it.</p>
          <div className="cols even">
            <div className="field"><span>Always block</span>
              <TagInput label="Site to block" placeholder="example.com" values={policy.block_domains} onChange={(v) => set("block_domains", v)} disabled={disabled} /></div>
            <div className="field"><span>Always allow</span>
              <TagInput label="Site to allow" placeholder="example.com" values={policy.allow_domains} onChange={(v) => set("allow_domains", v)} disabled={disabled} /></div>
          </div>
        </div>
      )}

      {has("keywords") && (
        <div className="section">
          <h3>Blocked words</h3>
          <p>Pages and searches whose address contains one of these words are blocked and raise an alert. Works on devices with inspection switched on.</p>
          <TagInput label="Word to block" placeholder="A word or phrase" values={policy.keywords} onChange={(v) => set("keywords", v)} disabled={disabled} />
        </div>
      )}
    </>
  );
}
