import "./Home.css";

const FEATURES = [
  {
    title: "Replay-aware decision coaching",
    body: "Feedback grounded in what actually happened in your replay — not generic tips.",
  },
  {
    title: "Scalable mistake detection",
    body: "A detection layer designed to generalize across roles, maps, and titles.",
  },
  {
    title: "Personalized improvement loops",
    body: "Recurring patterns become targeted drills so the same mistake stops repeating.",
  },
  {
    title: "Built for multi-game expansion",
    body: "An engine architected to add new competitive titles over time.",
  },
];

const STEPS = [
  {
    n: "01",
    title: "Bring your replay",
    body: "Drop in a CS2 demo round (or run the built-in sample). No setup, no login.",
  },
  {
    n: "02",
    title: "AI finds the moments",
    body: "rankup.ai scans the round for decision points where the play went wrong.",
  },
  {
    n: "03",
    title: "Get the better play",
    body: "Each mistake comes with what you should have done and why it works.",
  },
  {
    n: "04",
    title: "Turn it into practice",
    body: "Patterns become drills, building an improvement loop over your sessions.",
  },
];

const CATCHES = [
  "Bad positioning and over-extends",
  "Mistimed peeks and duels",
  "Poor trade and rotation reads",
  "Utility and economy misuse",
  "Repeating decision patterns",
  "Round-losing risk-taking",
];

function goToApp() {
  window.location.hash = "#/app";
}

export default function Home() {
  return (
    <div className="home">
      <nav className="home-nav">
        <span className="home-logo">
          rank<span className="accent">up</span>.ai
        </span>
        <button className="nav-cta" onClick={goToApp}>
          Try Now
        </button>
      </nav>

      <header className="hero">
        <div className="hero-glow" aria-hidden="true" />
        <div className="hero-inner">
          <span className="badge">AI coaching · competitive gaming</span>
          <h1 className="hero-title">
            AI coaching built for <span className="grad">every competitive game.</span>
          </h1>
          <p className="hero-sub">
            rankup.ai turns gameplay data into decision-focused feedback. Today, it analyzes
            CS2 demo rounds to find mistakes, explain better choices, and turn recurring
            patterns into practice.
          </p>
          <div className="hero-actions">
            <button className="cta primary" onClick={goToApp}>
              Try Now
            </button>
            <a className="cta ghost" href="#how">
              See how it works
            </a>
          </div>
          <p className="hero-note">Current demo: CS2 round analysis — no account required.</p>
        </div>
      </header>

      <main className="sections">
        <section id="how" className="section">
          <h2 className="section-title">How it works</h2>
          <p className="section-lead">From raw replay to a better next round in four steps.</p>
          <div className="steps">
            {STEPS.map((s) => (
              <div key={s.n} className="step-card">
                <span className="step-n">{s.n}</span>
                <h3>{s.title}</h3>
                <p>{s.body}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="section">
          <h2 className="section-title">Built to scale across games</h2>
          <p className="section-lead">
            The coaching engine isn't tied to one title. Decision analysis, mistake detection,
            and improvement loops are built to expand to new esports over time.
          </p>
          <div className="feature-grid">
            {FEATURES.map((f) => (
              <div key={f.title} className="feature-card">
                <h3>{f.title}</h3>
                <p>{f.body}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="section">
          <h2 className="section-title">What it catches</h2>
          <p className="section-lead">
            Today, on CS2 rounds, rankup.ai surfaces the decisions that quietly cost you the game.
          </p>
          <ul className="catch-list">
            {CATCHES.map((c) => (
              <li key={c}>{c}</li>
            ))}
          </ul>
        </section>

        <section className="section why">
          <h2 className="section-title">Why it helps players improve</h2>
          <p className="section-lead">
            Most players know they lost the round — not exactly which decision lost it. rankup.ai
            pinpoints the moment, shows the better play, and keeps a memory of your patterns so
            improvement compounds session over session.
          </p>
          <div className="cta-band">
            <div>
              <h3>Ready to see your mistakes?</h3>
              <p>Analyze a CS2 demo round in seconds.</p>
            </div>
            <button className="cta primary" onClick={goToApp}>
              Try Now
            </button>
          </div>
        </section>
      </main>

      <footer className="home-footer">
        <span className="home-logo small">
          rank<span className="accent">up</span>.ai
        </span>
        <span className="muted">AI decision coaching for competitive games · Current demo: CS2</span>
      </footer>
    </div>
  );
}
