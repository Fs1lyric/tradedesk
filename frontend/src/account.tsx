import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { api } from "./api";
import type { Account } from "./types";

interface AccountState {
  accounts: Account[];
  current: Account | null;
  select: (id: string) => void;
  reload: () => Promise<void>;
}

const Ctx = createContext<AccountState>({ accounts: [], current: null, select: () => {}, reload: async () => {} });
const KEY = "tradedesk.account";

export function AccountProvider({ children }: { children: ReactNode }) {
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [selected, setSelected] = useState<string | null>(() => {
    try { return localStorage.getItem(KEY); } catch { return null; }
  });
  const reload = async () => setAccounts(await api.accounts());
  useEffect(() => { reload().catch(() => {}); }, []);
  const current = accounts.find((a) => a.id === selected) ?? accounts[0] ?? null;
  const select = (id: string) => {
    setSelected(id);
    try { localStorage.setItem(KEY, id); } catch { /* private mode */ }
  };
  return <Ctx.Provider value={{ accounts, current, select, reload }}>{children}</Ctx.Provider>;
}

export const useAccounts = () => useContext(Ctx);
