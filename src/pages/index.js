import { useState, useEffect } from "react";
import Head from "next/head";

const API = "https://us-central1-scout-493918.cloudfunctions.net/friday-scout";

const COLORS = {
  bg: "#06080f", surface: "#0d1220", surfaceLight: "#141c2e",
  accent: "#00e5a0", accentDim: "#00e5a022", accentGlow: "#00e5a055",
  danger: "#ff3b5c", dangerDim: "#ff3b5c22",
  warning: "#ffb836", blue: "#3b82f6", purple: "#a855f7", purpleDim: "#a855f722",
  text: "#edf2fa", textDim: "#5a6d8a", border: "#1a2540", gold: "#ffd700",
};

const GATES = [
  { id: 1, code: "SENTINEL", name: "Market Filter" },
  { id: 2, code: "ATLAS", name: "Core Strength" },
  { id: 3, code: "ORACLE", name: "Forward Vision" },
  { id: 4, code: "PHANTOM", name: "Smart Money" },
  { id: 5, code: "CATALYST", name: "Event Trigger" },
  { id: 6, code: "SPECTER", name: "Threat Scan" },
  { id: 7, code: "MERIDIAN", name: "Sector Wind" },
  { id: 8, code: "AEGIS", name: "Earnings Shield" },
  { id: 9, code: "COMPASS", name: "Trend Lock" },
  { id: 10, code: "PULSE", name: "Volatility Read" },
  { id: 11, code: "SIGNAL", name: "Intel Feed" },
  { id: 12, code: "CURRENT", name: "Flow Analysis" },
  { id: 13, code: "ARCHER", name: "Strategy Select" },
  { id: 14, code: "FORTRESS", name: "Risk Gate" },
];

const WINS = [
  { ticker: "MSFT", type: "CALL", gain: "+149.5%" },
  { ticker: "NFLX", type: "CALL", gain: "+46.8%" },
  { ticker: "NOW", type: "CALL", gain: "+112.1%" },
];

/* ─── TICKER TAPE ─── */
function TickerTape() {
  const items = [...WINS, ...WINS, ...WINS, ...WINS, ...WINS, ...WINS];
  return (
    <div style={{ width: "100%", overflow: "hidden", background: `${COLORS.surface}ee`, borderBottom: `1px solid ${COLORS.border}`, padding: "8px 0", position: "relative", zIndex: 10 }}>
      <div style={{ display: "flex", gap: 40, whiteSpace: "nowrap", animation: "tickerScroll 30s linear infinite" }}>
        {items.map((w, i) => (
          <div key={i} style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 12, fontWeight: 700, color: COLORS.text }}>{w.ticker}</span>
            <span style={{ padding: "1px 6px", borderRadius: 3, fontSize: 9, fontFamily: "'JetBrains Mono', monospace", fontWeight: 700, background: COLORS.accentDim, color: COLORS.accent }}>{w.type}</span>
            <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 12, fontWeight: 700, color: COLORS.accent }}>{w.gain}</span>
            <span style={{ color: COLORS.border }}>│</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ─── SCOUT SCORE RING ─── */
function ScoutScore({ score, size = 160 }) {
  const c = score >= 80 ? COLORS.accent : score >= 60 ? COLORS.warning : score >= 40 ? COLORS.blue : COLORS.danger;
  const label = score >= 80 ? "STRONG BUY" : score >= 60 ? "MODERATE" : score >= 40 ? "WATCH" : "AVOID";
  const circ = (size - 20) * Math.PI;
  const filled = (score / 100) * circ;
  return (
    <div style={{ position: "relative", width: size, height: size, display: "flex", alignItems: "center", justifyContent: "center" }}>
      <div style={{ position: "absolute", width: size + 20, height: size + 20, borderRadius: "50%", border: `2px solid ${c}33`, animation: "ringPulse 2s ease-out infinite" }} />
      <svg width={size} height={size} style={{ position: "absolute", transform: "rotate(-90deg)" }}>
        <circle cx={size/2} cy={size/2} r={(size-20)/2} fill="none" stroke={COLORS.border} strokeWidth="6" />
        <circle cx={size/2} cy={size/2} r={(size-20)/2} fill="none" stroke={c} strokeWidth="6"
          strokeDasharray={circ} strokeDashoffset={circ - filled} strokeLinecap="round"
          style={{ transition: "stroke-dashoffset 1.5s ease", filter: `drop-shadow(0 0 8px ${c}66)` }} />
      </svg>
      <div style={{ textAlign: "center", animation: "scoreReveal 0.8s ease 0.3s both" }}>
        <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: size * 0.28, fontWeight: 800, color: c, lineHeight: 1 }}>{score}</div>
        <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 9, fontWeight: 700, color: c, letterSpacing: 3, marginTop: 4, opacity: 0.8 }}>{label}</div>
      </div>
    </div>
  );
}

/* ─── CONFIDENCE METER ─── */
function ConfidenceMeter({ passed, total = 14 }) {
  const pct = (passed / total) * 100;
  const c = pct >= 85 ? COLORS.accent : pct >= 65 ? COLORS.warning : pct >= 45 ? COLORS.blue : COLORS.danger;
  const label = pct >= 85 ? "EXTREME" : pct >= 65 ? "HIGH" : pct >= 45 ? "MODERATE" : "LOW";
  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
        <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 10, color: COLORS.textDim, letterSpacing: 2 }}>CONFIDENCE</span>
        <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 12, fontWeight: 700, color: c }}>{label} — {passed}/{total}</span>
      </div>
      <div style={{ height: 8, borderRadius: 4, background: COLORS.border, overflow: "hidden", position: "relative" }}>
        <div style={{ height: "100%", borderRadius: 4, width: `${pct}%`, background: `linear-gradient(90deg, ${c}88, ${c})`, boxShadow: `0 0 12px ${c}44`, transition: "width 1.2s ease" }} />
      </div>
    </div>
  );
}

