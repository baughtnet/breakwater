import { ReactNode, useEffect, useLayoutEffect, useRef, useState } from "react";
import { CATEGORY_LABEL, CATEGORY_ORDER, DARK_LABEL, catColor, dayLabel, duration, hourLabel } from "../lib/format";

/* Chart conventions (kept the same everywhere):
   thin bars with a rounded data end and a square baseline, 2px surface gaps between touching fills,
   hairline solid grid, text in ink tokens (never the series colour), hover and keyboard focus show the
   same tooltip, and every chart has a table twin. */

function useWidth<T extends HTMLElement>(): [React.RefObject<T>, number] {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    setWidth(el.clientWidth);
    const ro = new ResizeObserver(() => setWidth(el.clientWidth));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, width];
}

interface Tip {
  x: number;
  y: number;
  head: string;
  rows: { color?: string; label: string; value: string }[];
}

function Tooltip({ tip }: { tip: Tip | null }) {
  if (!tip) return null;
  return (
    <div className="tooltip" style={{ left: tip.x, top: tip.y }} role="status">
      <div className="t-head">{tip.head}</div>
      {tip.rows.map((r, i) => (
        <div className="t-row" key={i}>
          {r.color && <i style={{ background: r.color }} />}
          <span>{r.label}</span>
          <b>{r.value}</b>
        </div>
      ))}
    </div>
  );
}

/** A bar with a rounded top (the data end) and a square bottom (the baseline). */
function barPath(x: number, y: number, w: number, h: number, r: number): string {
  const rr = Math.max(0, Math.min(r, w / 2, h));
  return `M${x},${y + h}V${y + rr}Q${x},${y} ${x + rr},${y}H${x + w - rr}Q${x + w},${y} ${x + w},${y + rr}V${y + h}Z`;
}

function niceMax(v: number, steps: number[]): number {
  return steps.find((s) => s >= v) ?? steps[steps.length - 1];
}

