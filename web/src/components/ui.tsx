import {
  Gamepad2, Laptop, Monitor, MoreHorizontal, Moon, Pause, Play, Plus, Printer, Smartphone, Tablet, Timer, Tv, X,
} from "lucide-react";
import { ReactNode, createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { Catalogue, Session, api } from "../api";

// ------------------------------------------------------------------ context
interface AppCtx {
  session: Session;
  catalogue: Catalogue | null;
  readOnly: boolean; // standby node: settings can't be edited here
  toast: (message: string, kind?: "ok" | "error") => void;
  /** Run an action, toast its outcome, and report whether it worked. */
  run: (fn: () => Promise<unknown>, done?: string) => Promise<boolean>;
}
const Ctx = createContext<AppCtx | null>(null);
export const useApp = () => useContext(Ctx)!;

export function AppProvider({ session, children }: { session: Session; children: ReactNode }) {
  const [catalogue, setCatalogue] = useState<Catalogue | null>(null);
  const [toasts, setToasts] = useState<{ id: number; message: string; kind: string }[]>([]);
  useEffect(() => {
    api<Catalogue>("/api/catalogue").then(setCatalogue).catch(() => undefined);
  }, []);
  const toast = useCallback((message: string, kind: "ok" | "error" = "ok") => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, message, kind }]);
    window.setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), kind === "error" ? 6000 : 2600);
  }, []);
  const run = useCallback(async (fn: () => Promise<unknown>, done?: string) => {
    try {
      await fn();
      if (done) toast(done);
      return true;
    } catch (e) {
      toast(e instanceof Error ? e.message : "Something went wrong.", "error");
      return false;
    }
  }, [toast]);
  return (
    <Ctx.Provider value={{ session, catalogue, readOnly: session.role !== "primary", toast, run }}>
      {children}
      <div className="toasts" aria-live="polite">
        {toasts.map((t) => <div key={t.id} className={`toast ${t.kind === "error" ? "error" : ""}`}>{t.message}</div>)}
      </div>
    </Ctx.Provider>
  );
}

// ------------------------------------------------------------------ basics
export function Logo() {
  // A breakwater seen from above: the wall, and calm water behind it.
  return (
    <svg viewBox="0 0 32 32" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" aria-hidden="true">
      <path d="M4 9h17a7 7 0 0 1 7 7v0" />
      <path d="M4 17c2.5-2 5-2 7.500 0s5 2 7.500 0" opacity=".75" />
      <path d="M4 24c2.500-2 5-2 7.500 0s5 2 7.500 0 5-2 7.500 0" opacity=".45" />
    </svg>
  );
}

export function Avatar({ name, color, size = "" }: { name: string; color: string; size?: "" | "sm" | "lg" }) {
  return <span className={`avatar p-${color} ${size}`} aria-hidden="true">{name.trim().charAt(0).toUpperCase() || "?"}</span>;
}

export function Switch({ checked, onChange, label, disabled }: { checked: boolean; onChange: (v: boolean) => void; label: string; disabled?: boolean }) {
  return <button type="button" role="switch" className="switch" aria-checked={checked} aria-label={label} disabled={disabled} onClick={() => onChange(!checked)} />;
}

export function SwitchRow({ title, hint, checked, onChange, disabled }: { title: string; hint?: ReactNode; checked: boolean; onChange: (v: boolean) => void; disabled?: boolean }) {
  return (
    <div className="switch-row">
      <div>
        <div className="label">{title}</div>
        {hint && <div className="hint">{hint}</div>}
      </div>
      <Switch checked={checked} onChange={onChange} label={title} disabled={disabled} />
    </div>
  );
}

export function Seg<T extends string>({ value, options, onChange, label }: { value: T; options: { value: T; label: string }[]; onChange: (v: T) => void; label: string }) {
  return (
    <div className="seg" role="group" aria-label={label}>
      {options.map((o) => <button type="button" key={o.value} aria-pressed={value === o.value} onClick={() => onChange(o.value)}>{o.label}</button>)}
    </div>
  );
}

