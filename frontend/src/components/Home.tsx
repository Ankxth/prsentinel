import type { ReactNode } from "react";
import Logo from "./Logo";

const FEATURES = [
  {
    tag: "Tests",
    title: "Tests written for what you changed",
    text: "When you open a pull request or push a commit, PRSentinel finds the functions you touched and has an AI write tests for exactly those. It runs them on your old code and your new code.",
  },
  {
    tag: "Verdicts",
    title: "Did your change break something?",
    text: "Each test is compared before and after. A test that passed before and fails now is flagged as a possible break. Unstable and unreliable tests are labelled so they do not mislead you.",
  },
  {
    tag: "Security",
    title: "Security checks in plain words",
    text: "Trusted scanners look for risky code, leaked secrets and unsafe libraries. Only problems that your change introduced are put first, with an explanation and a suggested next step.",
  },
  {
    tag: "Evidence",
    title: "Real test runs, not guesses",
    text: "For command-injection problems an agent writes a safe test and runs it three times. If it reproduces the problem you see a confirmed result and the exact test it used.",
  },
  {
    tag: "Quality",
    title: "Mutation check",
    text: "We plant small deliberate faults in your code and see whether the generated tests notice. A high score means the tests are actually watching your code.",
  },
  {
    tag: "Reports",
    title: "One comment, one dashboard",
    text: "A single pull request comment is kept up to date, with a link to a dashboard page for each run that shows every test, its result and the code behind it.",
  },
];

const STEPS = [
  { title: "Sign in and add a repository", text: "Use your GitHub account. Public Python repositories only." },
  { title: "Add one small file", text: "Copy the workflow file shown on the Setup tab into your repository." },
  { title: "Push or open a pull request", text: "PRSentinel runs automatically in your own GitHub Actions." },
  { title: "Read the verdict", text: "See in plain language whether to carry on or fix something first." },
];

export default function Home({ action }: { action: ReactNode }) {
  return (
    <div>
      <section className="hero">
        <Logo size={72} />
        <h1>Know if your change breaks your code</h1>
        <p className="lead">
          PRSentinel checks every commit and pull request on your public Python repositories. It writes tests
          for what changed, runs security checks, and tells you in plain language whether to move forward or
          roll back.
        </p>
        <div className="actions">{action}</div>
      </section>

      <h2 className="section-title">What it does</h2>
      <div className="features">
        {FEATURES.map((f) => (
          <div className="feature" key={f.title}>
            <span className="feature-tag">{f.tag}</span>
            <h3>{f.title}</h3>
            <p>{f.text}</p>
          </div>
        ))}
      </div>

      <h2 className="section-title">How to start</h2>
      <ol className="steps">
        {STEPS.map((s) => (
          <li key={s.title}>
            <strong>{s.title}</strong>
            <span className="muted">{s.text}</span>
          </li>
        ))}
      </ol>

      <div className="promise">
        <strong>Our promises.</strong> Your code is never run on our servers: tests and scanners run in your own
        GitHub Actions. The AI suggests, but running code decides the verdict. When something could not be
        checked we say so, and we never show that as a pass.
      </div>
    </div>
  );
}