// --------------------------------------------------------------- day strip
/** One child's day at a glance: 24 hourly columns over a baseline; bedtime is the solid wall under it. */
export function DayStrip({ hours, bedtime, nowHour, label }: {
  hours: number[]; bedtime: [number, number][]; nowHour: number | null; label: string;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [tip, setTip] = useState<Tip | null>(null);
  const H = 56, base = 44, w = Math.max(width, 120);
  const band = w / 24;
  const bw = Math.min(24, Math.max(2, band - 2));
  const max = Math.max(3600, ...hours);
  const total = hours.reduce((a, b) => a + b, 0);
  return (
    <div className="chart" ref={ref}>
      <svg viewBox={`0 0 ${w} ${H + 14}`} height={H + 14} role="img"
        aria-label={`${label}: ${duration(total, "long")} today, by hour`}>
        <line className="axis" x1={0} x2={w} y1={base + 0.5} y2={base + 0.5} />
        {bedtime.map(([a, b], i) => (
          <rect key={i} x={a * band} y={base + 3} width={(b - a) * band} height={4} rx={2} fill="var(--ink-2)" opacity={0.55}>
            <title>Bedtime</title>
          </rect>
        ))}
        {hours.map((s, h) => {
          const bh = s > 0 ? Math.max(2, (s / max) * (base - 4)) : 0;
          const x = h * band + (band - bw) / 2;
          return (
            <g key={h}>
              {bh > 0 && <path d={barPath(x, base - bh, bw, bh, 3)} fill="var(--series)" />}
              <rect className="hit" x={h * band} y={0} width={band} height={H} tabIndex={s > 0 ? 0 : -1}
                aria-label={`${hourLabel(h)}: ${duration(s)}`}
                onPointerEnter={() => setTip({ x: h * band + band / 2, y: base - bh, head: `${hourLabel(h)} to ${hourLabel(h + 1)}`,
                  rows: [{ label: "In use", value: duration(s) }] })}
                onFocus={() => setTip({ x: h * band + band / 2, y: base - bh, head: `${hourLabel(h)} to ${hourLabel(h + 1)}`,
                  rows: [{ label: "In use", value: duration(s) }] })}
                onPointerLeave={() => setTip(null)} onBlur={() => setTip(null)} />
            </g>
          );
        })}
        {nowHour !== null && <line x1={nowHour * band} x2={nowHour * band} y1={2} y2={base + 9} stroke="var(--ink)" strokeWidth={1.5} />}
        {[0, 6, 12, 18].map((h) => (
          <text key={h} x={h * band + 1} y={H + 11}>{hourLabel(h)}</text>
        ))}
      </svg>
      <Tooltip tip={tip} />
    </div>
  );
}

// ------------------------------------------------------- stacked columns
export interface Column {
  key: string | number;
  label: string;          // tooltip heading
  tick?: string;          // x-axis label, if this column gets one
  values: Record<string, number>; // seconds per category
  emphasis?: boolean;
}

export function StackedColumns({ columns, height = 220, unit, limit, ariaLabel }: {
  columns: Column[]; height?: number; unit: "minutes" | "hours"; limit?: number | null; ariaLabel: string;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [tip, setTip] = useState<Tip | null>(null);
  const [hover, setHover] = useState<number | null>(null);
  const padL = 34, padB = 22, padT = 8;
  const w = Math.max(width, 200), plotW = w - padL, plotH = height - padB - padT;
  const totals = columns.map((c) => Object.values(c.values).reduce((a, b) => a + b, 0));
  const dataMax = Math.max(...totals, limit ?? 0, 1);
  const per = unit === "minutes" ? 60 : 3600;
  const steps = unit === "minutes" ? [5, 10, 15, 20, 30, 45, 60, 90, 120] : [1, 2, 3, 4, 6, 8, 10, 12, 16, 24];
  const top = niceMax(dataMax / per, steps);
  const ticks = [0, top / 2, top];
  const y = (sec: number) => padT + plotH - (sec / (top * per)) * plotH;
  const band = plotW / Math.max(columns.length, 1);
  const bw = Math.min(24, Math.max(2, band - 2));
  const show = (i: number) => {
    const c = columns[i];
    const rows = CATEGORY_ORDER.filter((k) => c.values[k] > 0).map((k) => ({ color: catColor(k), label: CATEGORY_LABEL[k], value: duration(c.values[k]) }));
    setHover(i);
    setTip({ x: padL + i * band + band / 2, y: y(totals[i]), head: c.label,
      rows: rows.length ? [...rows, ...(rows.length > 1 ? [{ label: "Total", value: duration(totals[i]) }] : [])] : [{ label: "Nothing", value: "0m" }] });
  };
  const hide = () => { setTip(null); setHover(null); };
  return (
    <div className="chart" ref={ref}>
      <svg viewBox={`0 0 ${w} ${height}`} height={height} role="img" aria-label={ariaLabel}>
        {ticks.map((t) => (
          <g key={t}>
            <line className={t === 0 ? "axis" : "gridline"} x1={padL} x2={w} y1={Math.round(y(t * per)) + 0.5} y2={Math.round(y(t * per)) + 0.5} />
            <text x={padL - 6} y={y(t * per) + 4} textAnchor="end">{Number.isInteger(t) ? t : t.toFixed(1)}{unit === "minutes" ? "m" : "h"}</text>
          </g>
        ))}
        {limit ? <line x1={padL} x2={w} y1={Math.round(y(limit)) + 0.5} y2={Math.round(y(limit)) + 0.5} stroke="var(--ink-2)" strokeWidth={1.5} /> : null}
        {columns.map((c, i) => {
          const x = padL + i * band + (band - bw) / 2;
          let acc = 0;
          const present = CATEGORY_ORDER.filter((k) => c.values[k] > 0);
          return (
            <g key={c.key} opacity={hover === null || hover === i ? 1 : 0.55}>
              {present.map((k, n) => {
                const y1 = y(acc), y0 = y(acc + c.values[k]);
                acc += c.values[k];
                const isTop = n === present.length - 1;
                const gap = n > 0 && y1 - y0 > 4 ? 2 : 0; // 2px surface gap between stacked fills
                const h = Math.max(1, y1 - y0 - gap);
                return isTop
                  ? <path key={k} d={barPath(x, y0, bw, h, 3)} fill={catColor(k)} />
                  : <rect key={k} x={x} y={y0} width={bw} height={h} fill={catColor(k)} />;
              })}
              {c.tick && <text x={padL + i * band + band / 2} y={height - 5} textAnchor="middle"
                fill={c.emphasis ? "var(--ink)" : undefined} fontWeight={c.emphasis ? 650 : undefined}>{c.tick}</text>}
              <rect className="hit" x={padL + i * band} y={padT} width={band} height={plotH} tabIndex={totals[i] > 0 ? 0 : -1}
                aria-label={`${c.label}: ${duration(totals[i])}`}
                onPointerEnter={() => show(i)} onFocus={() => show(i)} onPointerLeave={hide} onBlur={hide} />
            </g>
          );
        })}
      </svg>
      <Tooltip tip={tip} />
    </div>
  );
}

export function CategoryLegend({ categories }: { categories: string[] }) {
  const present = CATEGORY_ORDER.filter((c) => categories.includes(c));
  if (present.length < 2) return null;
  return (
    <div className="legend">
      {present.map((c) => (
        <span className="key" key={c}><i style={{ background: catColor(c) }} />{CATEGORY_LABEL[c]}</span>
      ))}
    </div>
  );
}

// ------------------------------------------------------------------ week
export function WeekBars({ values, starts, limit }: { values: number[]; starts: number[]; limit?: number | null }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [tip, setTip] = useState<Tip | null>(null);
  const H = 56, base = 44, w = Math.max(width, 80);
  const band = w / values.length, bw = Math.min(18, band - 4);
  const max = Math.max(3600, ...values, (limit ?? 0) * 60);
  const last = values.length - 1;
  const name = (i: number) => dayLabel(starts[i], { weekday: "long" });
  return (
    <div className="chart" ref={ref}>
      <svg viewBox={`0 0 ${w} ${H + 14}`} height={H + 14} role="img" aria-label="Screen time for the last seven days">
        <line className="axis" x1={0} x2={w} y1={base + 0.5} y2={base + 0.5} />
        {values.map((s, i) => {
          const bh = s > 0 ? Math.max(2, (s / max) * (base - 4)) : 0;
          const x = i * band + (band - bw) / 2;
          const show = () => setTip({ x: i * band + band / 2, y: base - bh, head: i === last ? "Today" : name(i), rows: [{ label: "In use", value: duration(s) }] });
          return (
            <g key={i}>
              {bh > 0 && <path d={barPath(x, base - bh, bw, bh, 3)} fill={i === last ? "var(--series)" : "var(--series-quiet)"} />}
              <text x={i * band + band / 2} y={H + 11} textAnchor="middle" fill={i === last ? "var(--ink)" : undefined}>
                {dayLabel(starts[i], { weekday: "narrow" })}
              </text>
              <rect className="hit" x={i * band} y={0} width={band} height={H} tabIndex={0} aria-label={`${name(i)}: ${duration(s)}`}
                onPointerEnter={show} onFocus={show} onPointerLeave={() => setTip(null)} onBlur={() => setTip(null)} />
            </g>
          );
        })}
      </svg>
      <Tooltip tip={tip} />
    </div>
  );
}

// --------------------------------------------------------------- heatmap
const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

export function Heatmap({ grid }: { grid: number[][] }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [tip, setTip] = useState<Tip | null>(null);
  const padL = 34, padT = 16;
  const w = Math.max(width, 240);
  const cell = (w - padL) / 24;
  const ch = Math.min(26, Math.max(14, cell));
  const max = Math.max(60, ...grid.flat());
  const level = (v: number) => (v < 30 ? 0 : Math.min(6, 1 + Math.floor((v / max) * 5.999)));
  return (
    <div>
      <div className="chart" ref={ref}>
        <svg viewBox={`0 0 ${w} ${padT + ch * 7 + 2}`} height={padT + ch * 7 + 2} role="img"
          aria-label="Typical screen time by weekday and hour, averaged over four weeks">
          {[0, 3, 6, 9, 12, 15, 18, 21].map((h) => <text key={h} x={padL + h * cell + 1} y={11}>{hourLabel(h)}</text>)}
          {grid.map((row, d) => (
            <g key={d}>
              <text x={0} y={padT + d * ch + ch / 2 + 4}>{DAY_NAMES[d]}</text>
              {row.map((v, h) => {
                const show = () => setTip({ x: padL + h * cell + cell / 2, y: padT + d * ch, head: `${DAY_NAMES[d]}, ${hourLabel(h)} to ${hourLabel(h + 1)}`,
                  rows: [{ label: "Typical", value: duration(v) }] });
                return (
                  <rect key={h} x={padL + h * cell + 1} y={padT + d * ch + 1} width={Math.max(1, cell - 2)} height={ch - 2} rx={3}
                    fill={`var(--heat-${level(v)})`} tabIndex={v >= 30 ? 0 : -1} aria-label={`${DAY_NAMES[d]} ${hourLabel(h)}: ${duration(v)}`}
                    onPointerEnter={show} onFocus={show} onPointerLeave={() => setTip(null)} onBlur={() => setTip(null)} />
                );
              })}
            </g>
          ))}
        </svg>
        <Tooltip tip={tip} />
      </div>
      <div className="legend" aria-hidden="true">
        <span>Less</span>
        <span style={{ display: "inline-flex", gap: 2 }}>
          {[0, 1, 2, 3, 4, 5, 6].map((l) => <i key={l} style={{ width: 16, height: 10, borderRadius: 2, background: `var(--heat-${l})` }} />)}
        </span>
        <span>More (up to {duration(max)} in an hour)</span>
      </div>
    </div>
  );
}

// ------------------------------------------------------ part-to-whole bar
export function SegmentedBar({ parts }: { parts: { category: string; label: string; seconds: number }[] }) {
  const total = parts.reduce((a, p) => a + p.seconds, 0);
  const refs = useRef<(HTMLDivElement | null)[]>([]);
  const [fits, setFits] = useState<boolean[]>([]);
  // Only print a share inside a block when it genuinely fits; otherwise the legend and table carry it.
  useEffect(() => {
    const measure = () => setFits(refs.current.map((el) => !!el && el.clientWidth >= 46));
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [parts.length, total]);
  if (!total) return null;
  const ordered = CATEGORY_ORDER.map((c) => parts.find((p) => p.category === c)).filter((p): p is NonNullable<typeof p> => !!p && p.seconds > 0);
  return (
    <div>
      <div className="segbar" role="img" aria-label={ordered.map((p) => `${p.label} ${Math.round((p.seconds / total) * 100)}%`).join(", ")}>
        {ordered.map((p, i) => (
          <div key={p.category} ref={(el) => (refs.current[i] = el)} className={DARK_LABEL.has(p.category) ? "dark-label" : ""}
            style={{ flex: `${p.seconds} 0 0`, background: catColor(p.category) }} title={`${p.label}: ${duration(p.seconds)}`}>
            {fits[i] ? `${Math.round((p.seconds / total) * 100)}%` : ""}
          </div>
        ))}
      </div>
      <div className="legend">
        {ordered.map((p) => <span className="key" key={p.category}><i style={{ background: catColor(p.category) }} />{p.label}</span>)}
      </div>
    </div>
  );
}

// -------------------------------------------------------------- chart card
export function ChartPanel({ title, subtitle, children, table, tools }: {
  title: string; subtitle?: ReactNode; children: ReactNode; table?: ReactNode; tools?: ReactNode;
}) {
  const [asTable, setAsTable] = useState(false);
  return (
    <section className="panel">
      <div className="panel-head">
        <div>
          <h2>{title}</h2>
          {subtitle && <p>{subtitle}</p>}
        </div>
        <div className="chart-tools">
          {tools}
          {table && (
            <button className="btn sm quiet" onClick={() => setAsTable(!asTable)} aria-pressed={asTable}>
              {asTable ? "Show chart" : "Show table"}
            </button>
          )}
        </div>
      </div>
      {asTable && table ? <div className="table-scroll">{table}</div> : children}
    </section>
  );
}
