import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  completeTrial,
  controllerDecision,
  createSession,
  getConfig,
  getNextTrial,
  postTrialEvents,
  saveClosingRatings,
  startCalibration,
  submitDat
} from "./api";
import "./styles.css";

const DAT_WORDS = 10;
const GENAI_USAGE_OPTIONS = [
  ["1", "1 从未使用"],
  ["2", "2 偶尔尝试"],
  ["3", "3 有一定使用经验"],
  ["4", "4 经常使用"],
  ["5", "5 非常熟练"]
];
const TRIAL_RATINGS = [
  ["autonomy", "我感到自己是创作过程的主动推动者"],
  ["ownership", "这个续写感觉像是我的作品"],
  ["mental_effort", "完成这个试次需要多少心理努力"],
  ["reliance", "我依赖AI建议来推进故事"],
  ["timing_adequacy", "AI建议出现的时机合适"]
];
const CLOSING_RATINGS = [
  ["perceived_timeliness", "整体而言，系统建议出现得及时"],
  ["perceived_adaptivity", "我觉得系统会根据我的状态调整"],
  ["perceived_physiology_use", "我认为系统使用了生理信息"],
  ["fatigue", "我现在感到疲劳"]
];

function App() {
  const [config, setConfig] = useState(null);
  const [view, setView] = useState("setup");
  const [session, setSession] = useState(null);
  const [sessionForm, setSessionForm] = useState({
    participant_id: "",
    age: "",
    native_language: "中文",
    vision_status: "正常或矫正正常",
    neurological_history: "无",
    psychiatric_history: "无",
    genai_usage: "",
    mode: "official",
    timer_preset: "official",
    controller_mode: "real"
  });
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  const [datWords, setDatWords] = useState(Array.from({ length: DAT_WORDS }, () => ""));
  const [calibration, setCalibration] = useState({ eyesOpen: null, eyesClosed: null });
  const [trial, setTrial] = useState(null);
  const [decision, setDecision] = useState(null);
  const [timeline, setTimeline] = useState([]);
  const [stageIndex, setStageIndex] = useState(0);
  const [stageStartedAt, setStageStartedAt] = useState(Date.now());
  const [remainingMs, setRemainingMs] = useState(0);
  const [finalText, setFinalText] = useState("");
  const [ratings, setRatings] = useState({});
  const [suggestionShown, setSuggestionShown] = useState(false);
  const [closingRatings, setClosingRatings] = useState({});
  const eventsRef = useRef({ phase_events: [], keystroke_events: [], suggestion_events: [], system_events: [] });

  useEffect(() => {
    getConfig().then(setConfig).catch((error) => setStatus(error.message));
  }, []);

  const currentStage = timeline[stageIndex] || null;
  const devMode = session?.mode === "dev";

  useEffect(() => {
    if (!currentStage || currentStage.duration_seconds == null || view !== "trial") return;
    const durationMs = currentStage.duration_seconds * 1000;
    const tick = () => {
      const remaining = Math.max(0, durationMs - (Date.now() - stageStartedAt));
      setRemainingMs(remaining);
      if (remaining <= 0) advanceStage("end");
    };
    tick();
    const timer = window.setInterval(tick, 200);
    return () => window.clearInterval(timer);
  }, [currentStage, stageStartedAt, view]);

  async function startSession() {
    setBusy(true);
    try {
      const isDevSession = sessionForm.mode === "dev";
      const participantId = sessionForm.participant_id.trim();
      const parsedAge = Number(sessionForm.age);
      const payload = {
        ...sessionForm,
        participant_id: isDevSession && !participantId ? "1" : participantId,
        age: isDevSession && (!Number.isFinite(parsedAge) || parsedAge < 1) ? 1 : parsedAge
      };
      const created = await createSession(payload);
      setSession(created.session);
      setView("dat");
      setStatus("");
    } catch (error) {
      setStatus(error.message);
    } finally {
      setBusy(false);
    }
  }

  async function submitDatStage() {
    const words = datWords.map((word) => word.trim());
    if (!devMode && words.some((word) => !word)) return;
    const submittedWords = devMode
      ? Array.from({ length: DAT_WORDS }, (_, index) => words[index] || `测试词${index + 1}`)
      : words;
    setBusy(true);
    try {
      const state = await submitDat(session.id, { words: submittedWords });
      setSession(state.session);
      setView("calibration");
    } catch (error) {
      setStatus(error.message);
    } finally {
      setBusy(false);
    }
  }

  async function runCalibration(type) {
    setBusy(true);
    try {
      const run = await startCalibration(type, session.id);
      const source = new EventSource(`/api/calibration/${run.id}/events`);
      source.onmessage = (event) => {
        const payload = JSON.parse(event.data);
        setCalibration((current) => ({ ...current, [type === "eyes-open" ? "eyesOpen" : "eyesClosed"]: payload }));
        if (payload.status === "finished" || payload.status === "error") {
          source.close();
          setBusy(false);
        }
      };
      source.onerror = () => {
        source.close();
        setBusy(false);
        setStatus("校准 SSE 连接中断。");
      };
    } catch (error) {
      setStatus(error.message);
      setBusy(false);
    }
  }

  async function beginTrials() {
    setBusy(true);
    try {
      await loadNextTrial();
    } catch (error) {
      setStatus(error.message);
    } finally {
      setBusy(false);
    }
  }

  async function loadNextTrial() {
    const response = await getNextTrial(session.id);
    if (response.session_complete) {
      setView("closing");
      return;
    }
    const next = response.trial;
    setTrial(next);
    setDecision(null);
    setFinalText("");
    setRatings({});
    setSuggestionShown(false);
    eventsRef.current = { phase_events: [], keystroke_events: [], suggestion_events: [], system_events: [] };

    let nextDecision = null;
    if (["fixed_early", "fixed_delayed", "no_ai", "neuroadaptive", "yoked_sham"].includes(next.condition)) {
      nextDecision = await controllerDecision(next.trial_id, { sequence_position: next.trial_order - 1 });
      setDecision(nextDecision);
    }
    const built = buildTimeline(next.condition, config, nextDecision, session.timer_preset);
    setTimeline(built);
    setStageIndex(0);
    setStageStartedAt(Date.now());
    setView("trial");
    logPhase(built[0].stage, "start", built[0].duration_seconds);
  }

  async function advanceStage(event) {
    if (!trial || !currentStage) return;
    logPhase(currentStage.stage, event, remainingMs);
    await flushEvents();
    if (currentStage.stage === "suggestion") {
      setSuggestionShown(true);
      eventsRef.current.suggestion_events.push({
        timestamp: new Date().toISOString(),
        action: "shown",
        suggestion_text: trial.material.suggestion_text
      });
    }
    const nextIndex = stageIndex + 1;
    if (nextIndex >= timeline.length) {
      return;
    }
    setStageIndex(nextIndex);
    setStageStartedAt(Date.now());
    logPhase(timeline[nextIndex].stage, "start", timeline[nextIndex].duration_seconds);
  }

  async function submitTrial() {
    setBusy(true);
    try {
      await flushEvents();
      const completion = await completeTrial(trial.trial_id, {
        planning_notes: "",
        final_text: finalText,
        suggestion_action: suggestionShown ? "ignored" : null,
        text_validity_override: devMode,
        ratings
      });
      if (completion.session_complete) {
        setView("closing");
      } else {
        await loadNextTrial();
      }
    } catch (error) {
      setStatus(error.message);
    } finally {
      setBusy(false);
    }
  }

  async function finishSession() {
    setBusy(true);
    try {
      const state = await saveClosingRatings(session.id, closingRatings);
      setSession(state.session);
      setView("complete");
    } catch (error) {
      setStatus(error.message);
    } finally {
      setBusy(false);
    }
  }

  function logPhase(stage, event, remaining) {
    eventsRef.current.phase_events.push({
      stage,
      event,
      timestamp: new Date().toISOString(),
      remaining_ms: typeof remaining === "number" ? Math.round(remaining) : null
    });
  }

  async function flushEvents() {
    if (!trial) return;
    const payload = eventsRef.current;
    if (!Object.values(payload).some((items) => items.length)) return;
    eventsRef.current = { phase_events: [], keystroke_events: [], suggestion_events: [], system_events: [] };
    await postTrialEvents(trial.trial_id, payload);
  }

  if (!config) return <main className="shell"><section className="panel">正在加载系统配置...</section></main>;

  return (
    <main className="shell">
      {view === "setup" && (
        <SetupView
          config={config}
          form={sessionForm}
          setForm={setSessionForm}
          onStart={startSession}
          busy={busy}
        />
      )}
      {view === "dat" && <DatView words={datWords} setWords={setDatWords} onSubmit={submitDatStage} busy={busy} allowEmpty={devMode} />}
      {view === "calibration" && (
        <CalibrationView
          config={config}
          calibration={calibration}
          onRun={runCalibration}
          onContinue={beginTrials}
          busy={busy}
        />
      )}
      {view === "trial" && trial && currentStage && (
        <TrialView
          trial={trial}
          stage={currentStage}
          decision={decision}
          remainingMs={remainingMs}
          finalText={finalText}
          setFinalText={(value) => {
            setFinalText(value);
            eventsRef.current.keystroke_events.push({
              timestamp: new Date().toISOString(),
              key: "[input]",
              cursor_position: value.length,
              action: "type"
            });
          }}
          ratings={ratings}
          setRatings={setRatings}
          devMode={devMode}
          onNext={() => advanceStage("submit")}
          onSubmit={submitTrial}
          busy={busy}
        />
      )}
      {view === "closing" && (
        <ClosingView ratings={closingRatings} setRatings={setClosingRatings} onFinish={finishSession} busy={busy} allowEmpty={devMode} />
      )}
      {view === "complete" && session && <CompleteView session={session} />}
      {status && <p className="status">{status}</p>}
    </main>
  );
}

