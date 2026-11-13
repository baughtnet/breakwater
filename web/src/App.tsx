import { Anchor, Bell, MonitorSmartphone, Settings2, Users } from "lucide-react";
import { FormEvent, useCallback, useEffect, useState } from "react";
import { BrowserRouter, NavLink, Navigate, Route, Routes } from "react-router-dom";
import { Session, api, useData } from "./api";
import { AppProvider, Logo } from "./components/ui";
import Alerts from "./pages/Alerts";
import Devices from "./pages/Devices";
import Harbour from "./pages/Harbour";
import People from "./pages/People";
import Person from "./pages/Person";
import SystemPage from "./pages/System";
import { setHouseOffset } from "./lib/format";

function Gate({ session, onDone }: { session: Session; onDone: () => void }) {
  const setup = session.setup_needed;
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api(setup ? "/api/setup" : "/api/login", { body: { username, password } });
      onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not sign in.");
    } finally {
      setBusy(false);
    }
  };
  const standbySetup = setup && session.role !== "primary";
  return (
    <div className="gate">
      <div className="gate-card">
        <div className="wordmark"><Logo />Breakwater</div>
        <p className="soft">
          {standbySetup ? "This is a standby node. Create the parent account on the primary node; it will appear here within a few seconds."
            : setup ? "Create the parent account. Kids' devices can't open this page, but choose a password they won't guess."
            : "Sign in to see how everyone is doing."}
        </p>
        {!standbySetup && (
          <form onSubmit={submit}>
            <label className="field"><span>Username</span>
              <input className="input" value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" autoFocus required />
            </label>
            <label className="field"><span>Password</span>
              <input className="input" type="password" value={password} onChange={(e) => setPassword(e.target.value)}
                autoComplete={setup ? "new-password" : "current-password"} minLength={setup ? 10 : undefined} required />
              {setup && <small>At least 10 characters.</small>}
            </label>
            {error && <p className="error-text" role="alert">{error}</p>}
            <button className="btn primary" disabled={busy}>{setup ? "Create account" : "Sign in"}</button>
          </form>
        )}
      </div>
    </div>
  );
}

const NAV = [
  { to: "/", label: "Harbour", icon: Anchor, end: true },
  { to: "/people", label: "People", icon: Users },
  { to: "/devices", label: "Devices", icon: MonitorSmartphone },
  { to: "/system", label: "System", icon: Settings2 },
];

function Shell({ session }: { session: Session }) {
  const { data: unacked, reload } = useData<{ unacked: number }>("/api/alerts/count", 20000);
  useEffect(() => {
    const h = () => reload();
    window.addEventListener("bw:alerts-changed", h);
    return () => window.removeEventListener("bw:alerts-changed", h);
  }, [reload]);
  const count = unacked?.unacked ?? 0;
  return (
    <div className="shell">
      <header className="topbar">
        <NavLink to="/" className="wordmark"><Logo />Breakwater</NavLink>
        <nav className="nav" aria-label="Main">
          {NAV.map((n) => <NavLink key={n.to} to={n.to} end={n.end}>{n.label}</NavLink>)}
        </nav>
        <div className="topbar-right">
          <NavLink to="/alerts" className="btn icon quiet bell" aria-label={count ? `Alerts, ${count} new` : "Alerts"}>
            <Bell />{count > 0 && <span className="count">{count > 99 ? "99+" : count}</span>}
          </NavLink>
        </div>
      </header>
      {session.role !== "primary" && (
        <div className="banner" role="status">
          You're on the standby node <strong>{session.node_id}</strong>. Pausing and resuming work here; rules and settings are edited on the primary.
        </div>
      )}
      <main className="main">
        <Routes>
          <Route path="/" element={<Harbour />} />
          <Route path="/people" element={<People />} />
          <Route path="/people/:id" element={<Person />} />
          <Route path="/people/:id/rules" element={<People />} />
          <Route path="/devices" element={<Devices />} />
          <Route path="/alerts" element={<Alerts />} />
          <Route path="/system" element={<SystemPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
      <nav className="bottomnav" aria-label="Main">
        {NAV.map((n) => <NavLink key={n.to} to={n.to} end={n.end}><n.icon />{n.label}</NavLink>)}
      </nav>
    </div>
  );
}

export default function App() {
  const [session, setSession] = useState<Session | null>(null);
  const [failed, setFailed] = useState("");
  const load = useCallback(() => {
    api<Session>("/api/session").then((s) => { setHouseOffset(s.utc_offset); setSession(s); }).catch((e) => setFailed(e instanceof Error ? e.message : "Unreachable"));
  }, []);
  useEffect(() => {
    load();
    window.addEventListener("bw:signed-out", load);
    return () => window.removeEventListener("bw:signed-out", load);
  }, [load]);
  if (failed && !session) return <div className="gate"><div className="gate-card"><div className="wordmark"><Logo />Breakwater</div><p className="error-text">{failed}</p></div></div>;
  if (!session) return null;
  if (!session.authenticated) return <Gate session={session} onDone={load} />;
  return (
    <BrowserRouter>
      <AppProvider session={session}>
        <Shell session={session} />
      </AppProvider>
    </BrowserRouter>
  );
}