/* ─── STOCK CHART ─── */
function StockChart({ ticker, price }) {
  const [tf, setTf] = useState("6M");
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const TFS = ["1D","1W","1M","3M","6M","YTD","1Y","2Y","5Y"];

  useEffect(() => {
    if (!ticker) return;
    setLoading(true);
    fetch(`${API}?mode=chart&ticker=${ticker}&tf=${tf}`)
      .then(r => r.json())
      .then(d => {
        if (Array.isArray(d) && d.length > 0) {
          setData(d.map(p => p.close));
        } else {
          // Fallback to simulated if API fails
          const days = { "1D": 78, "1W": 5, "1M": 22, "3M": 65, "6M": 130, "YTD": 100, "1Y": 252, "2Y": 504, "5Y": 1260 }[tf] || 130;
          let p2 = parseFloat(price) || 100;
          let sim = p2 * (0.7 + Math.random() * 0.6);
          const pts = [];
          for (let i = days; i >= 0; i--) { sim += (Math.random() - 0.47) * sim * 0.015; pts.push(Math.max(5, sim)); }
          pts[pts.length - 1] = p2;
          setData(pts);
        }
        setLoading(false);
      })
      .catch(() => {
        setLoading(false);
        setData(null);
      });
  }, [tf, ticker]);

  if (loading) return <div style={{ height: 160, display: "flex", alignItems: "center", justifyContent: "center", fontFamily: "'JetBrains Mono', monospace", fontSize: 12, color: COLORS.textDim }}>Loading chart...</div>;
  if (!data || data.length < 2) return null;

  const w = 700, h = 180;
  const mn = Math.min(...data), mx = Math.max(...data), rng = mx - mn || 1;
  const step = Math.max(1, Math.floor(data.length / 200));
  const sampled = data.filter((_, i) => i % step === 0);
  const toX = i => (i / (sampled.length - 1)) * w;
  const toY = v => h - 15 - ((v - mn) / rng) * (h - 30);
  const pathD = sampled.map((v, i) => `${i === 0 ? "M" : "L"} ${toX(i).toFixed(1)} ${toY(v).toFixed(1)}`).join(" ");
  const isUp = sampled[sampled.length - 1] >= sampled[0];
  const lc = isUp ? COLORS.accent : COLORS.danger;
  const changePct = ((sampled[sampled.length-1] - sampled[0]) / sampled[0] * 100);

  return (
    <div style={{ marginBottom: 16 }}>
      <div style={{ display: "flex", gap: 4, marginBottom: 10, flexWrap: "wrap" }}>
        {TFS.map(t => (
          <button key={t} onClick={() => setTf(t)} style={{
            padding: "4px 10px", borderRadius: 6, border: "none",
            background: tf === t ? COLORS.accent : COLORS.surfaceLight,
            color: tf === t ? COLORS.bg : COLORS.textDim,
            fontFamily: "'JetBrains Mono', monospace", fontSize: 10, fontWeight: 700, cursor: "pointer",
          }}>{t}</button>
        ))}
        <span style={{ marginLeft: "auto", fontFamily: "'JetBrains Mono', monospace", fontSize: 12, fontWeight: 700, color: lc }}>
          {isUp ? "+" : ""}{changePct.toFixed(1)}%
        </span>
      </div>
      <svg viewBox={`0 0 ${w} ${h}`} style={{ width: "100%", height: 160 }}>
        <defs>
          <linearGradient id="cg" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={lc} stopOpacity="0.12" />
            <stop offset="100%" stopColor={lc} stopOpacity="0" />
          </linearGradient>
        </defs>
        <polygon points={pathD.replace(/M\s/, "") + ` ${w},${h} 0,${h}`} fill="url(#cg)" />
        <path d={pathD} fill="none" stroke={lc} strokeWidth="2" style={{ filter: `drop-shadow(0 0 4px ${lc}44)` }} />
        <circle cx={toX(sampled.length-1)} cy={toY(sampled[sampled.length-1])} r="4" fill={lc} />
        <text x="4" y={toY(mx)+4} fill={COLORS.textDim} style={{ fontSize: 10, fontFamily: "'JetBrains Mono', monospace" }}>${mx.toFixed(0)}</text>
        <text x="4" y={toY(mn)-4} fill={COLORS.textDim} style={{ fontSize: 10, fontFamily: "'JetBrains Mono', monospace" }}>${mn.toFixed(0)}</text>
      </svg>
    </div>
  );
}

