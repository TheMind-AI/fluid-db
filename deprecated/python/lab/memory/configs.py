"""Configurations: an experiment names one choice per part (LLP 0013#protocol). Adding a variant is one line here.

  stores    which stores exist                  router   all / jev / llm / oracle (threshold: p to keep a store)
  answerer  llm (evidence budget) / full (no budget) / hybrid (the round-4 reader)
  lesion    stores still routed to that return nothing (necessity); plus_X adds one store to facts + log (sufficiency)
"""
# @ref LLP 0013#protocol — the configs; lesions remove one store from brain_all
from __future__ import annotations

from lab.memory import answerers, deciders, routers
from lab.memory.core import Memory
from lab.memory.stores import STORES

BRAIN = ["facts", "log", "episodes", "routines", "intentions", "preferences", "periods"]

CONFIGS = {
    "hybrid_r4": {"stores": [], "router": "all", "answerer": "hybrid"},
    "facts_only": {"stores": ["facts"], "router": "all", "answerer": "llm"},
    "log_rag": {"stores": ["log"], "router": "all", "answerer": "llm"},
    "facts_log": {"stores": ["facts", "log"], "router": "all", "answerer": "llm"},
    "brain_jev": {"stores": BRAIN, "router": "jev", "answerer": "llm"},
    "brain_jev_recall": {"stores": BRAIN, "router": "jev", "threshold": 0.3, "answerer": "llm"},
    "brain_llm_router": {"stores": BRAIN, "router": "llm", "answerer": "llm"},
    "brain_all": {"stores": BRAIN, "router": "all", "answerer": "llm"},
    "brain_oracle": {"stores": BRAIN, "router": "oracle", "answerer": "llm"},
    **{f"lesion_{s}": {"stores": BRAIN, "router": "all", "answerer": "llm", "lesion": [s]} for s in BRAIN},
    **{f"plus_{s}": {"stores": ["facts", "log", s], "router": "all", "answerer": "llm"} for s in BRAIN[2:]},
    "plus_computed": {"stores": ["facts", "log", "routines", "periods"], "router": "all", "answerer": "llm"},
    "full_context": {"stores": ["log_all"], "router": "all", "answerer": "full"},
    # round 9 (LLP 0016): the whole log plus the database's exact computed answer and matching rows
    "full_facts": {"stores": ["facts", "log_all"], "router": "all", "answerer": "full"},
    # LLP 0017: another product's memory (LAB_PRODUCT_MEMORY): all it stores, and what its reader injects
    "product": {"stores": ["product"], "router": "all", "answerer": "llm"},
    "product_injected": {"stores": ["product_injected"], "router": "all", "answerer": "llm"},
    # LLP 0019: memory for conversation
    "log_embed": {"stores": ["log_embed"], "router": "all", "answerer": "llm"},
    "log_hybrid": {"stores": ["log_hybrid"], "router": "all", "answerer": "llm"},
    "statements": {"stores": ["statements"], "router": "all", "answerer": "llm"},
    "dossier": {"stores": ["dossier"], "router": "all", "answerer": "llm"},
    "conv_all": {"stores": ["dossier", "statements", "log_hybrid"], "router": "all", "answerer": "llm", "budget": 48000},
    # exploratory, after the round's results: which parts conv_all needs, and a lean pair
    "conv_no_dossier": {"stores": ["statements", "log_hybrid"], "router": "all", "answerer": "llm", "budget": 48000},
    "conv_no_statements": {"stores": ["dossier", "log_hybrid"], "router": "all", "answerer": "llm", "budget": 48000},
    "conv_no_log": {"stores": ["dossier", "statements"], "router": "all", "answerer": "llm", "budget": 48000},
    "statements_embed": {"stores": ["statements", "log_embed"], "router": "all", "answerer": "llm"},
    # LLP 0020: merged statements, a dossier kept current, and the proposed best way (both searches reranked)
    "statements_merged": {"stores": ["statements_merged"], "router": "all", "answerer": "llm"},
    "dossier_inc": {"stores": ["dossier_inc"], "router": "all", "answerer": "llm"},
    "conv_best": {"stores": ["dossier_inc", "statements_rr", "log_rr"], "router": "all", "answerer": "llm", "budget": 48000},
    # LLP 0020 Part D: Jev routes over the new stores (the OpenAI shim on other people's data)
    "conv_router": {"stores": ["dossier_inc", "statements_rr", "log_rr", "log_embed", "log"], "router": "jev",
                    "threshold": 0.3, "answerer": "llm", "budget": 48000},
    # LLP 0021 Part B: every wording kept; the new best with Part A's best picker (P3, the model ranking a list), and
    # (exploratory) the same with Jev's closed questions on whole windows, affordable at every turn
    "statements_linked": {"stores": ["statements_linked"], "router": "all", "answerer": "llm"},
    "conv_best2": {"stores": ["dossier_inc", "statements_linked_p3", "log_p3"], "router": "all", "answerer": "llm",
                   "budget": 48000},
    "conv_best2_jev": {"stores": ["dossier_inc", "statements_linked_rr", "log_rr_full"], "router": "all",
                       "answerer": "llm", "budget": 48000},
}


def build_stores(ctx, names) -> dict:
    """Build each store once; configs share the built instances."""
    out, stats = {}, {}
    for n in names:
        out[n] = STORES[n]()
        stats[n] = out[n].build(ctx)
    return out, stats


def make(name: str, built: dict, ctx, types: dict[str, str] | None = None) -> Memory:
    cfg = CONFIGS[name]
    router = routers.make(cfg["router"], deciders.make(cfg["router"]) if cfg["router"] in ("jev", "llm") else None, types,
                          threshold=cfg.get("threshold", 0.5))
    answerer = {"llm": lambda: answerers.LLMAnswerer(budget=cfg.get("budget", 24000)),
                "full": lambda: answerers.LLMAnswerer(budget=None, tag="memory:answer:full"),
                "hybrid": lambda: answerers.HybridFacts()}[cfg["answerer"]]()
    return Memory(name, [built[s] for s in cfg["stores"]], router, answerer, ctx, lesion=tuple(cfg.get("lesion", ())))
