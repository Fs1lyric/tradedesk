import { useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { api, hasSession, onAuthChange } from "./api";
import { AccountProvider, useAccounts } from "./account";
import { Backtests } from "./pages/Backtests";
import { Dashboard } from "./pages/Dashboard";
import { Login } from "./pages/Login";
import { NewsFeed } from "./pages/NewsFeed";
import { Orders } from "./pages/Orders";
import { Recommendations } from "./pages/Recommendations";
import { Rules } from "./pages/Rules";
import { Settings } from "./pages/Settings";

const NAV = [
  ["/", "Dashboard"], ["/news", "News"], ["/recommendations", "Recommendations"], ["/orders", "Orders"],
  ["/rules", "Rules"], ["/backtests", "Backtests"], ["/settings", "Settings"],
] as const;

function AccountPicker() {
  const { accounts, current, select } = useAccounts();
  if (!accounts.length) return null;
  return (
    <select value={current?.id} onChange={(e) => select(e.target.value)} aria-label="Account">
      {accounts.map((a) => <option key={a.id} value={a.id}>{a.label} ({a.mode})</option>)}
    </select>
  );
}

function Shell() {
  return (
    <AccountProvider>
      <div className="shell">
        <nav className="nav">
          <span className="brand">tradedesk</span>
          {NAV.map(([to, label]) => <NavLink key={to} to={to} end={to === "/"}>{label}</NavLink>)}
          <div className="spacer" />
          <AccountPicker />
          <button className="link" onClick={() => api.logout()}>Sign out</button>
        </nav>
        <main className="content">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/news" element={<NewsFeed />} />
            <Route path="/recommendations" element={<Recommendations />} />
            <Route path="/orders" element={<Orders />} />
            <Route path="/rules" element={<Rules />} />
            <Route path="/backtests" element={<Backtests />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="*" element={<Navigate to="/" />} />
          </Routes>
        </main>
      </div>
    </AccountProvider>
  );
}

export function App() {
  const [signedIn, setSignedIn] = useState(hasSession());
  useEffect(() => onAuthChange(setSignedIn), []);
  return signedIn ? <Shell /> : <Login />;
}