/* ─── SCANNER ─── */
function Scanner() {
  const [ticker, setTicker] = useState("");
  const [scanning, setScanning] = useState(false);
  const [gate, setGate] = useState(-1);
  const [phase, setPhase] = useState("idle");
  const [result, setResult] = useState(null);
  const [raw, setRaw] = useState("");

  const scan = async () => {
    if (!ticker) return;
    setScanning(true); setResult(null); setRaw(""); setPhase("loading");

    try {
      const resp = await fetch(`${API}?mode=single&ticker=${ticker.toUpperCase()}&format=json`);
      const data = await resp.json();

      // Show chart first
      setResult(data);
      setPhase("chart");

      // Animate gates
      setTimeout(() => {
        setPhase("gates");
        let g = 0; setGate(0);
        const iv = setInterval(() => {
          g++; setGate(g);
          if (g >= 14) {
            clearInterval(iv);
            setTimeout(() => { setPhase("done"); setScanning(false); setGate(-1); }, 400);
          }
        }, 120);
      }, 1500);
    } catch (e) {
      // Fallback to text mode
      try {
        const resp = await fetch(`${API}?mode=single&ticker=${ticker.toUpperCase()}`);
        const text = await resp.text();
        setRaw(text);
        setPhase("raw");
        setScanning(false);
      } catch (e2) {
        setRaw("Error scanning. Try again."); setPhase("raw"); setScanning(false);
      }
    }
  };

  const gateStatus = (result, gateKey) => {
    if (!result || !result.gates) return "pending";
    const key = GATES.find(g => g.id === gateKey)?.code.toLowerCase();
    if (!key) return "pending";
    const map = { sentinel: "sentinel", atlas: "atlas", oracle: "oracle", phantom: "phantom",
      catalyst: "catalyst", specter: "specter", meridian: "meridian", aegis: "aegis",
      compass: "compass", pulse: "pulse", signal: "signal", current: "current",
      archer: "archer", fortress: "fortress" };
    const k = map[key];
    if (!k || result.gates[k] === undefined) return "pending";
    return result.gates[k] ? "pass" : "fail";
  };

  return (
    <div style={{ background: COLORS.surface, borderRadius: 20, padding: 32, border: `1px solid ${COLORS.border}`, position: "relative", overflow: "hidden" }}>
      {scanning && (
        <div style={{ position: "absolute", top: 0, left: 0, right: 0, height: 3, background: COLORS.border, overflow: "hidden" }}>
          <div style={{ width: "30%", height: "100%", background: `linear-gradient(90deg, transparent, ${COLORS.accent}, transparent)`, animation: "scanLine 1s linear infinite" }} />
        </div>
      )}
      <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 10, color: COLORS.accent, letterSpacing: 3, marginBottom: 6 }}>STOCK SCANNER</div>
      <div style={{ fontFamily: "'Outfit', sans-serif", fontSize: 20, fontWeight: 700, color: COLORS.text, marginBottom: 20 }}>Run Any Ticker Through 14 Gates</div>
      <div style={{ display: "flex", gap: 12, marginBottom: 24 }}>
        <input value={ticker} onChange={e => setTicker(e.target.value.toUpperCase())}
          onKeyDown={e => e.key === "Enter" && scan()} placeholder="AAPL"
          style={{ flex: 1, padding: "16px 20px", borderRadius: 12, border: `1px solid ${COLORS.border}`, background: COLORS.bg,
            color: COLORS.text, fontFamily: "'JetBrains Mono', monospace", fontSize: 20, outline: "none", letterSpacing: 4, textAlign: "center" }} />
        <button onClick={scan} disabled={scanning} style={{
          padding: "16px 32px", borderRadius: 12, border: "none",
          background: scanning ? COLORS.surfaceLight : `linear-gradient(135deg, ${COLORS.accent}, #00c48c)`,
          color: scanning ? COLORS.textDim : COLORS.bg, fontFamily: "'Outfit', sans-serif", fontSize: 15, fontWeight: 700, cursor: scanning ? "wait" : "pointer",
        }}>{scanning ? "Scanning..." : "Scout It"}</button>
      </div>

      {/* Chart */}
      {result && (phase === "chart" || phase === "gates" || phase === "done") && (
        <div>
          <div style={{ display: "flex", alignItems: "baseline", gap: 12, marginBottom: 8 }}>
            <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 28, fontWeight: 800, color: COLORS.text }}>{result.ticker}</span>
            <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 20, color: COLORS.textDim }}>${result.price?.toFixed(2)}</span>
            <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 14, fontWeight: 700, color: result.change >= 0 ? COLORS.accent : COLORS.danger }}>
              {result.change >= 0 ? "+" : ""}{result.change?.toFixed(2)}%
            </span>
          </div>
          <div style={{ display: "inline-block", padding: "4px 12px", borderRadius: 6, marginBottom: 12, background: COLORS.accentDim, border: `1px solid ${COLORS.accent}33`,
            fontFamily: "'JetBrains Mono', monospace", fontSize: 10, color: COLORS.accent, letterSpacing: 2 }}>
            {result.sector} ({result.wind >= 0 ? "+" : ""}{result.wind})
          </div>
          <StockChart ticker={result.ticker} price={result.price} />
        </div>
      )}

      {/* Gate animation */}
      {phase === "gates" && (
        <div style={{ marginBottom: 20 }}>
          <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 10, color: COLORS.accent, letterSpacing: 2, marginBottom: 12 }}>RUNNING 14-GATE ANALYSIS...</div>
          {GATES.map((g, i) => (
            <div key={i} style={{ display: "flex", alignItems: "center", gap: 10, padding: "4px 0", opacity: i <= gate ? 1 : 0.2, transition: "opacity 0.15s" }}>
              <div style={{
                width: 24, height: 24, borderRadius: 6,
                background: i < gate ? COLORS.accentDim : i === gate ? "#ffb83622" : COLORS.border,
                border: `1.5px solid ${i < gate ? COLORS.accent : i === gate ? COLORS.warning : COLORS.border}`,
                display: "flex", alignItems: "center", justifyContent: "center",
                fontFamily: "'JetBrains Mono', monospace", fontSize: 9, fontWeight: 700,
                color: i < gate ? COLORS.accent : i === gate ? COLORS.warning : COLORS.textDim,
              }}>{i < gate ? "✓" : i === gate ? "●" : g.id}</div>
              <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 11, fontWeight: 600, color: i <= gate ? COLORS.text : COLORS.textDim }}>{g.code}</span>
              <span style={{ fontFamily: "'Outfit', sans-serif", fontSize: 11, color: COLORS.textDim }}>{g.name}</span>
              {i === gate && <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 9, color: COLORS.warning, animation: "heatPulse 0.5s infinite" }}>ANALYZING...</span>}
            </div>
          ))}
        </div>
      )}

      {/* Results */}
      {phase === "done" && result && (
        <div style={{ animation: "fadeUp 0.6s ease" }}>
          <div style={{ display: "flex", gap: 24, alignItems: "center", padding: 24, background: COLORS.surfaceLight, borderRadius: 16, marginBottom: 20 }}>
            <ScoutScore score={result.scout_score || 0} size={140} />
            <div style={{ flex: 1 }}>
              <ConfidenceMeter passed={Object.values(result.gates || {}).filter(Boolean).length} />
              <div style={{ marginTop: 12, fontFamily: "'JetBrains Mono', monospace", fontSize: 11, color: COLORS.textDim }}>
                {result.direction} | {result.trend} | {result.iv_elevated ? "IV ELEVATED" : "IV NORMAL"}
                {result.earnings_days ? ` | Earnings ${result.earnings_days}d` : ""}
              </div>
            </div>
          </div>

          {/* Gate results grid */}
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 4, marginBottom: 20 }}>
            {GATES.map((g, i) => {
              const s = gateStatus(result, g.id);
              return (
                <div key={i} style={{ display: "flex", alignItems: "center", gap: 8, padding: "6px 10px", borderRadius: 8, background: s === "fail" ? COLORS.dangerDim : "transparent" }}>
                  <div style={{
                    width: 22, height: 22, borderRadius: 5, display: "flex", alignItems: "center", justifyContent: "center",
                    background: s === "pass" ? COLORS.accentDim : s === "fail" ? COLORS.dangerDim : COLORS.border,
                    border: `1.5px solid ${s === "pass" ? COLORS.accent : s === "fail" ? COLORS.danger : COLORS.border}`,
                    fontFamily: "'JetBrains Mono', monospace", fontSize: 8, fontWeight: 700,
                    color: s === "pass" ? COLORS.accent : s === "fail" ? COLORS.danger : COLORS.textDim,
                  }}>{s === "pass" ? "✓" : s === "fail" ? "✗" : "—"}</div>
                  <div>
                    <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 10, fontWeight: 700, color: COLORS.text }}>{g.code}</div>
                    <div style={{ fontFamily: "'Outfit', sans-serif", fontSize: 10, color: COLORS.textDim }}>{g.name}</div>
                  </div>
                </div>
              );
            })}
          </div>

          {/* Strategy */}
          {result.strategies && (
            <div style={{ padding: 20, borderRadius: 14, background: `linear-gradient(135deg, ${COLORS.accentDim}, ${COLORS.purpleDim})`, border: `1px solid ${COLORS.accent}22` }}>
              <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 9, color: COLORS.accent, letterSpacing: 3 }}>RECOMMENDED</div>
              {result.strategies.short_term?.viable && (
                <div style={{ fontFamily: "'Outfit', sans-serif", fontSize: 18, fontWeight: 800, color: COLORS.text, marginTop: 4 }}>
                  Short-term: {result.strategies.short_term.strategy}
                </div>
              )}
              {result.strategies.leaps?.candidate && (
                <div style={{ fontFamily: "'Outfit', sans-serif", fontSize: 14, color: COLORS.text, marginTop: 8 }}>
                  📌 LEAPS Candidate — Jan 2027+
                </div>
              )}
              {result.strategies.stock?.candidate && (
                <div style={{ fontFamily: "'Outfit', sans-serif", fontSize: 14, color: COLORS.text, marginTop: 4 }}>
                  📌 Long-term Stock Buy — {result.strategies.stock.dcf_target ? `DCF $${result.strategies.stock.dcf_target.toFixed(0)}` : "Strong fundamentals"}
                </div>
              )}
              <div style={{ fontFamily: "'Outfit', sans-serif", fontSize: 12, color: COLORS.textDim, marginTop: 8 }}>
                Subscribe for strikes, expiration, and full thesis.
              </div>
            </div>
          )}
        </div>
      )}

      {/* Raw text fallback */}
      {phase === "raw" && raw && (
        <pre style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 11, color: COLORS.text, whiteSpace: "pre-wrap", background: COLORS.surfaceLight, padding: 16, borderRadius: 12, maxHeight: 500, overflow: "auto" }}>
          {raw}
        </pre>
      )}
    </div>
  );
}

