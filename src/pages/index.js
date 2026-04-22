import { useState, useEffect } from "react";
import { db } from "../firebase";
import { doc, getDoc, collection, getDocs } from "firebase/firestore";

function Mini({ data, color, w = 130, h = 44 }) {
  if (!data || data.length === 0) return null;
  const mn = Math.min(...data), mx = Math.max(...data), rng = mx - mn || 1;
  const pts = data.map((v, i) => `${(i / (data.length - 1)) * w},${h - ((v - mn) / rng) * (h - 6) - 3}`).join(" ");
  return <svg width={w} height={h}><polyline points={pts} fill="none" stroke={color} strokeWidth="2" strokeLinejoin="round" /></svg>;
}

function Big({ data, color }) {
  if (!data || data.length === 0) return null;
  const mn = Math.min(...data), mx = Math.max(...data), rng = mx - mn || 1, w = 560, h = 180;
  const pts = data.map((v, i) => `${(i / (data.length - 1)) * w},${h - ((v - mn) / rng) * (h - 30) - 15}`).join(" ");
  return (
    <svg viewBox={`0 0 ${w} ${h}`} style={{ width: "100%", height: "auto" }}>
      <defs><linearGradient id="cg" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor={color} stopOpacity=".1" /><stop offset="100%" stopColor={color} stopOpacity="0" /></linearGradient></defs>
      <polygon points={pts + ` ${w},${h} 0,${h}`} fill="url(#cg)" />
      <polyline points={pts} fill="none" stroke={color} strokeWidth="2" strokeLinejoin="round" />
      <text x="4" y="12" fontSize="10" fill="#94a3b8" fontFamily="DM Mono, monospace">${mx.toFixed(0)}</text>
      <text x="4" y={h - 2} fontSize="10" fill="#94a3b8" fontFamily="DM Mono, monospace">${mn.toFixed(0)}</text>
    </svg>
  );
}

function genChart(price, dir) {
  const pts = [];
  let p = dir === "PUT" ? price * 0.6 : price * 1.3;
  for (let i = 0; i < 20; i++) {
    p += (dir === "PUT" ? 1 : -1) * (Math.random() * price * 0.03) + (dir === "PUT" ? price * 0.015 : -price * 0.01);
    pts.push(Math.max(p, price * 0.3));
  }
  pts.push(price);
  return pts;
}

