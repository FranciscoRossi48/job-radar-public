"""Selección del evaluador LLM configurado."""

from __future__ import annotations

import sqlite3

from job_radar.config import Config


def build_evaluator(cfg: Config, conn: sqlite3.Connection):
    if cfg.llm.provider == "agentrouter_codex":
        from job_radar.evaluate_codex import CodexEvaluator

        return CodexEvaluator(cfg, conn)
    if cfg.llm.provider == "agentrouter":
        from job_radar.evaluate_agentrouter import AgentRouterEvaluator

        return AgentRouterEvaluator(cfg, conn)
    if cfg.llm.provider == "gemini":
        from job_radar.evaluate_gemini import GeminiEvaluator

        return GeminiEvaluator(cfg, conn)
    raise ValueError(f"proveedor LLM no soportado: {cfg.llm.provider}")