function SetupView({ config, form, setForm, onStart, busy }) {
  const ready = config.materials.ready;
  const required = config.materials.required;
  return (
    <section className="panel">
      <p className="eyebrow">Neuroadaptive Writing Experiment</p>
      <h1>实验会话入口</h1>
      <div className={ready ? "notice ready" : "notice warning"}>
        内置材料：practice {config.materials.counts.practice}/{required.practice}，formal {config.materials.counts.formal}/{required.formal}
      </div>
      <div className="form-grid">
        <Field label="被试编号" value={form.participant_id} onChange={(participant_id) => setForm({ ...form, participant_id })} />
        <Field label="年龄" value={form.age} onChange={(age) => setForm({ ...form, age })} type="number" />
        <Field label="母语" value={form.native_language} onChange={(native_language) => setForm({ ...form, native_language })} />
        <Field label="视力情况" value={form.vision_status} onChange={(vision_status) => setForm({ ...form, vision_status })} />
        <Field label="神经系统病史" value={form.neurological_history} onChange={(neurological_history) => setForm({ ...form, neurological_history })} />
        <Field label="精神心理病史" value={form.psychiatric_history} onChange={(psychiatric_history) => setForm({ ...form, psychiatric_history })} />
        <ChoiceScale label="GenAI 使用经验" name="genai_usage" value={form.genai_usage} onChange={(genai_usage) => setForm({ ...form, genai_usage })} options={GENAI_USAGE_OPTIONS} />
      </div>
      <div className="form-grid compact">
        <Select label="运行模式" value={form.mode} onChange={(mode) => setForm({ ...form, mode, timer_preset: mode === "dev" ? "dev" : "official", controller_mode: mode === "dev" ? "simulation" : "real" })} options={[["official", "正式实验"], ["dev", "模拟测试实验"]]} />
        <Select label="计时" value={form.timer_preset} onChange={(timer_preset) => setForm({ ...form, timer_preset })} options={[["official", "正式"], ["dev", "短计时"]]} />
        <Select label="控制器" value={form.controller_mode} onChange={(controller_mode) => setForm({ ...form, controller_mode })} options={[["real", "真实 EEG"], ["simulation", "模拟"]]} />
      </div>
      <button onClick={onStart} disabled={busy || !ready}>创建会话</button>
    </section>
  );
}

