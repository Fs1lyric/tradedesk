import { useState, type FormEvent } from "react";
import { ApiError, api } from "../api";

export function Login() {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [needCode, setNeedCode] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (mode === "register") await api.register(email, password);
      await api.login(email, password, needCode ? code : undefined);
    } catch (err) {
      if (err instanceof ApiError && err.detail === "totp required") setNeedCode(true);
      else setError(err instanceof ApiError && Array.isArray(err.detail)
        ? "Check the email, and use a password of at least 10 characters."
        : (err as Error).message);
    } finally { setBusy(false); }
  };

  return (
    <main className="login">
      <form className="panel login-card" onSubmit={submit}>
        <h1>tradedesk</h1>
        <p className="muted">News-aware trading, with every order behind a risk check.</p>
        <label>Email<input type="email" autoComplete="email" value={email} required
          onChange={(e) => setEmail(e.target.value)} /></label>
        <label>Password<input type="password" value={password} required minLength={mode === "register" ? 10 : undefined}
          autoComplete={mode === "register" ? "new-password" : "current-password"}
          onChange={(e) => setPassword(e.target.value)} /></label>
        {needCode && <label>Authenticator code<input inputMode="numeric" pattern="[0-9]{6}" value={code} autoFocus
          onChange={(e) => setCode(e.target.value)} /></label>}
        {error && <p className="error" role="alert">{error}</p>}
        <button className="primary" disabled={busy}>{mode === "login" ? "Sign in" : "Create account"}</button>
        <button type="button" className="link" onClick={() => { setMode(mode === "login" ? "register" : "login"); setError(null); }}>
          {mode === "login" ? "New here? Create an account" : "Have an account? Sign in"}
        </button>
      </form>
    </main>
  );
}