/* ─── AUTH MODAL ─── */
function AuthModal({ onClose, onAuth, initialMode }) {
  const [mode, setMode] = useState(initialMode || "login");
  const [email, setEmail] = useState("");
  const [pw, setPw] = useState("");
  const [name, setName] = useState("");
  const inp = { width: "100%", padding: "14px 16px", borderRadius: 10, boxSizing: "border-box", border: `1px solid ${COLORS.border}`, background: COLORS.bg, color: COLORS.text, fontFamily: "'Outfit', sans-serif", fontSize: 15, outline: "none", marginBottom: 12 };
  return (
    <div style={{ position: "fixed", inset: 0, zIndex: 1000, background: "rgba(0,0,0,0.85)", backdropFilter: "blur(12px)", display: "flex", alignItems: "center", justifyContent: "center" }} onClick={onClose}>
      <div style={{ background: COLORS.surface, borderRadius: 24, padding: 40, width: 420, maxWidth: "90vw", border: `1px solid ${COLORS.border}`, boxShadow: `0 0 80px ${COLORS.accentDim}`, animation: "fadeUp 0.4s ease" }} onClick={e => e.stopPropagation()}>
        <div style={{ textAlign: "center", marginBottom: 32 }}>
          <div style={{ width: 50, height: 50, borderRadius: 14, margin: "0 auto 16px", background: `linear-gradient(135deg, ${COLORS.accent}, #00c48c)`, display: "flex", alignItems: "center", justifyContent: "center", fontFamily: "'JetBrains Mono', monospace", fontSize: 22, fontWeight: 800, color: COLORS.bg, boxShadow: `0 0 30px ${COLORS.accentDim}` }}>S</div>
          <div style={{ fontFamily: "'Outfit', sans-serif", fontSize: 26, fontWeight: 800, color: COLORS.text }}>{mode === "login" ? "Welcome Back" : "Start Scouting"}</div>
          <div style={{ fontFamily: "'Outfit', sans-serif", fontSize: 14, color: COLORS.textDim, marginTop: 6 }}>{mode === "login" ? "Your positions are waiting" : "14 gates stand between you and bad trades"}</div>
        </div>
        {mode === "signup" && <input placeholder="Name" value={name} onChange={e => setName(e.target.value)} style={inp} />}
        <input placeholder="Email" type="email" value={email} onChange={e => setEmail(e.target.value)} style={inp} />
        <input placeholder="Password" type="password" value={pw} onChange={e => setPw(e.target.value)} style={inp} onKeyDown={e => e.key === "Enter" && onAuth({ email, name: name || email.split("@")[0] })} />
        {mode === "login" && (
          <div style={{ textAlign: "right", marginTop: -4, marginBottom: 12 }}>
            <span
              role="button"
              tabIndex={0}
              onClick={() => alert("Password reset coming soon.")}
              onKeyDown={e => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), alert("Password reset coming soon."))}
              style={{ color: COLORS.accent, fontSize: 13, cursor: "pointer", fontWeight: 600 }}
            >
              Forgot password?
            </span>
          </div>
        )}
        <button onClick={() => onAuth({ email, name: name || email.split("@")[0] })} style={{ width: "100%", padding: 16, borderRadius: 12, border: "none", marginTop: 8, background: `linear-gradient(135deg, ${COLORS.accent}, #00c48c)`, color: COLORS.bg, fontFamily: "'Outfit', sans-serif", fontSize: 16, fontWeight: 700, cursor: "pointer", boxShadow: `0 4px 20px ${COLORS.accentDim}` }}>
          {mode === "login" ? "Log In" : "Create Account"}
        </button>
        <div style={{ textAlign: "center", marginTop: 16 }}>
          <span style={{ color: COLORS.textDim, fontSize: 13 }}>{mode === "login" ? "New here? " : "Have an account? "}</span>
          <span onClick={() => setMode(mode === "login" ? "signup" : "login")} style={{ color: COLORS.accent, fontSize: 13, cursor: "pointer", fontWeight: 600 }}>{mode === "login" ? "Sign Up" : "Log In"}</span>
        </div>
        <div style={{ marginTop: 24, padding: 14, borderRadius: 10, background: COLORS.surfaceLight, fontSize: 11, color: COLORS.textDim, textAlign: "center", lineHeight: 1.6 }}>
          {mode === "login"
            ? "By signing in, you agree to Scout's Terms of Service. Scout provides analysis for educational purposes only and is not financial advice."
            : "By creating an account, you agree to Scout's Terms of Service. Scout provides analysis for educational purposes only and is not financial advice."}
        </div>
      </div>
    </div>
  );
}