function DatView({ words, setWords, onSubmit, busy, allowEmpty }) {
  return (
    <section className="panel">
      <p className="eyebrow">Stage 1</p>
      <h1>DAT 预试</h1>
      <p className="muted">请输入 10 个尽可能语义距离远的中文词。</p>
      <div className="word-grid">
        {words.map((word, index) => (
          <input key={index} value={word} onChange={(event) => setWords(words.map((item, i) => i === index ? event.target.value : item))} placeholder={`词 ${index + 1}`} />
        ))}
      </div>
      <button onClick={onSubmit} disabled={busy || (!allowEmpty && words.some((word) => !word.trim()))}>提交 DAT</button>
    </section>
  );
}

function CalibrationView({ config, calibration, onRun, onContinue, busy }) {
  return (
    <section className="panel">
      <p className="eyebrow">Stage 2</p>
      <h1>EEG 基线校准</h1>
      <div className="cards">
        <CalibrationCard title="睁眼屏幕基线" seconds={config.eyes_open_seconds} run={calibration.eyesOpen} onRun={() => onRun("eyes-open")} busy={busy} />
        <CalibrationCard title="闭眼 IAF 基线" seconds={config.eyes_closed_seconds} run={calibration.eyesClosed} onRun={() => onRun("eyes-closed")} busy={busy} />
      </div>
      <button onClick={onContinue}>进入练习与正式试次</button>
    </section>
  );
}

