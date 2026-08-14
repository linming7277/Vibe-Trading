import { useCallback, useEffect, useState } from "react";
import { safeGet, safeRemove, safeSet } from "@/lib/storage";

const STORAGE_KEY = "hz-decision-flow-v1";
const CHANGE_EVENT = "hengzhi:decision-flow";

export interface DecisionFlowState {
  macro_headline?: string;
  macro_stance?: string;
  macro_as_of?: string;
  sector_code?: string;
  sector_name?: string;
  symbol?: string;
  company_name?: string;
  research_report_id?: string;
  research_completed_at?: string;
  trade_plan_id?: string;
  updated_at?: string;
}

function readState(): DecisionFlowState {
  try {
    return JSON.parse(safeGet(STORAGE_KEY) || "{}") as DecisionFlowState;
  } catch {
    return {};
  }
}

function writeState(value: DecisionFlowState) {
  safeSet(STORAGE_KEY, JSON.stringify(value));
  window.dispatchEvent(new CustomEvent(CHANGE_EVENT, { detail: value }));
}

export function useDecisionFlow() {
  const [flow, setFlow] = useState<DecisionFlowState>(readState);
  useEffect(() => {
    const sync = (event: Event) => setFlow((event as CustomEvent<DecisionFlowState>).detail || readState());
    window.addEventListener(CHANGE_EVENT, sync);
    return () => window.removeEventListener(CHANGE_EVENT, sync);
  }, []);

  const update = useCallback((patch: Partial<DecisionFlowState>) => {
    const next = { ...readState(), ...patch, updated_at: new Date().toISOString() };
    writeState(next);
    return next;
  }, []);
  const selectMacro = useCallback((value: { headline: string; stance: string; as_of: string }) => {
    writeState({ macro_headline: value.headline, macro_stance: value.stance, macro_as_of: value.as_of, updated_at: new Date().toISOString() });
  }, []);
  const selectSector = useCallback((value: { code: string; name: string }) => {
    const current = readState();
    writeState({
      macro_headline: current.macro_headline, macro_stance: current.macro_stance, macro_as_of: current.macro_as_of,
      sector_code: value.code, sector_name: value.name, updated_at: new Date().toISOString(),
    });
  }, []);
  const selectLeader = useCallback((value: { symbol: string; name: string }) => {
    const current = readState();
    writeState({ ...current, symbol: value.symbol, company_name: value.name, research_report_id: undefined, research_completed_at: undefined, trade_plan_id: undefined, updated_at: new Date().toISOString() });
  }, []);
  const reset = useCallback(() => {
    safeRemove(STORAGE_KEY);
    window.dispatchEvent(new CustomEvent(CHANGE_EVENT, { detail: {} }));
  }, []);
  return { flow, update, selectMacro, selectSector, selectLeader, reset };
}