export function Choices<T extends string>({ value, options, onChange, disabled }: { value: T; options: { value: T; title: string; text: string }[]; onChange: (v: T) => void; disabled?: boolean }) {
  return (
    <div className="choices">
      {options.map((o) => (
        <button type="button" key={o.value} className="choice" aria-pressed={value === o.value} disabled={disabled} onClick={() => onChange(o.value)}>
          <strong>{o.title}</strong>
          <span>{o.text}</span>
        </button>
      ))}
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return <div className="empty"><strong>{title}</strong>{children}</div>;
}

const DEVICE_ICONS: Record<string, typeof Laptop> = { phone: Smartphone, tablet: Tablet, laptop: Laptop, desktop: Monitor, console: Gamepad2, tv: Tv, other: Printer };
export function DeviceIcon({ kind }: { kind: string }) {
  const Icon = DEVICE_ICONS[kind] ?? Monitor;
  return <Icon aria-hidden="true" />;
}

/** App icons come from the filter's service catalogue as monochrome SVG; drawn as a mask so they follow the theme. */
export function SiteIcon({ site, label }: { site: string; label: string }) {
  const { catalogue } = useApp();
  const svc = site.startsWith("@") ? catalogue?.services.find((s) => s.id === site.slice(1)) : undefined;
  if (svc?.icon) {
    const url = `url("data:image/svg+xml;base64,${svc.icon}")`;
    return <span className="svc-icon" aria-hidden="true"><span style={{ width: "100%", height: "100%", background: "currentColor",
      WebkitMaskImage: url, maskImage: url, WebkitMaskSize: "contain", maskSize: "contain", WebkitMaskRepeat: "no-repeat", maskRepeat: "no-repeat", WebkitMaskPosition: "center", maskPosition: "center" }} /></span>;
  }
  return <span className="svc-icon letter" aria-hidden="true">{label.charAt(0)}</span>;
}

// ----------------------------------------------------------------- overlays
export function Menu({ button, children, label }: { button: ReactNode; children: (close: () => void) => ReactNode; label: string }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", away); document.removeEventListener("keydown", esc); };
  }, [open]);
  return (
    <div className="menu-wrap" ref={ref}>
      <span onClick={() => setOpen(!open)} aria-haspopup="menu" aria-expanded={open} aria-label={label}>{button}</span>
      {open && <div className="menu" role="menu">{children(() => setOpen(false))}</div>}
    </div>
  );
}

export function Drawer({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  useEffect(() => {
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", esc);
    return () => document.removeEventListener("keydown", esc);
  }, [onClose]);
  return (
    <div className="scrim" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <aside className="drawer" role="dialog" aria-modal="true" aria-label={title}>
        <div className="drawer-head">
          <h2>{title}</h2>
          <button className="btn icon quiet" onClick={onClose} aria-label="Close"><X /></button>
        </div>
        {children}
      </aside>
    </div>
  );
}

// -------------------------------------------------------------- pause menu
/** Pause / resume for one device or a whole person. Works on standby nodes too. */
export function PauseControl({ target, id, locked, reason, profileId, onDone, compact }: {
  target: "device" | "profile"; id: number; locked: boolean; reason: string; profileId?: number | null; onDone: () => void; compact?: boolean;
}) {
  const { run } = useApp();
  const act = async (path: string, body: object, done: string) => {
    if (await run(() => api(path, { body }), done)) onDone();
  };
  const scheduled = locked && (reason === "bedtime" || reason === "limit");
  if (scheduled && profileId) {
    return (
      <Menu label="Give more time" button={<button className={`btn ${compact ? "sm" : ""}`}><Timer />More time</button>}>
        {(close) => [15, 30, 60].map((m) => (
          <button key={m} role="menuitem" onClick={() => { close(); act("/api/bonus", { profile_id: profileId, minutes: m }, `${m} more minutes given`); }}>
            <Timer />{m} more minutes
          </button>
        ))}
      </Menu>
    );
  }
  if (locked) {
    return <button className={`btn primary ${compact ? "sm" : ""}`} onClick={() => act("/api/unlock", { target, id }, "Internet resumed")}><Play />Resume</button>;
  }
  const pause = (close: () => void, body: object, done: string) => { close(); act("/api/lock", { target, id, ...body }, done); };
  return (
    <Menu label="Pause internet" button={<button className={`btn ${compact ? "sm" : ""}`}><Pause />Pause</button>}>
      {(close) => (
        <>
          <button role="menuitem" onClick={() => pause(close, { minutes: 30 }, "Paused for 30 minutes")}><Timer />For 30 minutes</button>
          <button role="menuitem" onClick={() => pause(close, { minutes: 60 }, "Paused for 1 hour")}><Timer />For 1 hour</button>
          <button role="menuitem" onClick={() => pause(close, { until: "morning" }, "Paused until morning")}><Moon />Until morning</button>
          <hr />
          <button role="menuitem" onClick={() => pause(close, {}, "Paused until you resume")}><Pause />Until I resume</button>
        </>
      )}
    </Menu>
  );
}

// ---------------------------------------------------------------- tag input
export function TagInput({ values, onChange, placeholder, label, disabled }: { values: string[]; onChange: (v: string[]) => void; placeholder: string; label: string; disabled?: boolean }) {
  const [text, setText] = useState("");
  const add = () => {
    const parts = text.split(/[,\n]/).map((s) => s.trim().toLowerCase()).filter(Boolean);
    if (parts.length) onChange([...new Set([...values, ...parts])]);
    setText("");
  };
  return (
    <div className="stack" style={{ gap: 8 }}>
      <div style={{ display: "flex", gap: 8 }}>
        <input className="input" value={text} placeholder={placeholder} aria-label={label} disabled={disabled}
          onChange={(e) => setText(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); add(); } }} />
        <button type="button" className="btn" onClick={add} disabled={disabled || !text.trim()}><Plus />Add</button>
      </div>
      {values.length > 0 && (
        <div className="chips">
          {values.map((v) => (
            <span className="chip" key={v}>{v}
              <button type="button" aria-label={`Remove ${v}`} disabled={disabled} onClick={() => onChange(values.filter((x) => x !== v))}><X /></button>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

export function MoreButton() {
  return <button className="btn icon quiet" aria-label="More actions"><MoreHorizontal /></button>;
}