function TrialView(props) {
  const { trial, stage, remainingMs, finalText, setFinalText, ratings, setRatings, devMode, onNext, onSubmit, busy } = props;
  const isTimed = stage.duration_seconds != null;
  if (stage.stage === "ideation" || stage.stage === "ideation_resume") {
    return (
      <section className="ideation-screen">
        <div className="ideation-timer">{isTimed ? formatRemaining(remainingMs) : "--:--"}</div>
        <div className="ideation-cross">+</div>
        {devMode && (
          <div className="ideation-dev">
            <p>当前条件：{conditionLabel(trial.condition)}</p>
            <button onClick={onNext}>下一步</button>
          </div>
        )}
      </section>
    );
  }
  return (
    <section className="panel trial-panel">
      <header className="trial-header">
        <div>
          <span>{trial.phase === "practice" ? "练习" : "正式"} · 试次 {trial.trial_order}/{trial.total_trials}</span>
          <strong>{conditionLabel(trial.condition)}</strong>
        </div>
        <div>{stageLabel(stage.stage)}</div>
        <div className="timer">{isTimed ? formatRemaining(remainingMs) : "--:--"}</div>
      </header>
      {stage.stage === "reading" && (
        <div className="stage-body">
          <p className="theme">主题：{trial.material.theme}</p>
          <p className="premise">{trial.material.premise_text}</p>
          <button onClick={onNext}>已读完</button>
        </div>
      )}
      {stage.stage === "suggestion" && (
        <div className="stage-body">
          <p className="eyebrow">AI 建议</p>
          <div className="suggestion">{trial.material.suggestion_text}</div>
          <button onClick={onNext}>继续</button>
        </div>
      )}
      {stage.stage === "writing" && (
        <div className="stage-body writing">
          <label>四句续写
            <textarea value={finalText} onChange={(event) => setFinalText(event.target.value)} className="writing-box" />
          </label>
          <button onClick={onNext}>进入试次评分</button>
        </div>
      )}
      {stage.stage === "rating" && (
        <RatingForm items={TRIAL_RATINGS} values={ratings} setValues={setRatings} onSubmit={onSubmit} busy={busy} allowEmpty={devMode} />
      )}
    </section>
  );
}

function ClosingView({ ratings, setRatings, onFinish, busy, allowEmpty }) {
  return (
    <section className="panel">
      <p className="eyebrow">Stage 5</p>
      <h1>结束评分与说明</h1>
      <p className="muted">本实验比较不同 AI 介入时机。部分条件可能基于 EEG 或匹配日程决定是否展示建议；系统不会推断身份、临床状态或一般创造力。</p>
      <RatingForm items={CLOSING_RATINGS} values={ratings} setValues={setRatings} onSubmit={onFinish} busy={busy} allowEmpty={allowEmpty} />
    </section>
  );
}