export default function Home() {
  const [page, setPage] = useState("home");
  const [picked, setPicked] = useState(null);
  const [scan, setScan] = useState(null);
  const [record, setRecord] = useState(null);
  const [active, setActive] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function load() {
      try {
        const scanDoc = await getDoc(doc(db, "scans", "latest"));
        if (scanDoc.exists()) setScan(scanDoc.data());

        const recDoc = await getDoc(doc(db, "config", "track_record"));
        if (recDoc.exists()) setRecord(recDoc.data());

        const actDoc = await getDoc(doc(db, "config", "active_trades"));
        if (actDoc.exists()) setActive(actDoc.data());
      } catch (e) {
        console.error("Firestore load error:", e);
      }
      setLoading(false);
    }
    load();
  }, []);

  const picks = scan?.picks || [];
  const trades = record?.trades || [];
  const wr = record?.win_rate || 0;
  const avgR = trades.length > 0 ? trades.reduce((s, t) => s + t.return_pct, 0) / trades.length : 0;
  const mctx = scan?.market_context || {};
  const analysis = scan?.analysis || "";

  const nav = ["home", "watchlist", "track record", "disclaimers"];
  const C = picked ? "analysis" : page;

  if (loading) return (
    <div style={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center", background: "#fafbfc" }}>
      <div style={{ textAlign: "center" }}>
        <div style={{ width: "8px", height: "8px", borderRadius: "50%", background: "#2563eb", margin: "0 auto 12px", animation: "pulse 1s infinite" }} />
        <div style={{ fontSize: "14px", color: "#64748b" }}>Loading Friday Scout...</div>
      </div>
      <style>{`@keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.3; } }`}</style>
    </div>
  );

  return (
    <div style={{ minHeight: "100vh", background: "#fafbfc", fontFamily: "'Libre Franklin','Helvetica Neue',sans-serif", color: "#1a1a2e" }}>
      {/* Nav */}
      <nav style={{ background: "#fff", borderBottom: "1px solid #e8ecf1", padding: "0 32px", display: "flex", justifyContent: "space-between", alignItems: "center", height: "56px", position: "sticky", top: 0, zIndex: 100 }}>
        <div style={{ display: "flex", alignItems: "center", gap: "24px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "8px", cursor: "pointer" }} onClick={() => { setPage("home"); setPicked(null); }}>
            <div style={{ width: "8px", height: "8px", borderRadius: "50%", background: "#2563eb" }} />
            <span style={{ fontSize: "16px", fontWeight: "800", letterSpacing: "-.5px" }}>Friday Scout</span>
          </div>
          {nav.map(p => (
            <button key={p} onClick={() => { setPage(p); setPicked(null); }} style={{ background: "none", border: "none", fontSize: "13px", fontWeight: page === p ? "700" : "400", color: page === p ? "#2563eb" : "#64748b", cursor: "pointer", textTransform: "capitalize", borderBottom: page === p ? "2px solid #2563eb" : "2px solid transparent", padding: "16px 0", fontFamily: "inherit" }}>{p}</button>
          ))}
        </div>
        <div style={{ display: "flex", gap: "16px", alignItems: "center" }}>
          <div style={{ textAlign: "right" }}>
            <div style={{ fontSize: "9px", color: "#94a3b8", textTransform: "uppercase", letterSpacing: "1px" }}>Win Rate</div>
            <div style={{ fontSize: "14px", fontWeight: "700", color: "#2563eb", fontFamily: "'DM Mono'" }}>{wr.toFixed(0)}%</div>
          </div>
          <div style={{ width: "1px", height: "20px", background: "#e8ecf1" }} />
          <div style={{ textAlign: "right" }}>
            <div style={{ fontSize: "9px", color: "#94a3b8", textTransform: "uppercase", letterSpacing: "1px" }}>Record</div>
            <div style={{ fontSize: "14px", fontWeight: "700", color: "#0f172a", fontFamily: "'DM Mono'" }}>{record?.record || "--"}</div>
          </div>
        </div>
      </nav>

      <div style={{ maxWidth: "1100px", margin: "0 auto", padding: "24px 32px" }}>

        {/* HOME */}
        {C === "home" && (
          <div>
            {/* Macro */}
            <div style={{ background: "#fff", border: "1px solid #e8ecf1", borderRadius: "12px", padding: "24px", marginBottom: "24px" }}>
              <div style={{ fontSize: "11px", color: "#2563eb", fontWeight: "700", textTransform: "uppercase", letterSpacing: "1.5px", marginBottom: "8px" }}>Macro Brief — {scan?.timestamp ? new Date(scan.timestamp).toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' }) : "Loading..."}</div>
              <div style={{ display: "flex", gap: "20px", marginBottom: "16px" }}>
                {Object.entries(mctx).map(([name, d]) => (
                  <div key={name} style={{ fontSize: "13px", color: "#475569" }}>
                    {name}: <strong style={{ fontFamily: "'DM Mono'", color: d.change >= 0 ? "#16a34a" : "#dc2626" }}>${d.price?.toFixed(2)} ({d.change >= 0 ? "+" : ""}{d.change?.toFixed(2)}%)</strong>
                  </div>
                ))}
              </div>
              {analysis && (
                <p style={{ fontSize: "14px", lineHeight: "1.7", color: "#334155", margin: 0, whiteSpace: "pre-wrap" }}>
                  {analysis.split("\n").slice(0, 8).join("\n").substring(0, 600)}
                  {analysis.length > 600 ? "..." : ""}
                </p>
              )}
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "1fr 320px", gap: "24px" }}>
              <div>
                <div style={{ fontSize: "11px", color: "#64748b", fontWeight: "700", textTransform: "uppercase", letterSpacing: "1.5px", marginBottom: "16px" }}>
                  Trade Recommendations — {scan?.total_passed || 0} stocks passed all gates
                </div>
                {picks.length === 0 && <div style={{ background: "#fff", border: "1px solid #e8ecf1", borderRadius: "12px", padding: "40px", textAlign: "center", color: "#94a3b8" }}>No scan data yet. Scans run Monday & Thursday at 8:45am CT.</div>}
                {picks.map((s, i) => (
                  <div key={i} onClick={() => setPicked(s)} style={{ background: "#fff", border: "1px solid #e8ecf1", borderRadius: "12px", padding: "20px", marginBottom: "12px", cursor: "pointer", transition: "all .15s", boxShadow: "0 1px 3px rgba(0,0,0,.04)" }}
                    onMouseEnter={e => { e.currentTarget.style.borderColor = "#2563eb"; e.currentTarget.style.boxShadow = "0 2px 8px rgba(37,99,235,.08)"; }}
                    onMouseLeave={e => { e.currentTarget.style.borderColor = "#e8ecf1"; e.currentTarget.style.boxShadow = "0 1px 3px rgba(0,0,0,.04)"; }}>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
                      <div style={{ flex: 1 }}>
                        <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "6px" }}>
                          <span style={{ fontSize: "20px", fontWeight: "800" }}>{s.ticker}</span>
                          <span style={{ padding: "2px 10px", borderRadius: "100px", fontSize: "10px", fontWeight: "700", background: s.direction === "CALL" ? "#dcfce7" : "#fee2e2", color: s.direction === "CALL" ? "#16a34a" : "#dc2626" }}>{s.direction}</span>
                          <span style={{ fontSize: "11px", color: "#94a3b8", fontFamily: "'DM Mono'" }}>{s.conviction} · {s.total_score}</span>
                        </div>
                        <div style={{ fontSize: "12px", color: "#64748b" }}>
                          ${s.price} · Cons ${s.conservative_strike} / Risky ${s.risky_strike}
                          {s.dcf_gap ? ` · DCF ${Math.abs(s.dcf_gap).toFixed(0)}% ${s.direction === "PUT" ? "over" : "under"}valued` : ""}
                        </div>
                        {s.details && s.details.length > 0 && (
                          <div style={{ fontSize: "11px", color: "#94a3b8", marginTop: "4px" }}>{s.details[0]}</div>
                        )}
                      </div>
                      <Mini data={genChart(s.price, s.direction)} color={s.direction === "CALL" ? "#16a34a" : "#dc2626"} />
                    </div>
                  </div>
                ))}
              </div>

              {/* Sidebar */}
              <div>
                <div style={{ background: "#fff", border: "1px solid #e8ecf1", borderRadius: "12px", padding: "20px", marginBottom: "16px" }}>
                  <div style={{ fontSize: "11px", color: "#64748b", fontWeight: "700", textTransform: "uppercase", letterSpacing: "1.5px", marginBottom: "12px" }}>Active Trades</div>
                  {(!active || !active.trades || active.trades.length === 0) ? (
                    <div style={{ fontSize: "13px", color: "#94a3b8", fontStyle: "italic", textAlign: "center", padding: "16px 0" }}>
                      {active?.status || "No active positions"}
                    </div>
                  ) : active.trades.map((t, i) => (
                    <div key={i} style={{ padding: "8px 0", borderBottom: "1px solid #f1f5f9", fontSize: "13px" }}>
                      <strong>{t.ticker}</strong> · {t.direction} · ${t.strike}
                    </div>
                  ))}
                </div>

                <div style={{ background: "#fff", border: "1px solid #e8ecf1", borderRadius: "12px", padding: "20px", marginBottom: "16px" }}>
                  <div style={{ fontSize: "11px", color: "#64748b", fontWeight: "700", textTransform: "uppercase", letterSpacing: "1.5px", marginBottom: "12px" }}>Performance</div>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "8px" }}>
                    <div style={{ background: "#f8fafc", borderRadius: "8px", padding: "14px", textAlign: "center" }}>
                      <div style={{ fontSize: "22px", fontWeight: "800", color: "#2563eb", fontFamily: "'DM Mono'" }}>{wr.toFixed(0)}%</div>
                      <div style={{ fontSize: "10px", color: "#64748b" }}>WIN RATE</div>
                    </div>
                    <div style={{ background: "#f8fafc", borderRadius: "8px", padding: "14px", textAlign: "center" }}>
                      <div style={{ fontSize: "22px", fontWeight: "800", color: "#64748b", fontFamily: "'DM Mono'" }}>{record?.record || "--"}</div>
                      <div style={{ fontSize: "10px", color: "#64748b" }}>RECORD</div>
                    </div>
                  </div>
                </div>

                <div style={{ background: "#fff", border: "1px solid #e8ecf1", borderRadius: "12px", padding: "20px" }}>
                  <div style={{ fontSize: "11px", color: "#64748b", fontWeight: "700", textTransform: "uppercase", letterSpacing: "1.5px", marginBottom: "12px" }}>Scan Info</div>
                  <div style={{ fontSize: "12px", color: "#475569", lineHeight: "1.8" }}>
                    <div>Tickers scanned: <strong>304</strong></div>
                    <div>Candidates analyzed: <strong>{scan?.total_candidates || "--"}</strong></div>
                    <div>Passed all gates: <strong>{scan?.total_passed || "--"}</strong></div>
                    <div>Last scan: <strong>{scan?.timestamp ? new Date(scan.timestamp).toLocaleString() : "--"}</strong></div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* WATCHLIST */}
        {C === "watchlist" && (
          <div>
            <div style={{ fontSize: "11px", color: "#64748b", fontWeight: "700", textTransform: "uppercase", letterSpacing: "1.5px", marginBottom: "20px" }}>Watchlist — Stocks Passing All 4 Gates</div>
            {picks.map((s, i) => (
              <div key={i} onClick={() => setPicked(s)} style={{ background: "#fff", border: "1px solid #e8ecf1", borderRadius: "12px", padding: "24px", marginBottom: "16px", cursor: "pointer" }}
                onMouseEnter={e => e.currentTarget.style.borderColor = "#2563eb"} onMouseLeave={e => e.currentTarget.style.borderColor = "#e8ecf1"}>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "12px" }}>
                  <div>
                    <div style={{ display: "flex", alignItems: "center", gap: "10px", marginBottom: "4px" }}>
                      <span style={{ fontSize: "24px", fontWeight: "800" }}>{s.ticker}</span>
                      <span style={{ padding: "3px 12px", borderRadius: "100px", fontSize: "11px", fontWeight: "700", background: s.direction === "CALL" ? "#dcfce7" : "#fee2e2", color: s.direction === "CALL" ? "#16a34a" : "#dc2626" }}>{s.direction}</span>
                      <span style={{ background: "#eff6ff", color: "#2563eb", padding: "3px 10px", borderRadius: "100px", fontSize: "11px", fontWeight: "700" }}>{s.conviction} · {s.total_score}</span>
                    </div>
                  </div>
                  <Mini data={genChart(s.price, s.direction)} color={s.direction === "CALL" ? "#16a34a" : "#dc2626"} w={170} h={52} />
                </div>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr 1fr", gap: "8px" }}>
                  <div style={{ background: "#f8fafc", borderRadius: "8px", padding: "10px" }}>
                    <div style={{ fontSize: "9px", color: "#2563eb", fontWeight: "700", textTransform: "uppercase" }}>Valuation</div>
                    <div style={{ fontSize: "11px", color: "#475569" }}>{s.dcf_gap ? `DCF ${Math.abs(s.dcf_gap).toFixed(0)}% ${s.direction === "PUT" ? "over" : "under"}valued` : "N/A"}</div>
                  </div>
                  <div style={{ background: "#f8fafc", borderRadius: "8px", padding: "10px" }}>
                    <div style={{ fontSize: "9px", color: "#2563eb", fontWeight: "700", textTransform: "uppercase" }}>Technical</div>
                    <div style={{ fontSize: "11px", color: "#475569" }}>RSI {s.rsi || "N/A"}</div>
                  </div>
                  <div style={{ background: "#f8fafc", borderRadius: "8px", padding: "10px" }}>
                    <div style={{ fontSize: "9px", color: "#2563eb", fontWeight: "700", textTransform: "uppercase" }}>Quality</div>
                    <div style={{ fontSize: "11px", color: "#475569" }}>Piotroski {s.piotroski || "N/A"}/9</div>
                  </div>
                  <div style={{ background: "#f8fafc", borderRadius: "8px", padding: "10px" }}>
                    <div style={{ fontSize: "9px", color: "#2563eb", fontWeight: "700", textTransform: "uppercase" }}>Strikes</div>
                    <div style={{ fontSize: "11px", color: "#475569" }}>${s.conservative_strike} / ${s.risky_strike}</div>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}

        {/* ANALYSIS */}
        {C === "analysis" && picked && (
          <div>
            <button onClick={() => setPicked(null)} style={{ background: "none", border: "none", color: "#2563eb", fontSize: "13px", cursor: "pointer", marginBottom: "16px", fontWeight: "600", fontFamily: "inherit" }}>← Back</button>
            <div style={{ background: "#fff", border: "1px solid #e8ecf1", borderRadius: "12px", padding: "32px", marginBottom: "24px" }}>
              <div style={{ display: "flex", alignItems: "center", gap: "12px", marginBottom: "12px" }}>
                <span style={{ fontSize: "32px", fontWeight: "800" }}>{picked.ticker}</span>
                <span style={{ padding: "4px 14px", borderRadius: "100px", fontSize: "12px", fontWeight: "700", background: picked.direction === "CALL" ? "#dcfce7" : "#fee2e2", color: picked.direction === "CALL" ? "#16a34a" : "#dc2626" }}>{picked.direction}</span>
                <span style={{ background: "#eff6ff", color: "#2563eb", padding: "4px 12px", borderRadius: "100px", fontSize: "12px", fontWeight: "700" }}>{picked.conviction} · {picked.total_score}</span>
              </div>
              <div style={{ display: "flex", gap: "24px", fontSize: "13px", color: "#64748b", marginBottom: "16px" }}>
                <span>Price: <strong style={{ color: "#0f172a" }}>${picked.price}</strong></span>
                <span>Conservative: <strong style={{ color: "#16a34a" }}>${picked.conservative_strike}</strong></span>
                <span>Risky: <strong style={{ color: "#dc2626" }}>${picked.risky_strike}</strong></span>
                {picked.dcf_value && <span>DCF: <strong style={{ color: "#2563eb" }}>${picked.dcf_value}</strong></span>}
              </div>
            </div>

            <div style={{ background: "#fff", border: "1px solid #e8ecf1", borderRadius: "12px", padding: "24px", marginBottom: "24px" }}>
              <Big data={genChart(picked.price, picked.direction)} color={picked.direction === "CALL" ? "#16a34a" : "#dc2626"} />
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "12px", marginBottom: "24px" }}>
              <div style={{ background: "#fff", border: "1px solid #e8ecf1", borderRadius: "12px", padding: "20px", textAlign: "center" }}>
                <div style={{ fontSize: "10px", color: "#94a3b8", textTransform: "uppercase", marginBottom: "6px" }}>RSI</div>
                <div style={{ fontSize: "28px", fontWeight: "800", color: (picked.rsi || 50) > 70 ? "#dc2626" : (picked.rsi || 50) < 30 ? "#16a34a" : "#0f172a", fontFamily: "'DM Mono'" }}>{picked.rsi || "N/A"}</div>
              </div>
              <div style={{ background: "#fff", border: "1px solid #e8ecf1", borderRadius: "12px", padding: "20px", textAlign: "center" }}>
                <div style={{ fontSize: "10px", color: "#94a3b8", textTransform: "uppercase", marginBottom: "6px" }}>DCF Gap</div>
                <div style={{ fontSize: "28px", fontWeight: "800", color: "#2563eb", fontFamily: "'DM Mono'" }}>{picked.dcf_gap ? `${Math.abs(picked.dcf_gap).toFixed(0)}%` : "N/A"}</div>
              </div>
              <div style={{ background: "#fff", border: "1px solid #e8ecf1", borderRadius: "12px", padding: "20px", textAlign: "center" }}>
                <div style={{ fontSize: "10px", color: "#94a3b8", textTransform: "uppercase", marginBottom: "6px" }}>Piotroski</div>
                <div style={{ fontSize: "28px", fontWeight: "800", color: "#0f172a", fontFamily: "'DM Mono'" }}>{picked.piotroski || "N/A"}/9</div>
              </div>
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px", marginBottom: "24px" }}>
              {[
                { label: "Gate 1: Valuation", items: picked.details || [] },
                { label: "Gate 2: Smart Money", items: picked.g2_details || [] },
                { label: "Gate 3: Catalyst", items: picked.g3_details || [] },
                { label: "Gate 4: Red Flags", items: picked.g4_flags || ["Clean — no flags"] },
              ].map((gate, i) => (
                <div key={i} style={{ background: "#fff", border: "1px solid #e8ecf1", borderRadius: "12px", padding: "20px" }}>
                  <div style={{ fontSize: "10px", color: "#2563eb", fontWeight: "700", textTransform: "uppercase", letterSpacing: ".5px", marginBottom: "8px" }}>{gate.label}</div>
                  {gate.items.map((d, j) => (
                    <div key={j} style={{ fontSize: "12px", color: "#475569", lineHeight: "1.6" }}>• {d}</div>
                  ))}
                </div>
              ))}
            </div>

            <div style={{ fontSize: "11px", color: "#94a3b8", textAlign: "center" }}>For informational purposes only. See Disclaimers.</div>
          </div>
        )}

        {/* TRACK RECORD */}
        {C === "track record" && (
          <div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "16px", marginBottom: "28px" }}>
              {[
                { l: "Win Rate", v: `${wr.toFixed(0)}%`, c: "#2563eb" },
                { l: "Avg Return", v: `${avgR >= 0 ? "+" : ""}${avgR.toFixed(1)}%`, c: avgR >= 0 ? "#16a34a" : "#dc2626" },
                { l: "Record", v: record?.record || "--", c: "#0f172a" },
              ].map((s, i) => (
                <div key={i} style={{ background: "#fff", border: "1px solid #e8ecf1", borderRadius: "12px", padding: "20px", textAlign: "center" }}>
                  <div style={{ fontSize: "10px", color: "#94a3b8", textTransform: "uppercase", letterSpacing: "1px", marginBottom: "8px" }}>{s.l}</div>
                  <div style={{ fontSize: "28px", fontWeight: "800", color: s.c, fontFamily: "'DM Mono'" }}>{s.v}</div>
                </div>
              ))}
            </div>
            <div style={{ background: "#fff", border: "1px solid #e8ecf1", borderRadius: "12px", overflow: "hidden" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "13px" }}>
                <thead><tr style={{ background: "#f8fafc" }}>
                  {["#", "Ticker", "Dir", "Strike", "Return", "Dates", "Result"].map(h => (
                    <th key={h} style={{ padding: "14px 16px", textAlign: "left", fontSize: "10px", color: "#64748b", fontWeight: "700", textTransform: "uppercase", borderBottom: "1px solid #e8ecf1" }}>{h}</th>
                  ))}
                </tr></thead>
                <tbody>{trades.map((t, i) => (
                  <tr key={i} style={{ borderBottom: "1px solid #f1f5f9" }}>
                    <td style={{ padding: "14px 16px", color: "#94a3b8" }}>#{t.id}</td>
                    <td style={{ padding: "14px 16px", fontWeight: "700" }}>{t.ticker}</td>
                    <td style={{ padding: "14px 16px" }}><span style={{ padding: "2px 10px", borderRadius: "100px", fontSize: "10px", fontWeight: "700", background: t.direction === "CALL" ? "#dcfce7" : "#fee2e2", color: t.direction === "CALL" ? "#16a34a" : "#dc2626" }}>{t.direction}</span></td>
                    <td style={{ padding: "14px 16px", fontFamily: "'DM Mono'" }}>${t.strike}</td>
                    <td style={{ padding: "14px 16px", fontWeight: "700", color: t.return_pct >= 0 ? "#16a34a" : "#dc2626", fontFamily: "'DM Mono'" }}>{t.return_pct >= 0 ? "+" : ""}{t.return_pct}%</td>
                    <td style={{ padding: "14px 16px", fontSize: "11px", color: "#94a3b8" }}>{t.entryDate} → {t.exitDate}</td>
                    <td style={{ padding: "14px 16px" }}><span style={{ fontSize: "10px", fontWeight: "700", color: t.result === "WIN" ? "#16a34a" : "#dc2626" }}>{t.result}</span></td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          </div>
        )}

        {/* DISCLAIMERS */}
        {C === "disclaimers" && (
          <div style={{ background: "#fff", border: "1px solid #e8ecf1", borderRadius: "12px", padding: "32px" }}>
            <h1 style={{ fontSize: "24px", fontWeight: "800", marginBottom: "24px", fontFamily: "'Playfair Display',serif" }}>Disclaimers & Disclosures</h1>
            <div style={{ fontSize: "14px", color: "#475569", lineHeight: "1.8" }}>
              <h3 style={{ fontSize: "15px", fontWeight: "700", color: "#0f172a", marginTop: "24px", marginBottom: "8px" }}>Not Financial Advice</h3>
              <p>The content on this platform is for informational and educational purposes only. Nothing published here constitutes personalized investment advice, a recommendation to buy or sell any security, or an offer to provide investment advisory services.</p>
              <h3 style={{ fontSize: "15px", fontWeight: "700", color: "#0f172a", marginTop: "24px", marginBottom: "8px" }}>Personal Trading Journal</h3>
              <p>This platform documents the author&apos;s personal trades and analysis. Past performance is not indicative of future results.</p>
              <h3 style={{ fontSize: "15px", fontWeight: "700", color: "#0f172a", marginTop: "24px", marginBottom: "8px" }}>Risk Warning</h3>
              <p>Options trading involves substantial risk and is not suitable for all investors. You can lose your entire investment in a very short period. Only trade with money you can afford to lose entirely.</p>
              <h3 style={{ fontSize: "15px", fontWeight: "700", color: "#0f172a", marginTop: "24px", marginBottom: "8px" }}>Do Your Own Research</h3>
              <p>Before making any investment decision, conduct your own research. Consider consulting with a qualified financial advisor.</p>
              <h3 style={{ fontSize: "15px", fontWeight: "700", color: "#0f172a", marginTop: "24px", marginBottom: "8px" }}>No Guarantees</h3>
              <p>There is no guarantee that any trade or strategy will be profitable. Markets are inherently unpredictable.</p>
              <h3 style={{ fontSize: "15px", fontWeight: "700", color: "#0f172a", marginTop: "24px", marginBottom: "8px" }}>Conflicts of Interest</h3>
              <p>The author actively trades securities discussed. Positions may be entered before, during, or after publication.</p>
            </div>
            <div style={{ marginTop: "32px", paddingTop: "16px", borderTop: "1px solid #e8ecf1", fontSize: "12px", color: "#94a3b8" }}>© 2026 Friday Scout. All rights reserved.</div>
          </div>
        )}
      </div>
    </div>
  );
}
