"use client";

import React from "react";

export type Tone = "info" | "warning" | "danger" | "success" | "neutral";

function cx(...parts: Array<string | false | undefined | null>): string {
  return parts.filter(Boolean).join(" ");
}

/* ── Typography ─────────────────────────────────────────────────────────── */

export function H1({ children }: { children: React.ReactNode }) {
  return <h1 className="h1">{children}</h1>;
}

export function H2({ children }: { children: React.ReactNode }) {
  return <h2 className="h2">{children}</h2>;
}

export function H3({ children }: { children: React.ReactNode }) {
  return <h3 className="h3">{children}</h3>;
}

export function Text({
  children,
  size = "normal",
  tone,
  weight,
  style,
}: {
  children: React.ReactNode;
  size?: "small" | "normal";
  tone?: "secondary" | "tertiary" | "quaternary";
  weight?: "semibold";
  style?: React.CSSProperties;
}) {
  return (
    <p
      className={cx(
        "text",
        size === "small" && "text-small",
        tone && `text-${tone}`,
        weight === "semibold" && "text-semibold",
      )}
      style={style}
    >
      {children}
    </p>
  );
}

/* ── Layout ─────────────────────────────────────────────────────────────── */

export function Stack({
  children,
  gap = 0,
  style,
}: {
  children: React.ReactNode;
  gap?: number;
  style?: React.CSSProperties;
}) {
  return (
    <div className="stack" style={{ gap, ...style }}>
      {children}
    </div>
  );
}

export function Row({
  children,
  gap = 0,
  wrap,
  align,
  style,
}: {
  children: React.ReactNode;
  gap?: number;
  wrap?: boolean;
  align?: "start" | "center" | "end";
  style?: React.CSSProperties;
}) {
  const alignItems =
    align === "center" ? "center" : align === "end" ? "flex-end" : undefined;
  return (
    <div className={cx("row", wrap && "row-wrap")} style={{ gap, alignItems, ...style }}>
      {children}
    </div>
  );
}

export function Spacer() {
  return <div className="spacer" />;
}

