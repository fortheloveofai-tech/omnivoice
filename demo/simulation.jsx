import { useState, useEffect, useRef, useCallback } from "react";

// ── Simulation constants ──────────────────────────────────────────
const SAMPLE_CONVERSATIONS = [
  {
    user: "What's the weather like in San Francisco today?",
    assistant: "San Francisco is looking foggy this morning with temperatures around 58 degrees. Expect the fog to burn off by noon, with highs reaching 68 in the afternoon.",
  },
  {
    user: "Set a timer for ten minutes.",
    assistant: "Sure, I've set a timer for ten minutes. I'll let you know when it goes off.",
  },
  {
    user: "Can you summarize the main points of the last meeting?",
    assistant: "The last meeting covered three main topics: the Q3 roadmap update, a discussion about expanding the engineering team, and a review of customer feedback from last month's release.",
  },
  {
    user: "Play something relaxing.",
    assistant: "Playing ambient music for you now. I've queued up a lo-fi chill playlist — enjoy!",
  },
];

const ATTS_STATES = ["idle", "listening", "thinking", "speaking", "interrupt"];

const STATE_COLORS = {
  idle:      "#64748b",
  listening: "#3b82f6",
  thinking:  "#f59e0b",
  speaking:  "#10b981",
  interrupt: "#ef4444",
};

const PIPELINE_STAGES = ["ASR", "ATTS", "LLM", "TAB", "AQAL", "TTS"];

const STAGE_COLORS = {
  ASR:  "#6366f1",
  ATTS: "#3b82f6",
  LLM:  "#f59e0b",
  TAB:  "#8b5cf6",
  AQAL: "#06b6d4",
  TTS:  "#10b981",
};

const STAGE_LATENCIES = { ASR: 80, ATTS: 5, LLM: 120, TAB: 20, AQAL: 10, TTS: 65 };

// ── Helpers ───────────────────────────────────────────────────────
function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

function fmtMs(ms) {
  if (ms == null) return "—";
  return ms < 1 ? `<1ms` : `${Math.round(ms)}ms`;
}

// Leaky bucket simulation
class LeakyBucket {
  constructor(capacity = 10, leakRate = 0.01) {
    this.capacity = capacity;
    this.leakRate = leakRate;
    this.level = 0;
    this.lastTs = Date.now();
  }
  _drain() {
    const now = Date.now();
    const elapsed = now - this.lastTs;
    this.level = Math.max(0, this.level - elapsed * this.leakRate);
    this.lastTs = now;
  }
  add(n = 1) { this._drain(); this.level = Math.min(this.capacity, this.level + n); return this.level; }
  consume(n = 1) { this._drain(); if (this.level >= n) { this.level -= n; return true; } return false; }
  get fillPct() { this._drain(); return this.level / this.capacity; }
}