function CompleteView({ session }) {
  return (
    <section className="panel">
      <p className="eyebrow">完成</p>
      <h1>数据已保存</h1>
      <div className="actions">
        <a className="button-link" href={`/api/export/${session.id}.json`}>导出 JSON</a>
        <a className="button-link" href={`/api/export/${session.id}.csv`}>导出 CSV</a>
        <button onClick={() => window.location.reload()}>新会话</button>
      </div>
    </section>
  );
}

function Field({ label, value, onChange, type = "text" }) {
  return <label>{label}<input type={type} value={value} onChange={(event) => onChange(event.target.value)} /></label>;
}

function Select({ label, value, onChange, options }) {
  return <label>{label}<select value={value} onChange={(event) => onChange(event.target.value)}>{options.map(([value, text]) => <option key={value} value={value}>{text}</option>)}</select></label>;
}

function ChoiceScale({ label, name, value, onChange, options }) {
  return (
    <fieldset className="choice-field">
      <legend>{label}</legend>
      <div className="choice-scale">
        {options.map(([optionValue, text]) => (
          <label key={optionValue} className="choice-option">
            <input type="radio" name={name} checked={value === optionValue} onChange={() => onChange(optionValue)} />
            {text}
          </label>
        ))}
      </div>
    </fieldset>
  );
}

function CalibrationCard({ title, seconds, run, onRun, busy }) {
  return (
    <div className="card">
      <h3>{title}</h3>
      <p>{seconds}s</p>
      <p className="muted">{run?.message || run?.status || "未开始"}</p>
      <button onClick={onRun} disabled={busy}>开始</button>
    </div>
  );
}

function RatingForm({ items, values, setValues, onSubmit, busy, allowEmpty = false }) {
  const complete = allowEmpty || items.every(([key]) => values[key]);
  return (
    <div className="stage-body">
      <div className="ratings">
        {items.map(([key, label]) => (
          <div className="rating-row" key={key}>
            <span>{label}</span>
            <div>
              {[1, 2, 3, 4, 5, 6, 7].map((value) => (
                <label key={value} className="radio-label">
                  <input type="radio" name={key} checked={values[key] === value} onChange={() => setValues({ ...values, [key]: value })} />
                  {value}
                </label>
              ))}
            </div>
          </div>
        ))}
      </div>
      <button onClick={onSubmit} disabled={busy || !complete}>提交</button>
    </div>
  );
}

function buildTimeline(condition, config, decision, timerPreset = "official") {
  const durations = config.durations[timerPreset] || config.durations.official;
  const read = { stage: "reading", duration_seconds: durations.reading };
  const ideation = { stage: "ideation", duration_seconds: durations.ideation };
  const suggestion = { stage: "suggestion", duration_seconds: durations.suggestion };
  const writing = { stage: "writing", duration_seconds: null };
  const rating = { stage: "rating", duration_seconds: null };
  if (condition === "no_ai") return [read, ideation, writing, rating];
  if (condition === "fixed_early") return [read, suggestion, ideation, writing, rating];
  if (condition === "fixed_delayed") return [read, ideation, suggestion, writing, rating];
  const display = decision?.display_suggestion;
  if (!display) return [read, ideation, writing, rating];
  const trigger = Math.min(Math.max(decision?.display_time_seconds || durations.ideation, 0), durations.ideation);
  const remaining = Math.max(0, durations.ideation - trigger);
  return [read, { stage: "ideation", duration_seconds: trigger }, suggestion, ...(remaining ? [{ stage: "ideation_resume", duration_seconds: remaining }] : []), writing, rating];
}

function conditionLabel(condition) {
  return {
    no_ai: "无AI",
    fixed_early: "构思前AI",
    fixed_delayed: "构思后AI",
    neuroadaptive: "神经自适应AI",
    yoked_sham: "安慰剂"
  }[condition] || condition;
}

function stageLabel(stage) {
  return {
    reading: "阅读",
    ideation: "静默构思",
    ideation_resume: "继续构思",
    suggestion: "AI建议",
    writing: "续写",
    rating: "试次评分"
  }[stage] || stage;
}

function formatRemaining(ms) {
  const seconds = Math.max(0, Math.ceil(ms / 1000));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

createRoot(document.getElementById("root")).render(<App />);
