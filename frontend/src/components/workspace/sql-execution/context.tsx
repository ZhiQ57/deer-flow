"use client";

import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { executeSql } from "@/core/sql-execution/api";
import type { SqlExecutionResult } from "@/core/sql-execution/types";

export interface SqlExecutionContextValue {
  enabled: boolean;
  open: boolean;
  sql: string;
  loading: boolean;
  result: SqlExecutionResult | null;
  error: string | null;
  execute: (sql: string) => Promise<void>;
  rerun: () => Promise<void>;
  close: () => void;
}

const SqlExecutionContext = createContext<SqlExecutionContextValue | null>(
  null,
);

export function SqlExecutionProvider({
  children,
  threadId,
  agentName,
  enabled,
}: {
  children: ReactNode;
  threadId: string;
  agentName?: string;
  enabled: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [sql, setSql] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<SqlExecutionResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const requestRef = useRef<AbortController | null>(null);
  const available = enabled && Boolean(agentName);

  const reset = useCallback(() => {
    requestRef.current?.abort();
    requestRef.current = null;
    setOpen(false);
    setSql("");
    setLoading(false);
    setResult(null);
    setError(null);
  }, []);

  useEffect(() => {
    reset();
    return () => {
      requestRef.current?.abort();
    };
  }, [agentName, available, reset, threadId]);

  const execute = useCallback(
    async (nextSql: string) => {
      if (!available || !agentName) return;

      requestRef.current?.abort();
      const controller = new AbortController();
      requestRef.current = controller;
      setOpen(true);
      setSql(nextSql);
      setLoading(true);
      setResult(null);
      setError(null);

      try {
        const nextResult = await executeSql(threadId, {
          agentName,
          sql: nextSql,
          signal: controller.signal,
        });
        if (requestRef.current === controller) {
          setResult(nextResult);
        }
      } catch (requestError) {
        if (
          requestRef.current === controller &&
          !(
            requestError instanceof DOMException &&
            requestError.name === "AbortError"
          )
        ) {
          setError(
            requestError instanceof Error
              ? requestError.message
              : "SQL execution request failed",
          );
        }
      } finally {
        if (requestRef.current === controller) {
          requestRef.current = null;
          setLoading(false);
        }
      }
    },
    [agentName, available, threadId],
  );

  const rerun = useCallback(async () => {
    if (sql) await execute(sql);
  }, [execute, sql]);

  const close = useCallback(() => {
    setOpen(false);
  }, []);

  const value = useMemo<SqlExecutionContextValue>(
    () => ({
      enabled: available,
      open,
      sql,
      loading,
      result,
      error,
      execute,
      rerun,
      close,
    }),
    [available, close, error, execute, loading, open, rerun, result, sql],
  );

  return (
    <SqlExecutionContext.Provider value={value}>
      {children}
    </SqlExecutionContext.Provider>
  );
}

export function useSqlExecution(): SqlExecutionContextValue {
  const context = useContext(SqlExecutionContext);
  if (!context) {
    throw new Error("useSqlExecution must be used within SqlExecutionProvider");
  }
  return context;
}

export function useMaybeSqlExecution(): SqlExecutionContextValue | null {
  return useContext(SqlExecutionContext);
}
