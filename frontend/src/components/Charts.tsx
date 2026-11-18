import { formatCompact } from "../lib/format";

export function ActivityAreaChart({ data }: { data: Array<{ day: string; jobs: number }> }) {
  const maximum = Math.max(1, ...data.map((item) => item.jobs));
  const point = (value: number, index: number) => {
    const x = data.length <= 1 ? 50 : (index / (data.length - 1)) * 100;
    const y = 90 - (value / maximum) * 80;
    return [x, y] as const;
  };
  const points = data.map((item, index) => point(item.jobs, index));
  const line = points.map(([x, y], index) => `${index ? "L" : "M"}${x},${y}`).join(" ");
  const area = points.length ? `${line} L100,100 L0,100 Z` : "";
  return (
    <div className="light-chart" role="img" aria-label="Jobs discovered over the last 14 days">
      <svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
        {[10, 30, 50, 70, 90].map((y) => <line className="chart-gridline" key={y} x1="0" x2="100" y1={y} y2={y} />)}
        <path className="chart-area" d={area} />
        <path className="chart-line" d={line} pathLength="1" />
      </svg>
      <div className="chart-labels" aria-hidden="true">{data.filter((_, index) => index % 2 === 0 || index === data.length - 1).map((item) => <span key={item.day}>{item.day.slice(5)}</span>)}</div>
      <ol className="sr-only">{data.map((item) => <li key={item.day}>{item.day}: {item.jobs} jobs</li>)}</ol>
    </div>
  );
}

export function TokenBarChart({
  data,
}: {
  data: Array<{ day: string; tokens_in: number; tokens_out: number }>;
}) {
  const maximum = Math.max(1, ...data.map((item) => item.tokens_in + item.tokens_out));
  return (
    <div className="token-chart" role="img" aria-label="Input and output token usage by day">
      <div className="token-chart__plot">
        {data.map((item) => {
          const inputHeight = (item.tokens_in / maximum) * 100;
          const outputHeight = (item.tokens_out / maximum) * 100;
          return (
            <div className="token-column" key={`${item.day}-${item.tokens_in}-${item.tokens_out}`} title={`${item.day}: ${formatCompact(item.tokens_in)} input, ${formatCompact(item.tokens_out)} output`}>
              <span className="token-column__input" style={{ height: `${inputHeight}%` }} />
              <span className="token-column__output" style={{ height: `${outputHeight}%` }} />
            </div>
          );
        })}
      </div>
      <div className="token-chart__labels" aria-hidden="true">{data.map((item, index) => <span key={item.day} style={{ visibility: index % 2 === 0 ? "visible" : "hidden" }}>{item.day.slice(5)}</span>)}</div>
      <div className="chart-legend"><span><i className="legend-input" /> Input</span><span><i className="legend-output" /> Output</span></div>
      <ol className="sr-only">{data.map((item) => <li key={item.day}>{item.day}: {item.tokens_in} input and {item.tokens_out} output tokens</li>)}</ol>
    </div>
  );
}