export function Grid({
  children,
  columns = 2,
  gap = 12,
}: {
  children: React.ReactNode;
  columns?: number;
  gap?: number;
}) {
  return (
    <div
      className="grid"
      style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))`, gap }}
    >
      {children}
    </div>
  );
}

export function Divider() {
  return <hr className="divider" />;
}

/* ── Button ─────────────────────────────────────────────────────────────── */

export function Button({
  children,
  variant = "secondary",
  onClick,
  disabled,
  title,
}: {
  children: React.ReactNode;
  variant?: "primary" | "secondary" | "ghost";
  onClick?: () => void;
  disabled?: boolean;
  title?: string;
}) {
  return (
    <button
      type="button"
      className={cx("button", `button-${variant}`)}
      onClick={onClick}
      disabled={disabled}
      title={title}
    >
      {children}
    </button>
  );
}

/* ── Pill ───────────────────────────────────────────────────────────────── */

export function Pill({
  children,
  active,
  onClick,
  size,
  title,
}: {
  children: React.ReactNode;
  active?: boolean;
  onClick?: () => void;
  size?: "sm";
  title?: string;
}) {
  const className = cx(
    "pill",
    size === "sm" && "pill-sm",
    active && "pill-active",
    onClick && "pill-clickable",
  );
  if (!onClick) {
    return (
      <span className={className} title={title}>
        {children}
      </span>
    );
  }
  return (
    <button type="button" className={className} onClick={onClick} title={title}>
      {children}
    </button>
  );
}

/* ── Card ───────────────────────────────────────────────────────────────── */

const CardSizeContext = React.createContext<"md" | "lg">("md");

export function Card({
  children,
  size = "md",
}: {
  children: React.ReactNode;
  size?: "md" | "lg";
}) {
  return (
    <CardSizeContext.Provider value={size}>
      <div className="card">{children}</div>
    </CardSizeContext.Provider>
  );
}

export function CardHeader({
  children,
  trailing,
}: {
  children: React.ReactNode;
  trailing?: React.ReactNode;
}) {
  const size = React.useContext(CardSizeContext);
  return (
    <div className={cx("card-header", size === "lg" && "card-header-lg")}>
      <span>{children}</span>
      {trailing !== undefined && <span className="card-header-trailing">{trailing}</span>}
    </div>
  );
}

export function CardBody({ children }: { children: React.ReactNode }) {
  const size = React.useContext(CardSizeContext);
  return <div className={cx("card-body", size === "lg" && "card-body-lg")}>{children}</div>;
}

/* ── Callout ────────────────────────────────────────────────────────────── */

export function Callout({
  children,
  tone = "neutral",
  title,
}: {
  children?: React.ReactNode;
  tone?: Tone;
  title: string;
}) {
  return (
    <div className={cx("callout", `callout-${tone}`)}>
      <div className="callout-title">{title}</div>
      {children !== undefined && children !== null && (
        <div className="callout-body">{children}</div>
      )}
    </div>
  );
}

/* ── Stat ───────────────────────────────────────────────────────────────── */

export function Stat({
  value,
  label,
  tone,
}: {
  value: string | number;
  label: string;
  tone?: "info" | "success" | "warning";
}) {
  return (
    <div className={cx("stat", tone && `stat-${tone}`)}>
      <div className="stat-value">{value}</div>
      <div className="stat-label">{label}</div>
    </div>
  );
}

export function ClickStat({
  active,
  value,
  label,
  tone,
  onClick,
}: {
  active: boolean;
  value: string;
  label: string;
  tone?: "info" | "success";
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className={cx("stat-clickable", active && "stat-clickable-active")}
      onClick={onClick}
    >
      <Stat value={value} label={label} tone={tone} />
    </button>
  );
}

/* ── Table ──────────────────────────────────────────────────────────────── */

export type ColumnAlign = "left" | "right" | "center";

export function Table({
  headers,
  rows,
  columnAlign,
  striped,
  stickyHeader,
  rowTone,
  rowKeys,
  wide,
  style,
}: {
  headers: React.ReactNode[];
  rows: React.ReactNode[][];
  columnAlign?: ColumnAlign[];
  striped?: boolean;
  stickyHeader?: boolean;
  rowTone?: Array<Tone | undefined>;
  rowKeys?: string[];
  wide?: boolean;
  style?: React.CSSProperties;
}) {
  function alignClass(index: number) {
    const align = columnAlign?.[index];
    if (align === "right") return "align-right";
    if (align === "center") return "align-center";
    return undefined;
  }

  return (
    <div className="table-scroll" style={style}>
      <table
        className={cx(
          "table",
          wide && "table-wide",
          striped && "table-striped",
          stickyHeader && "table-sticky",
        )}
      >
        <thead>
          <tr>
            {headers.map((header, i) => (
              <th key={i} className={alignClass(i)}>
                {header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, r) => (
            <tr
              key={rowKeys?.[r] ?? r}
              className={rowTone?.[r] ? `row-tone-${rowTone[r]}` : undefined}
            >
              {row.map((cell, c) => (
                <td key={c} className={alignClass(c)}>
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ── Inputs ─────────────────────────────────────────────────────────────── */

export function TextInput({
  value,
  onChange,
  placeholder,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
}) {
  return (
    <input
      className="input"
      value={value}
      placeholder={placeholder}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}

export function TextArea({
  value,
  onChange,
  rows = 3,
  placeholder,
}: {
  value: string;
  onChange: (value: string) => void;
  rows?: number;
  placeholder?: string;
}) {
  return (
    <textarea
      className="textarea"
      rows={rows}
      value={value}
      placeholder={placeholder}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}

export function Select({
  value,
  onChange,
  options,
}: {
  value: string;
  onChange: (value: string) => void;
  options: { value: string; label: string }[];
}) {
  return (
    <select className="select" value={value} onChange={(e) => onChange(e.target.value)}>
      {options.map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  );
}

export function Checkbox({
  checked,
  onChange,
  label,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
  label: string;
}) {
  return (
    <label className="checkbox">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span>{label}</span>
    </label>
  );
}

export function Toggle({
  checked,
  onChange,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      className={cx("toggle", checked && "toggle-on")}
      onClick={() => onChange(!checked)}
    >
      <span className="toggle-knob" />
    </button>
  );
}

/* ── BarChart ───────────────────────────────────────────────────────────── */

export type BarSeries = {
  name: string;
  data: number[];
  tone?: "info" | "neutral" | "success";
};

export function BarChart({
  categories,
  series,
  height = 140,
  stacked,
  valuePrefix = "",
  valueSuffix = "",
}: {
  categories: string[];
  series: BarSeries[];
  height?: number;
  stacked?: boolean;
  valuePrefix?: string;
  valueSuffix?: string;
}) {
  const totals = categories.map((_, i) =>
    stacked
      ? series.reduce((sum, s) => sum + (s.data[i] ?? 0), 0)
      : Math.max(...series.map((s) => s.data[i] ?? 0)),
  );
  const max = Math.max(1, ...totals);

  function format(value: number) {
    const rounded = Number.isInteger(value) ? String(value) : value.toFixed(2);
    return `${valuePrefix}${rounded}${valueSuffix}`;
  }

  return (
    <div className="chart">
      <div className="chart-plot" style={{ height }}>
        {categories.map((category, i) => (
          <div className="chart-col" key={category}>
            <div className="chart-value">{format(totals[i])}</div>
            <div className="chart-bars" style={{ height: height - 24 }}>
              {stacked ? (
                <div
                  className="chart-bar"
                  style={{
                    display: "flex",
                    flexDirection: "column-reverse",
                    height: `${(totals[i] / max) * 100}%`,
                    background: "transparent",
                    border: 0,
                  }}
                >
                  {series.map((s) => {
                    const value = s.data[i] ?? 0;
                    const share = totals[i] > 0 ? (value / totals[i]) * 100 : 0;
                    return (
                      <div
                        key={s.name}
                        className={cx("chart-bar", `chart-bar-${s.tone ?? "neutral"}`)}
                        style={{ height: `${share}%`, width: "100%", maxWidth: "none" }}
                      />
                    );
                  })}
                </div>
              ) : (
                series.map((s) => (
                  <div
                    key={s.name}
                    className={cx("chart-bar", `chart-bar-${s.tone ?? "neutral"}`)}
                    style={{ height: `${((s.data[i] ?? 0) / max) * 100}%` }}
                  />
                ))
              )}
            </div>
          </div>
        ))}
      </div>
      <div className="chart-axis">
        {categories.map((category) => (
          <div className="chart-axis-label" key={category}>
            {category}
          </div>
        ))}
      </div>
      {series.length > 1 && (
        <div className="chart-legend">
          {series.map((s) => (
            <span className="chart-legend-item" key={s.name}>
              <span className={cx("chart-swatch", `chart-bar-${s.tone ?? "neutral"}`)} />
              {s.name}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