// ── Main simulation component ─────────────────────────────────────
export default function OmniVoiceSimulation() {
  const [attsState, setAttsState] = useState("idle");
  const [activeStages, setActiveStages] = useState({});
  const [stageLatencies, setStageLatencies] = useState({});
  const [tabLevel, setTabLevel] = useState(0);
  const [tabBackpressure, setTabBackpressure] = useState(false);
  const [aqalFill, setAqalFill] = useState(0);
  const [codec, setCodec] = useState("opus_24khz_64kbps");
  const [rttMs, setRttMs] = useState(28);
  const [lossP, setLossP] = useState(0.2);
  const [bwKbps, setBwKbps] = useState(1000);
  const [tokens, setTokens] = useState([]);
  const [conversation, setConversation] = useState([]);
  const [e2eMs, setE2eMs] = useState(null);
  const [running, setRunning] = useState(false);
  const [convIndex, setConvIndex] = useState(0);
  const [tokenStream, setTokenStream] = useState("");
  const [metrics, setMetrics] = useState({ asr: null, llm: null, tts: null, e2e: null });
  const [audioWave, setAudioWave] = useState([]);
  const [networkMode, setNetworkMode] = useState("good");
  const aqalRef = useRef(new LeakyBucket(30, 0.05));
  const convRef = useRef(null);
  const cancelRef = useRef(false);

  // ── Network presets ─────────────────────────────────────────────
  const NETWORK_PRESETS = {
    good:    { rtt: 28,  loss: 0.1,  bw: 1000, label: "🟢 Good (WiFi)",       codec: "opus_24khz_64kbps" },
    mobile:  { rtt: 55,  loss: 1.2,  bw: 500,  label: "🟡 Mobile (4G LTE)",    codec: "opus_16khz_48kbps" },
    poor:    { rtt: 120, loss: 3.8,  bw: 180,  label: "🔴 Poor (3G/congested)", codec: "evs_8khz_32kbps"  },
  };

  useEffect(() => {
    const preset = NETWORK_PRESETS[networkMode];
    setRttMs(preset.rtt);
    setLossP(preset.loss);
    setBwKbps(preset.bw);
    setCodec(preset.codec);
  }, [networkMode]);

  // Animated audio waveform
  useEffect(() => {
    let frame;
    const tick = () => {
      if (attsState === "listening") {
        setAudioWave(Array.from({ length: 24 }, () => Math.random() * 0.8 + 0.1));
      } else if (attsState === "speaking") {
        setAudioWave(Array.from({ length: 24 }, () => Math.random() * 0.6 + 0.05));
      } else {
        setAudioWave(Array.from({ length: 24 }, () => 0.05));
      }
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [attsState]);

  // ── Stage activation helpers ────────────────────────────────────
  const activateStage = useCallback((stage) => {
    setActiveStages(s => ({ ...s, [stage]: true }));
  }, []);
  const deactivateStage = useCallback((stage) => {
    setActiveStages(s => ({ ...s, [stage]: false }));
  }, []);
  const recordLatency = useCallback((stage, ms) => {
    setStageLatencies(s => ({ ...s, [stage]: ms }));
  }, []);

  // ── Main pipeline simulation ────────────────────────────────────
  const runConversationTurn = useCallback(async (turn) => {
    cancelRef.current = false;
    const preset = NETWORK_PRESETS[networkMode];
    const networkJitter = preset.rtt * 0.15;

    const t0 = Date.now();

    // 1. IDLE → LISTENING
    setAttsState("listening");
    setConversation(c => [...c, { role: "user", text: turn.user, partial: true }]);
    setTokenStream("");
    setTokens([]);

    // Simulate audio frames arriving
    activateStage("ASR");
    for (let i = 0; i < 8; i++) {
      if (cancelRef.current) return;
      await sleep(20 + Math.random() * 10);
    }

    // 2. ASR completes
    const asrMs = 65 + Math.random() * 30 + (preset.rtt / 3);
    await sleep(asrMs);
    recordLatency("ASR", asrMs);
    deactivateStage("ASR");
    setConversation(c => c.map((t, i) => i === c.length - 1 ? { ...t, partial: false } : t));

    if (cancelRef.current) return;

    // 3. ATTS: silence detected → THINKING
    activateStage("ATTS");
    await sleep(preset.rtt + networkJitter);
    setAttsState("thinking");
    recordLatency("ATTS", preset.rtt);
    deactivateStage("ATTS");

    if (cancelRef.current) return;

    // 4. LLM starts generating
    activateStage("LLM");
    const words = turn.assistant.split(" ");
    const llmTokens = [];
    let firstTokenMs = null;
    const t1 = Date.now();

    for (let i = 0; i < words.length; i++) {
      if (cancelRef.current) return;
      const tokenDelay = i === 0
        ? 80 + Math.random() * 40   // first token latency
        : 25 + Math.random() * 20;  // subsequent tokens
      await sleep(tokenDelay);

      if (i === 0) {
        firstTokenMs = Date.now() - t1;
        recordLatency("LLM", firstTokenMs);
        setAttsState("speaking");
        // Start TAB and AQAL
        activateStage("TAB");
        activateStage("AQAL");
      }

      const word = words[i];
      llmTokens.push(word);
      setTokens([...llmTokens]);

      // TAB: update buffer level
      const tabFill = Math.min(1, (i + 1) / Math.max(words.length, 1));
      const tabLevelVal = tabFill * 0.18;
      setTabLevel(tabLevelVal);
      setTabBackpressure(tabLevelVal > 0.15);

      // AQAL: update bucket
      aqalRef.current.add(1);
      setAqalFill(aqalRef.current.fillPct);

      // Stream tokens to display
      setTokenStream(t => t + (i > 0 ? " " : "") + word);
    }

    deactivateStage("LLM");
    recordLatency("LLM", Date.now() - t1);

    if (cancelRef.current) return;

    // 5. TAB drains
    await sleep(30);
    setTabLevel(0);
    setTabBackpressure(false);
    deactivateStage("TAB");
    recordLatency("TAB", 18 + Math.random() * 5);

    // 6. AQAL normalises
    aqalRef.current.level = 0;
    setAqalFill(0);
    deactivateStage("AQAL");

    // 7. TTS synthesis
    activateStage("TTS");
    const ttsMs = 45 + Math.random() * 30;
    await sleep(ttsMs);
    recordLatency("TTS", ttsMs);
    deactivateStage("TTS");

    if (cancelRef.current) return;

    // 8. Done — IDLE
    const totalMs = Date.now() - t0;
    setE2eMs(totalMs);
    setMetrics({ asr: asrMs, llm: firstTokenMs, tts: ttsMs, e2e: totalMs });
    setAttsState("idle");
    setConversation(c => [...c, { role: "assistant", text: turn.assistant }]);
    setTokenStream("");

  }, [networkMode, activateStage, deactivateStage, recordLatency]);

  const handleSpeak = useCallback(async () => {
    if (running) {
      cancelRef.current = true;
      setRunning(false);
      setAttsState("idle");
      setActiveStages({});
      setTabLevel(0); setTabBackpressure(false); setAqalFill(0); setTokenStream("");
      return;
    }
    setRunning(true);
    const turn = SAMPLE_CONVERSATIONS[convIndex % SAMPLE_CONVERSATIONS.length];
    setConvIndex(i => i + 1);
    await runConversationTurn(turn);
    setRunning(false);
  }, [running, convIndex, runConversationTurn]);

  // Auto scroll conversation
  useEffect(() => {
    if (convRef.current) convRef.current.scrollTop = convRef.current.scrollHeight;
  }, [conversation, tokenStream]);

  // ── Render ────────────────────────────────────────────────────────
  return (
    <div style={{ background: "#0d0d14", minHeight: "100vh", color: "#e2e2f0", fontFamily: "'Segoe UI', system-ui, sans-serif", padding: "1.5rem", maxWidth: 960, margin: "0 auto" }}>

      {/* Header */}
      <div style={{ textAlign: "center", marginBottom: "1.5rem" }}>
        <h1 style={{ fontSize: "1.6rem", fontWeight: 800, background: "linear-gradient(135deg,#7c6ff7,#a78bfa)", WebkitBackgroundClip: "text", WebkitTextFillColor: "transparent", margin: 0 }}>
          🎙 Omni Voice — Pipeline Simulation
        </h1>
        <p style={{ color: "#8888aa", fontSize: "0.85rem", marginTop: 4 }}>
          Simulates the full real-time voice AI pipeline end-to-end
        </p>
      </div>

      {/* Top row: Controls + ATTS state */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "1rem", marginBottom: "1rem" }}>

        {/* Controls */}
        <div style={card}>
          <Label>Controls</Label>

          {/* Network preset */}
          <div style={{ marginBottom: "0.75rem" }}>
            <div style={{ fontSize: "0.75rem", color: "#8888aa", marginBottom: 6 }}>Network Condition</div>
            <div style={{ display: "flex", gap: 6 }}>
              {Object.entries(NETWORK_PRESETS).map(([key, p]) => (
                <button key={key} onClick={() => setNetworkMode(key)} style={{
                  flex: 1, padding: "5px 0", borderRadius: 6, border: "1px solid",
                  borderColor: networkMode === key ? "#7c6ff7" : "#2e2e3e",
                  background: networkMode === key ? "#2a2040" : "#0d0d14",
                  color: networkMode === key ? "#a78bfa" : "#666",
                  fontSize: "0.7rem", cursor: "pointer", fontWeight: 600,
                }}>
                  {p.label}
                </button>
              ))}
            </div>
          </div>

          {/* Network stats */}
          <div style={{ display: "flex", gap: 8, marginBottom: "1rem" }}>
            {[["RTT", `${rttMs}ms`, rttMs < 40 ? "#10b981" : rttMs < 80 ? "#f59e0b" : "#ef4444"],
              ["Loss", `${lossP}%`, lossP < 1 ? "#10b981" : lossP < 3 ? "#f59e0b" : "#ef4444"],
              ["BW", `${bwKbps}k`, bwKbps > 500 ? "#10b981" : bwKbps > 200 ? "#f59e0b" : "#ef4444"]
            ].map(([label, val, col]) => (
              <div key={label} style={{ flex: 1, background: "#0a0a10", borderRadius: 8, padding: "6px 8px", textAlign: "center" }}>
                <div style={{ fontSize: "0.65rem", color: "#8888aa" }}>{label}</div>
                <div style={{ fontSize: "1rem", fontWeight: 700, color: col }}>{val}</div>
              </div>
            ))}
          </div>

          {/* Mic button */}
          <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 8 }}>
            <button onClick={handleSpeak} style={{
              width: 72, height: 72, borderRadius: "50%", border: "none",
              background: running ? "#ef4444" : "#7c6ff7",
              fontSize: "1.6rem", cursor: "pointer",
              boxShadow: running ? "0 0 0 0 rgba(239,68,68,0.5)" : "none",
              animation: running ? "pulse 1.2s infinite" : "none",
            }}>
              {running ? "⏹" : "🎤"}
            </button>
            <style>{`@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(239,68,68,0.5)}70%{box-shadow:0 0 0 14px rgba(239,68,68,0)}100%{box-shadow:0 0 0 0 rgba(239,68,68,0)}}`}</style>
            <span style={{ fontSize: "0.78rem", color: "#8888aa" }}>
              {running ? "Click to interrupt" : "Click to simulate a turn"}
            </span>
          </div>
        </div>

        {/* ATTS state machine */}
        <div style={card}>
          <Label>ATTS State Machine</Label>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {ATTS_STATES.map(s => (
              <div key={s} style={{
                display: "flex", alignItems: "center", gap: 10,
                padding: "8px 12px", borderRadius: 8,
                background: attsState === s ? `${STATE_COLORS[s]}22` : "#0a0a10",
                border: `1px solid ${attsState === s ? STATE_COLORS[s] : "#1a1a22"}`,
                transition: "all 0.25s",
              }}>
                <div style={{ width: 8, height: 8, borderRadius: "50%", background: attsState === s ? STATE_COLORS[s] : "#333", transition: "background 0.25s", flexShrink: 0 }} />
                <span style={{ fontSize: "0.85rem", textTransform: "capitalize", color: attsState === s ? STATE_COLORS[s] : "#666", fontWeight: attsState === s ? 700 : 400 }}>
                  {s}
                </span>
                {attsState === s && (
                  <span style={{ marginLeft: "auto", fontSize: "0.7rem", color: STATE_COLORS[s] }}>● active</span>
                )}
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Pipeline flow */}
      <div style={{ ...card, marginBottom: "1rem" }}>
        <Label>Pipeline Flow</Label>
        <div style={{ display: "flex", alignItems: "center", gap: 4, overflowX: "auto", paddingBottom: 4 }}>
          {/* Mic */}
          <PipelineNode label="MIC" icon="🎤" active={attsState === "listening"} color="#3b82f6" latency={null} />
          <Arrow active={attsState === "listening"} />
          {PIPELINE_STAGES.map((stage, i) => (
            <div key={stage} style={{ display: "flex", alignItems: "center", gap: 4 }}>
              <PipelineNode
                label={stage}
                icon={["🔊","🔄","🧠","⏱","📡","🗣"][i]}
                active={!!activeStages[stage]}
                color={STAGE_COLORS[stage]}
                latency={stageLatencies[stage]}
              />
              {i < PIPELINE_STAGES.length - 1 && <Arrow active={!!activeStages[PIPELINE_STAGES[i+1]]} />}
            </div>
          ))}
          <Arrow active={attsState === "speaking"} />
          <PipelineNode label="SPEAKER" icon="🔈" active={attsState === "speaking"} color="#10b981" latency={null} />
        </div>
      </div>

      {/* TAB + AQAL buffers + waveform */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "1rem", marginBottom: "1rem" }}>

        {/* TAB */}
        <div style={card}>
          <Label>TAB Buffer</Label>
          <div style={{ fontSize: "0.72rem", color: "#8888aa", marginBottom: 8 }}>Temporal Alignment Buffer</div>
          <BarMeter
            value={tabLevel / 0.20}
            color={tabBackpressure ? "#ef4444" : "#8b5cf6"}
            label={`${(tabLevel * 1000).toFixed(0)}ms buffered`}
            highWaterPct={0.75}
          />
          <div style={{ marginTop: 8, fontSize: "0.72rem", display: "flex", justifyContent: "space-between" }}>
            <span style={{ color: "#8888aa" }}>Back-pressure</span>
            <span style={{ color: tabBackpressure ? "#ef4444" : "#10b981", fontWeight: 700 }}>
              {tabBackpressure ? "ON" : "off"}
            </span>
          </div>
        </div>

        {/* AQAL */}
        <div style={card}>
          <Label>AQAL Bucket</Label>
          <div style={{ fontSize: "0.72rem", color: "#8888aa", marginBottom: 8 }}>Adaptive Quality Layer</div>
          <BarMeter
            value={aqalFill}
            color={aqalFill > 0.8 ? "#ef4444" : "#06b6d4"}
            label={`${(aqalFill * 100).toFixed(0)}% full`}
            highWaterPct={0.8}
          />
          <div style={{ marginTop: 8, fontSize: "0.7rem" }}>
            <div style={{ color: "#8888aa", marginBottom: 3 }}>Codec</div>
            <div style={{ padding: "3px 8px", borderRadius: 6, background: "#0a0a10", color: "#06b6d4", fontSize: "0.68rem", fontFamily: "monospace" }}>
              {codec}
            </div>
          </div>
        </div>

        {/* Audio waveform */}
        <div style={card}>
          <Label>Audio Waveform</Label>
          <div style={{ fontSize: "0.72rem", color: "#8888aa", marginBottom: 8 }}>
            {attsState === "listening" ? "Capturing microphone…" :
             attsState === "speaking" ? "Playing synthesised audio…" : "Silent"}
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 2, height: 48 }}>
            {audioWave.map((h, i) => (
              <div key={i} style={{
                flex: 1, borderRadius: 2,
                height: `${h * 100}%`,
                background: attsState === "listening" ? "#3b82f6" :
                            attsState === "speaking" ? "#10b981" : "#2e2e3e",
                transition: "height 0.08s ease-out",
              }} />
            ))}
          </div>
          <div style={{ marginTop: 8, fontSize: "0.72rem", color: "#8888aa" }}>
            Codec: <span style={{ color: "#e2e2f0" }}>{codec.includes("evs") ? "EVS 8kHz" : codec.includes("16") ? "Opus 16kHz" : "Opus 24kHz"}</span>
          </div>
        </div>
      </div>

      {/* Token stream */}
      {tokenStream && (
        <div style={{ ...card, marginBottom: "1rem" }}>
          <Label>Live Token Stream (LLM → TAB → TTS)</Label>
          <div style={{ fontFamily: "monospace", fontSize: "0.85rem", color: "#a78bfa", lineHeight: 1.7 }}>
            {tokenStream.split(" ").map((t, i) => (
              <span key={i} style={{ display: "inline-block", margin: "1px 3px", padding: "1px 5px", borderRadius: 4, background: "#1a1240", border: "1px solid #3a2a80", animation: "fadein 0.15s ease" }}>
                {t}
              </span>
            ))}
            <style>{`@keyframes fadein{from{opacity:0;transform:translateY(3px)}to{opacity:1;transform:translateY(0)}}`}</style>
          </div>
        </div>
      )}

      {/* Latency metrics */}
      <div style={{ ...card, marginBottom: "1rem" }}>
        <Label>Latency Budget</Label>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: 8 }}>
          {[
            ["ASR", metrics.asr, 80],
            ["LLM first token", metrics.llm, 120],
            ["TAB", stageLatencies.TAB, 20],
            ["TTS", metrics.tts, 70],
            ["End-to-end", metrics.e2e, 315],
          ].map(([label, val, budget]) => (
            <div key={label} style={{ background: "#0a0a10", borderRadius: 8, padding: "8px 10px", textAlign: "center" }}>
              <div style={{ fontSize: "0.65rem", color: "#8888aa", marginBottom: 2 }}>{label}</div>
              <div style={{
                fontSize: label === "End-to-end" ? "1.3rem" : "1.1rem",
                fontWeight: 700,
                color: val == null ? "#444" : val <= budget ? "#10b981" : val <= budget * 1.2 ? "#f59e0b" : "#ef4444",
              }}>
                {fmtMs(val)}
              </div>
              <div style={{ fontSize: "0.6rem", color: "#444", marginTop: 2 }}>budget {budget}ms</div>
            </div>
          ))}
        </div>
      </div>

      {/* Conversation */}
      <div style={card}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
          <Label>Conversation</Label>
          <button onClick={() => { setConversation([]); setE2eMs(null); setMetrics({ asr:null,llm:null,tts:null,e2e:null }); }}
            style={{ background: "none", border: "1px solid #2e2e3e", color: "#888", borderRadius: 6, padding: "2px 10px", cursor: "pointer", fontSize: "0.75rem" }}>
            Clear
          </button>
        </div>
        <div ref={convRef} style={{ display: "flex", flexDirection: "column", gap: 8, maxHeight: 280, overflowY: "auto" }}>
          {conversation.length === 0 && (
            <div style={{ color: "#444", fontSize: "0.85rem", textAlign: "center", padding: "1rem" }}>
              Click 🎤 to start a simulated conversation turn
            </div>
          )}
          {conversation.map((turn, i) => (
            <div key={i} style={{
              padding: "8px 12px", borderRadius: 10, maxWidth: "85%", fontSize: "0.875rem", lineHeight: 1.5,
              alignSelf: turn.role === "user" ? "flex-end" : "flex-start",
              background: turn.role === "user" ? "#3730a3" : "#1e1e2e",
              color: turn.role === "user" ? "#c7d2fe" : "#e2e2f0",
              border: turn.partial ? "1px dashed #555" : "1px solid transparent",
              opacity: turn.partial ? 0.7 : 1,
            }}>
              <div style={{ fontSize: "0.65rem", color: turn.role === "user" ? "#818cf8" : "#8888aa", marginBottom: 3 }}>
                {turn.role === "user" ? "👤 User" : "🤖 Omni Voice"}
              </div>
              {turn.text}
              {turn.partial && <span style={{ color: "#888" }}> ▌</span>}
            </div>
          ))}
          {tokenStream && attsState === "speaking" && (
            <div style={{ padding: "8px 12px", borderRadius: 10, maxWidth: "85%", fontSize: "0.875rem", lineHeight: 1.5, background: "#1e1e2e", border: "1px dashed #3b82f6" }}>
              <div style={{ fontSize: "0.65rem", color: "#3b82f6", marginBottom: 3 }}>🤖 Omni Voice (streaming…)</div>
              {tokenStream}<span style={{ color: "#3b82f6" }}>▌</span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Sub-components ────────────────────────────────────────────────

function PipelineNode({ label, icon, active, color, latency }) {
  return (
    <div style={{
      display: "flex", flexDirection: "column", alignItems: "center", gap: 4,
      minWidth: 64,
    }}>
      <div style={{
        width: 52, height: 52, borderRadius: 12,
        display: "flex", alignItems: "center", justifyContent: "center",
        fontSize: "1.3rem",
        background: active ? `${color}22` : "#0a0a10",
        border: `2px solid ${active ? color : "#1e1e2e"}`,
        boxShadow: active ? `0 0 14px ${color}55` : "none",
        transition: "all 0.2s",
      }}>
        {icon}
      </div>
      <span style={{ fontSize: "0.6rem", color: active ? color : "#555", fontWeight: 700, letterSpacing: "0.05em" }}>
        {label}
      </span>
      {latency != null && (
        <span style={{ fontSize: "0.6rem", color: "#888", background: "#0a0a10", borderRadius: 4, padding: "1px 4px" }}>
          {fmtMs(latency)}
        </span>
      )}
    </div>
  );
}

function Arrow({ active }) {
  return (
    <div style={{
      display: "flex", alignItems: "center",
      color: active ? "#7c6ff7" : "#2e2e3e",
      transition: "color 0.2s",
      fontSize: "1rem",
      flexShrink: 0,
    }}>
      →
    </div>
  );
}

function BarMeter({ value, color, label, highWaterPct = 0.75 }) {
  const pct = Math.min(1, Math.max(0, value));
  return (
    <div>
      <div style={{ position: "relative", height: 20, background: "#0a0a10", borderRadius: 6, overflow: "hidden" }}>
        <div style={{
          position: "absolute", left: 0, top: 0, bottom: 0,
          width: `${pct * 100}%`,
          background: color,
          borderRadius: 6,
          transition: "width 0.15s ease-out",
        }} />
        {/* High water mark */}
        <div style={{
          position: "absolute", top: 0, bottom: 0,
          left: `${highWaterPct * 100}%`,
          width: 2, background: "#ef444466",
        }} />
      </div>
      <div style={{ fontSize: "0.7rem", color: "#8888aa", marginTop: 4 }}>{label}</div>
    </div>
  );
}

function Label({ children }) {
  return (
    <div style={{ fontSize: "0.72rem", textTransform: "uppercase", letterSpacing: "0.1em", color: "#8888aa", marginBottom: 10, fontWeight: 600 }}>
      {children}
    </div>
  );
}

const card = {
  background: "#13131e",
  border: "1px solid #1e1e2e",
  borderRadius: 12,
  padding: "1rem",
};