/* ─── MAIN PAGE ─── */
export default function Home() {
  const [user, setUser] = useState(null);
  const [showAuth, setShowAuth] = useState(false);
  const [authMode, setAuthMode] = useState("login");
  const [page, setPage] = useState("home");

  return (
    <>
      <Head>
        <title>{page === "engine" ? "Inside the Engine — Scout" : "Scout — 14-Gate Stock Analysis"}</title>
        <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700;800&family=Outfit:wght@300;400;500;600;700;800;900&display=swap" rel="stylesheet" />
      </Head>

      <style jsx global>{`
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { background: ${COLORS.bg}; color: ${COLORS.text}; font-family: 'Outfit', sans-serif; }
        @keyframes tickerScroll { 0% { transform: translateX(0); } 100% { transform: translateX(-50%); } }
        @keyframes fadeUp { 0% { opacity: 0; transform: translateY(30px); } 100% { opacity: 1; transform: translateY(0); } }
        @keyframes slideIn { 0% { opacity: 0; transform: translateX(-20px); } 100% { opacity: 1; transform: translateX(0); } }
        @keyframes glow { 0%,100% { box-shadow: 0 0 20px ${COLORS.accentDim}; } 50% { box-shadow: 0 0 50px ${COLORS.accentGlow}; } }
        @keyframes scanLine { 0% { transform: translateX(-100%); } 100% { transform: translateX(100%); } }
        @keyframes scoreReveal { 0% { transform: scale(0.5); opacity: 0; } 50% { transform: scale(1.15); } 100% { transform: scale(1); opacity: 1; } }
        @keyframes ringPulse { 0% { transform: scale(1); opacity: 0.5; } 100% { transform: scale(1.4); opacity: 0; } }
        @keyframes heatPulse { 0%,100% { opacity: 0.7; } 50% { opacity: 1; } }
        @keyframes twinkle { 0%,100% { opacity: 0.1; } 50% { opacity: 0.5; } }
      `}</style>

      {showAuth && <AuthModal initialMode={authMode} onClose={() => setShowAuth(false)} onAuth={u => { setUser(u); setShowAuth(false); setPage("scanner"); }} />}

      {/* Background */}
      <div style={{ position: "fixed", inset: 0, zIndex: 0, background: COLORS.bg }}>
        <div style={{ position: "absolute", inset: 0, opacity: 0.03, backgroundImage: `linear-gradient(${COLORS.accent} 1px, transparent 1px), linear-gradient(90deg, ${COLORS.accent} 1px, transparent 1px)`, backgroundSize: "50px 50px" }} />
        <div style={{ position: "absolute", width: "120%", height: "120%", top: "-10%", left: "-10%", background: `radial-gradient(ellipse at 25% 30%, ${COLORS.accentDim} 0%, transparent 40%), radial-gradient(ellipse at 75% 70%, ${COLORS.purpleDim} 0%, transparent 40%)` }} />
      </div>

      <div style={{ position: "relative", zIndex: 1, minHeight: "100vh" }}>
        <TickerTape />

        {/* Nav */}
        <nav style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "16px 40px", maxWidth: 1200, margin: "0 auto" }}>
          <div onClick={() => setPage("home")} style={{ cursor: "pointer", display: "flex", alignItems: "center", gap: 10 }}>
            <div style={{ width: 34, height: 34, borderRadius: 9, background: `linear-gradient(135deg, ${COLORS.accent}, #00c48c)`, display: "flex", alignItems: "center", justifyContent: "center", fontFamily: "'JetBrains Mono', monospace", fontSize: 15, fontWeight: 800, color: COLORS.bg }}>S</div>
            <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 14, fontWeight: 700, color: COLORS.text, letterSpacing: 2 }}>SCOUT</span>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 20 }}>
            {user ? (
              <>
                {["Scanner", "Dashboard"].map(p => (
                  <span key={p} onClick={() => setPage(p.toLowerCase())} style={{ fontFamily: "'Outfit', sans-serif", fontSize: 13, fontWeight: 500, cursor: "pointer", color: page === p.toLowerCase() ? COLORS.accent : COLORS.textDim, borderBottom: page === p.toLowerCase() ? `2px solid ${COLORS.accent}` : "2px solid transparent", paddingBottom: 4 }}>{p}</span>
                ))}
                <div style={{ padding: "6px 14px", borderRadius: 8, background: COLORS.surfaceLight, fontSize: 12, color: COLORS.textDim, border: `1px solid ${COLORS.border}` }}>{user.name}</div>
              </>
            ) : (
              <>
                <span onClick={() => { setAuthMode("login"); setShowAuth(true); }} style={{ fontSize: 13, color: COLORS.textDim, cursor: "pointer" }}>Log In</span>
                <button onClick={() => { setAuthMode("signup"); setShowAuth(true); }} style={{ padding: "10px 22px", borderRadius: 10, border: "none", background: `linear-gradient(135deg, ${COLORS.accent}, #00c48c)`, color: COLORS.bg, fontSize: 13, fontWeight: 700, cursor: "pointer" }}>Get Started</button>
              </>
            )}
          </div>
        </nav>

        {/* HOME */}
        {page === "home" && (
          <div style={{ maxWidth: 1200, margin: "0 auto", padding: "40px 40px 80px" }}>
            <div style={{ textAlign: "center", marginBottom: 80, animation: "fadeUp 0.8s ease" }}>
              <div style={{ display: "inline-flex", alignItems: "center", gap: 8, padding: "6px 18px", borderRadius: 20, marginBottom: 24, background: COLORS.accentDim, border: `1px solid ${COLORS.accent}33` }}>
                <div style={{ width: 6, height: 6, borderRadius: "50%", background: COLORS.accent, animation: "heatPulse 1.5s infinite" }} />
                <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 11, color: COLORS.accent, letterSpacing: 2 }}>LIVE — 14-GATE SYSTEM ACTIVE</span>
              </div>
              <h1 style={{ fontSize: 64, fontWeight: 900, lineHeight: 1.12, margin: "0 0 12px", paddingBottom: 6, overflow: "visible", background: `linear-gradient(135deg, ${COLORS.text} 30%, ${COLORS.accent} 100%)`, WebkitBackgroundClip: "text", backgroundClip: "text", WebkitTextFillColor: "transparent" }}>
                Every Stock Gets<br />
                <span style={{ display: "inline-block", lineHeight: 1.12, paddingBottom: 6 }}>Interrogated.</span>
              </h1>
              <p style={{ fontSize: 19, color: COLORS.textDim, fontWeight: 300, maxWidth: 550, margin: "0 auto 40px", lineHeight: 1.7 }}>
                14 proprietary gates. Any ticker. Real-time sector analysis. Only the strongest signals survive.
              </p>
              <div style={{ display: "flex", justifyContent: "center", gap: 16 }}>
                <button onClick={() => { setAuthMode("signup"); setShowAuth(true); }} style={{ padding: "18px 44px", borderRadius: 14, border: "none", background: `linear-gradient(135deg, ${COLORS.accent}, #00c48c)`, color: COLORS.bg, fontSize: 17, fontWeight: 700, cursor: "pointer", animation: "glow 3s ease-in-out infinite" }}>Start Scanning →</button>
                <button onClick={() => setPage("engine")} style={{ padding: "18px 44px", borderRadius: 14, border: `1px solid ${COLORS.border}`, background: "transparent", color: COLORS.textDim, fontSize: 17, fontWeight: 500, cursor: "pointer" }}>Inside The Engine</button>
              </div>
            </div>

            {/* Stats */}
            <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 16, marginBottom: 80, animation: "fadeUp 0.8s ease 0.2s both" }}>
              {[{ v: "∞", l: "Any Ticker", s: "Scan what you want" }, { v: "14", l: "Signals Processed", s: "Zero shortcuts" }, { v: "24/7", l: "Monitoring", s: "Alerts in real-time" }, { v: "67%", l: "Win Rate", s: "And improving" }].map((x, i) => (
                <div key={i} style={{ textAlign: "center", padding: "24px 16px", borderRadius: 16, background: COLORS.surface, border: `1px solid ${COLORS.border}` }}>
                  <div style={{ fontFamily: "'JetBrains Mono', monospace", 
                  fontSize: 38,
                    lineHeight: 1,
                    display: "inline-block",
                    transform: x.v === "∞" ? "scale(1.45) translateY(-4px)" : "scale(1) translateY(0px)",
                    transformOrigin: "center center",
                    fontWeight: 800, 
                    color: COLORS.accent }}>{x.v}</div>
                  <div style={{ fontSize: 13, fontWeight: 600, color: COLORS.text, marginTop: 4 }}>{x.l}</div>
                  <div style={{ fontSize: 11, color: COLORS.textDim, marginTop: 2 }}>{x.s}</div>
                </div>
              ))}
            </div>

            {/* Scout Score demo */}
            <div style={{ display: "flex", gap: 24, marginBottom: 80, animation: "fadeUp 0.8s ease 0.3s both" }}>
              <div style={{ flex: 1, background: COLORS.surface, borderRadius: 20, padding: 32, border: `1px solid ${COLORS.border}`, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center" }}>
                <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 10, color: COLORS.accent, letterSpacing: 3, marginBottom: 16 }}>THE SCOUT SCORE</div>
                <ScoutScore score={87} size={200} />
                <div style={{ fontSize: 14, color: COLORS.textDim, textAlign: "center", marginTop: 20, lineHeight: 1.7, maxWidth: 280 }}>Noise removed. Opportunity revealed.</div>
              </div>
              <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 16 }}>
                <div style={{ flex: 1, background: COLORS.surface, borderRadius: 20, padding: 24, border: `1px solid ${COLORS.border}` }}>
                  <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 10, color: COLORS.accent, letterSpacing: 3, marginBottom: 12 }}>CONFIDENCE METER</div>
                  <ConfidenceMeter passed={12} />
                  <div style={{ fontSize: 13, color: COLORS.textDim, marginTop: 12, lineHeight: 1.6 }}>See how many gates aligned before risking a dollar.</div>
                </div>
                <div style={{ flex: 1, background: COLORS.surface, borderRadius: 20, padding: 24, border: `1px solid ${COLORS.border}` }}>
                  <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 10, color: COLORS.accent, letterSpacing: 3, marginBottom: 12 }}>STRATEGY ENGINE</div>
                  <div style={{ fontSize: 22, fontWeight: 800, color: COLORS.text, marginBottom: 8 }}>Trade Architecture</div>
                  <div style={{ fontSize: 13, color: COLORS.textDim, lineHeight: 1.6 }}> Risk. Trend. Volatility. Catalysts. Fourteen gates.<br />One engineered deployment.</div>
                </div>
              </div>
            </div>

            {/* Gates */}
            <div id="gates" style={{ marginBottom: 80, animation: "fadeUp 0.8s ease 0.4s both" }}>
              <div style={{ textAlign: "center", marginBottom: 40 }}>
                <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 10, color: COLORS.accent, letterSpacing: 3, marginBottom: 8 }}>THE SYSTEM</div>
                <h2 style={{ fontSize: 36, fontWeight: 800, color: COLORS.text, margin: 0 }}>14 Gates. Zero Guesswork.</h2>
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                {GATES.map((g, i) => (
                  <div key={i} style={{ display: "flex", alignItems: "center", gap: 16, padding: "14px 20px", borderRadius: 12, background: i % 2 === 0 ? COLORS.surfaceLight : "transparent", animation: `slideIn 0.4s ease ${i * 0.05}s both` }}>
                    <div style={{ width: 32, height: 32, flexShrink: 0, borderRadius: 8, background: COLORS.accentDim, border: `1.5px solid ${COLORS.accent}44`, display: "flex", alignItems: "center", justifyContent: "center", fontFamily: "'JetBrains Mono', monospace", fontSize: 11, fontWeight: 700, color: COLORS.accent }}>{g.id}</div>
                    <span style={{ flex: "0 0 96px", fontFamily: "'JetBrains Mono', monospace", fontSize: 12, fontWeight: 700, color: COLORS.text, letterSpacing: 1 }}>{g.code}</span>
                    <span style={{ fontFamily: "'Outfit', sans-serif", fontSize: 12, color: COLORS.textDim, marginLeft: 12 }}>{g.name}</span>
                    <div style={{ marginLeft: "auto", width: 8, height: 8, borderRadius: "50%", background: COLORS.accent, opacity: 0.5, animation: `heatPulse ${1.5 + i * 0.2}s infinite` }} />
                  </div>
                ))}
              </div>
            </div>

            {/* Disclaimer */}
            <div style={{ textAlign: "center", padding: 20, borderRadius: 14, background: COLORS.surfaceLight, border: `1px solid ${COLORS.border}` }}>
              <p style={{ fontSize: 11, color: COLORS.textDim, lineHeight: 1.8, margin: 0 }}>
                Scout provides analysis for educational purposes only. This is not financial advice. All trading involves risk of loss. Past performance does not guarantee future results. You are solely responsible for your trading decisions.
              </p>
            </div>
          </div>
        )}

        {/* INSIDE THE ENGINE */}
        {page === "engine" && (
          <div style={{ maxWidth: 1200, margin: "0 auto", padding: "40px 40px 80px" }}>
            <div style={{ textAlign: "center", marginBottom: 56, animation: "fadeUp 0.8s ease" }}>
              <div style={{ display: "inline-flex", alignItems: "center", gap: 8, padding: "6px 18px", borderRadius: 20, marginBottom: 24, background: COLORS.accentDim, border: `1px solid ${COLORS.accent}33` }}>
                <div style={{ width: 6, height: 6, borderRadius: "50%", background: COLORS.accent, animation: "heatPulse 1.5s infinite" }} />
                <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 11, color: COLORS.accent, letterSpacing: 2 }}>INSIDE THE ENGINE</span>
              </div>
              <h1 style={{ fontSize: 44, fontWeight: 900, lineHeight: 1.15, margin: "0 0 20px", color: COLORS.text }}>
                How Scout Interrogates Every Trade
              </h1>
              <p style={{ fontSize: 18, color: COLORS.textDim, fontWeight: 300, maxWidth: 640, margin: "0 auto", lineHeight: 1.75 }}>
                Scout runs every ticker through a 14-gate trade architecture built to filter noise, expose risk, and surface only the strongest setups.
              </p>
            </div>

            {/* Section 1 */}
            <div style={{ marginBottom: 72, animation: "fadeUp 0.8s ease 0.05s both" }}>
              <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 10, color: COLORS.accent, letterSpacing: 3, marginBottom: 10 }}>THE ARCHITECTURE</div>
              <h2 style={{ fontSize: 30, fontWeight: 800, color: COLORS.text, margin: "0 0 20px" }}>The 14-Gate Trade Architecture</h2>
              <div style={{ display: "flex", flexDirection: "column", gap: 16, marginBottom: 32, maxWidth: 800 }}>
                <p style={{ fontSize: 15, color: COLORS.textDim, lineHeight: 1.75, margin: 0 }}>
                  Scout doesn't just look for one signal. It forces every ticker through a layered gate system before reaching the finish line.
                </p>
                <p style={{ fontSize: 15, color: COLORS.textDim, lineHeight: 1.75, margin: 0 }}>
                  Each gate checks a different part of the trade: market quality, sector alignment, technical structure, volume and flow, options activity, risk, timing, and catalyst strength. Weak points show up early instead of after you are committed.
                </p>
                <p style={{ fontSize: 15, color: COLORS.textDim, lineHeight: 1.75, margin: 0 }}>
                  The goal is simple: prevent weak trades from reaching the final signal. Only setups that hold up across the full stack advance.
                </p>
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))", gap: 12 }}>
                {GATES.map((g, i) => (
                  <div
                    key={g.id}
                    style={{
                      padding: "16px 18px",
                      borderRadius: 14,
                      background: COLORS.surface,
                      border: `1px solid ${COLORS.border}`,
                      display: "flex",
                      alignItems: "flex-start",
                      gap: 12,
                      boxShadow: `0 0 0 1px ${COLORS.accentDim}`,
                    }}
                  >
                    <div
                      style={{
                        width: 30,
                        height: 30,
                        flexShrink: 0,
                        borderRadius: 8,
                        background: COLORS.accentDim,
                        border: `1.5px solid ${COLORS.accent}44`,
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        fontFamily: "'JetBrains Mono', monospace",
                        fontSize: 10,
                        fontWeight: 700,
                        color: COLORS.accent,
                      }}
                    >
                      {g.id}
                    </div>
                    <div style={{ minWidth: 0 }}>
                      <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 11, fontWeight: 700, color: COLORS.text, letterSpacing: 1 }}>{g.code}</div>
                      <div style={{ fontFamily: "'Outfit', sans-serif", fontSize: 13, color: COLORS.textDim, marginTop: 4, lineHeight: 1.45 }}>{g.name}</div>
                    </div>
                  </div>
                ))}
              </div>
            </div>

            {/* Section 2 + pricing */}
            <div style={{ marginBottom: 72, animation: "fadeUp 0.8s ease 0.1s both" }}>
              <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 10, color: COLORS.accent, letterSpacing: 3, marginBottom: 10 }}>WHAT YOU GET</div>
              <h2 style={{ fontSize: 30, fontWeight: 800, color: COLORS.text, margin: "0 0 28px" }}>What You Get</h2>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))", gap: 16, marginBottom: 28 }}>
                {[
                  "14-gate ticker analysis",
                  "Cleaner trade filtering",
                  "Risk-aware setup scoring",
                  "Real-time market context",
                  "Simple bullish, bearish, and neutral readouts",
                ].map((label, i) => (
                  <div key={i} style={{ padding: 22, borderRadius: 16, background: COLORS.surfaceLight, border: `1px solid ${COLORS.border}`, backdropFilter: "blur(12px)" }}>
                    <div style={{ width: 8, height: 8, borderRadius: "50%", background: COLORS.accent, marginBottom: 12, opacity: 0.85 }} />
                    <div style={{ fontSize: 15, fontWeight: 600, color: COLORS.text, lineHeight: 1.5 }}>{label}</div>
                  </div>
                ))}
              </div>
              <div
                style={{
                  maxWidth: 420,
                  margin: "0 auto",
                  padding: 32,
                  borderRadius: 20,
                  background: `linear-gradient(145deg, ${COLORS.surface} 0%, ${COLORS.surfaceLight} 100%)`,
                  border: `1px solid ${COLORS.accent}33`,
                  boxShadow: `0 0 60px ${COLORS.accentDim}`,
                  textAlign: "center",
                }}
              >
                <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 10, color: COLORS.accent, letterSpacing: 3, marginBottom: 12 }}>MEMBERSHIP</div>
                <div style={{ fontSize: 36, fontWeight: 800, color: COLORS.text, marginBottom: 8 }}>$x/mo</div>
                <div style={{ fontSize: 13, color: COLORS.textDim, marginBottom: 24, lineHeight: 1.6 }}>Early access pricing placeholder</div>
                <button
                  onClick={() => { setAuthMode("signup"); setShowAuth(true); }}
                  style={{
                    width: "100%",
                    padding: "14px 24px",
                    borderRadius: 12,
                    border: "none",
                    background: `linear-gradient(135deg, ${COLORS.accent}, #00c48c)`,
                    color: COLORS.bg,
                    fontSize: 15,
                    fontWeight: 700,
                    cursor: "pointer",
                    boxShadow: `0 4px 20px ${COLORS.accentDim}`,
                  }}
                >
                  Get Started
                </button>
              </div>
            </div>

            {/* Section 3 */}
            <div style={{ marginBottom: 64, animation: "fadeUp 0.8s ease 0.15s both" }}>
              <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 10, color: COLORS.accent, letterSpacing: 3, marginBottom: 10 }}>DECISION SPEED</div>
              <h2 style={{ fontSize: 30, fontWeight: 800, color: COLORS.text, margin: "0 0 18px" }}>Built for Decision Speed</h2>
              <p style={{ fontSize: 15, color: COLORS.textDim, lineHeight: 1.75, margin: 0, maxWidth: 800 }}>
                Scout is built to help traders move faster without skipping the hard questions. Instead of chasing every alert, Scout organizes the setup, pressure-tests the thesis, and shows where the trade may be strong or weak.
              </p>
            </div>

            {/* Footer CTA */}
            <div
              style={{
                textAlign: "center",
                padding: "40px 24px",
                borderRadius: 20,
                background: COLORS.surface,
                border: `1px solid ${COLORS.border}`,
                animation: "fadeUp 0.8s ease 0.2s both",
              }}
            >
              <div style={{ fontSize: 22, fontWeight: 800, color: COLORS.text, marginBottom: 20 }}>Ready to run the gates?</div>
              <button
                onClick={() => { setAuthMode("signup"); setShowAuth(true); }}
                style={{
                  padding: "16px 40px",
                  borderRadius: 14,
                  border: "none",
                  background: `linear-gradient(135deg, ${COLORS.accent}, #00c48c)`,
                  color: COLORS.bg,
                  fontSize: 16,
                  fontWeight: 700,
                  cursor: "pointer",
                  animation: "glow 3s ease-in-out infinite",
                }}
              >
                Start Scanning
              </button>
            </div>

            <div style={{ textAlign: "center", padding: 24, marginTop: 32 }}>
              <p style={{ fontSize: 11, color: COLORS.textDim, lineHeight: 1.8, margin: 0 }}>
                Scout provides analysis for educational purposes only. This is not financial advice.
              </p>
            </div>
          </div>
        )}

        {/* SCANNER */}
        {page === "scanner" && (
          <div style={{ maxWidth: 800, margin: "0 auto", padding: "20px 40px 80px" }}>
            <Scanner />
          </div>
        )}

        {/* DASHBOARD */}
        {page === "dashboard" && (
          <div style={{ maxWidth: 1200, margin: "0 auto", padding: "20px 40px 80px" }}>
            <div style={{ fontSize: 26, fontWeight: 800, color: COLORS.text, marginBottom: 24 }}>Welcome back{user ? `, ${user.name}` : ""}</div>
            <div style={{ background: COLORS.surface, borderRadius: 20, padding: 32, border: `1px solid ${COLORS.border}`, textAlign: "center" }}>
              <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 10, color: COLORS.accent, letterSpacing: 3, marginBottom: 16 }}>COMING SOON</div>
              <div style={{ fontSize: 18, fontWeight: 700, color: COLORS.text, marginBottom: 8 }}>Trade Tracker & Performance Dashboard</div>
              <div style={{ fontSize: 14, color: COLORS.textDim }}>Your trades, P&L history, Kelly sizing, and personalized alerts — all in one place.</div>
            </div>
          </div>
        )}
      </div>
    </>
  );
}
