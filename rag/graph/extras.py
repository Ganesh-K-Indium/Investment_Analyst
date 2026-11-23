"""
Graph nodes and edges added by the configurable pipeline:

    route_query        one node that decides the workflow (replaces the three serial detect_* nodes): built-in modules the user
                       enabled, then the user's own frameworks, then the standard pipeline
    clarify            optional human-in-the-loop pause for a question that does not say which company it is about
    rewrite_query      corrective retrieval: rewrite the query from what grading found missing and search again
    verify_answer      check every figure in the answer against the retrieved evidence (rag/verify/numbers.py)
    custom_retrieve / custom_generate   run a user-defined framework (name, sections, sources, prompt)
    decide_after_grade the user's web-fallback and retry choices applied to the grader's verdict
"""
import asyncio
import logging
import re
from typing import Any, Dict, List, Optional

from langchain_core.documents import Document
from langchain_core.messages import HumanMessage, SystemMessage

from app.services import app_settings
from rag.graph.doc_kinds import split_notes
from rag.verify import numbers

logger = logging.getLogger("rag.graph.extras")


# ---------------------------------------------------------------------------------------------------------------------
# Frameworks (modules)
# ---------------------------------------------------------------------------------------------------------------------

def enabled_builtin(module_id: str) -> bool:
    return module_id in app_settings.get("modules", "enabled", ["alpha", "scenario", "macro"])


def custom_frameworks() -> List[Dict[str, Any]]:
    return [f for f in app_settings.get("modules", "custom", []) if f.get("enabled", True)]


def match_custom_framework(question: str, forced_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The user's framework this question belongs to: named explicitly (the UI's framework picker) or matched by a trigger phrase."""
    fws = custom_frameworks()
    if forced_id:
        return next((f for f in fws if f["id"] == forced_id), None)
    q = (question or "").lower()
    return next((f for f in fws if any(t.strip().lower() in q for t in f.get("triggers", []) if t.strip())), None)


def route_query(state):
    """Single entry decision. Each built-in detector is a cheap keyword check; the built-ins the user switched off are skipped."""
    from rag.graph import nodes
    question = state["messages"][-1].content
    update: Dict[str, Any] = {}
    if enabled_builtin("alpha"):
        update.update(nodes.detect_alpha_query(state))
    if enabled_builtin("scenario") and not update.get("alpha_mode"):
        update.update(nodes.detect_scenario_query({**state, **update}))
    if enabled_builtin("macro") and not (update.get("alpha_mode") or update.get("scenario_mode")):
        update.update(nodes.detect_macro_query({**state, **update}))
    fw = None if (update.get("alpha_mode") or update.get("scenario_mode") or update.get("macro_mode")) else match_custom_framework(question, state.get("module_id"))
    update["custom_module"] = fw
    return update


def route_workflow(state):
    if state.get("alpha_mode"):
        return "alpha"
    if state.get("scenario_mode"):
        return "scenario"
    if state.get("macro_mode"):
        return "macro"
    if state.get("custom_module"):
        return "custom"
    return "normal"


def _as_documents(points) -> List[Document]:
    """Qdrant hits -> Documents (the search helpers return raw points)."""
    out = []
    for p in points or []:
        payload = getattr(p, "payload", None) or {}
        if payload.get("page_content"):
            out.append(Document(page_content=payload["page_content"], metadata=dict(payload.get("metadata") or {})))
    return out


async def custom_retrieve(state, config=None):
    """Search each section of the user's framework in the sources the framework (and the user's settings) allow: the company's SEC filings,
    the user's uploaded documents, and their notes. Chunks are grouped by section for the writer."""
    from rag.graph import nodes
    from app.services.vectordb_manager import get_vectordb_manager
    from app.utils.company_mapping import get_most_recent_filed_fiscal_year
    fw = state["custom_module"]
    question = state["messages"][-1].content
    allowed = set(app_settings.get("retrieval", "sources", [])) & set(fw.get("sources") or ["filings", "uploads", "notes"])
    ticker = state.get("ticker") or next(iter(state.get("company_filter") or []), None) or nodes._extract_ticker_from_free_text(question)
    sections = fw.get("sections") or ["Overview"]
    limit = max(3, int(app_settings.get("retrieval", "top_k", 12)) // 2)
    user_id = ((config or {}).get("configurable") or {}).get("user_id")
    by_section: Dict[str, List[Document]] = {sec: [] for sec in sections}

    if ticker and "filings" in allowed:
        try:
            db = get_vectordb_manager().get_instance(ticker, create_if_missing=False)
            year = get_most_recent_filed_fiscal_year(ticker)
            hits = await asyncio.gather(*[nodes._alpha_vdb_search(db, f"{fw['name']} {sec} {ticker}", limit, year) for sec in sections], return_exceptions=True)
            for sec, res in zip(sections, hits):
                if isinstance(res, Exception):
                    logger.warning("Custom framework %s: filings search for %r failed: %s", fw["id"], sec, res)
                else:
                    by_section[sec].extend(_as_documents(res))
        except Exception as e:  # a company with no filings collection must not break a framework that also has uploads or notes
            logger.warning("Custom framework %s: no filings for %s (%s)", fw["id"], ticker, e)
    if user_id and "uploads" in allowed:
        from app.services.user_docs import store as udocs
        hits = await asyncio.gather(*[udocs.search(user_id, f"{fw['name']} {sec} {question}", limit=max(2, limit // 2), doc_ids=state.get("document_ids") or None) for sec in sections], return_exceptions=True)
        for sec, res in zip(sections, hits):
            if isinstance(res, Exception):
                logger.warning("Custom framework %s: uploads search for %r failed: %s", fw["id"], sec, res)
            else:
                by_section[sec].extend(res)
    notes = []
    if "notes" in allowed:
        notes = await nodes._analyst_note_documents(config or {}, [ticker] if ticker else [], f"{fw['name']} {question}")
    flat = [d for docs in by_section.values() for d in docs]
    return {"documents": flat + notes, "ticker": ticker, "custom_sections": {k: [d.page_content for d in v] for k, v in by_section.items()}}


def custom_generate(state):
    from rag.graph import nodes
    fw = state["custom_module"]
    question = state["messages"][-1].content
    secs = state.get("custom_sections") or {}
    ctx = "\n\n".join(f"### {sec}\n" + "\n---\n".join(chunks[:6]) if chunks else f"### {sec}\n(no evidence found)" for sec, chunks in secs.items())
    notes = [d.page_content for d in split_notes(state.get("documents") or [])[1]]
    system = (fw.get("prompt") or "You are a rigorous equity analyst.") + (
        "\n\nWrite a report with exactly one section per heading below, in order. Use ONLY the evidence under each heading; if it is empty, say the "
        "documents do not cover it. Quote figures exactly with their period and unit. Never invent numbers.\n\nHeadings: " + "; ".join(secs.keys()))
    if notes:
        system += "\n\nThe analyst's own notes are included after the evidence. Treat them as the analyst's views, not as filings."
    llm = app_settings.get_llm("analysis")
    out = llm.invoke([SystemMessage(content=system), HumanMessage(content=f"Question: {question}\n\nEvidence:\n{ctx}" + ("\n\nAnalyst notes:\n" + "\n---\n".join(notes) if notes else ""))])
    return {"Intermediate_message": out.content}


# ---------------------------------------------------------------------------------------------------------------------
# Clarification (human in the loop)
# ---------------------------------------------------------------------------------------------------------------------

_ALL_WORDS = re.compile(r"\b(all|each|every|portfolio|compare|comparison|versus|vs\.?|across|both)\b", re.I)


def needs_clarification(state) -> Optional[str]:
    """A question that could be about several companies in the portfolio but names none of them. Heuristic and conservative:
    it only asks when the scope is a multi-company portfolio and nothing in the question points at one company or at all of them."""
    if state.get("alpha_mode") or state.get("scenario_mode") or state.get("macro_mode") or state.get("ticker") or state.get("clarified"):
        return None
    scope = state.get("company_filter") or []
    if len(scope) < 2:
        return None
    q = state["messages"][-1].content
    from app.utils.company_mapping import get_company_aliases
    low = q.lower()
    if _ALL_WORDS.search(q) or any(t.lower() in re.findall(r"[a-z0-9.]+", low) for t in scope):
        return None
    try:
        if any(a.lower() in low for t in scope for a in (get_company_aliases(t) or [])):
            return None
    except Exception:
        pass
    return f"Which company do you mean? Your portfolio has {', '.join(scope)}. Name one, or say \"all\" to cover every company."


def clarify(state):
    """Pause the run and ask. The graph resumes with the user's answer (LangGraph `interrupt`); the answer is appended to the question."""
    if not app_settings.get("answers", "clarify_ambiguous", False):
        return {}
    ask = needs_clarification(state)
    if not ask:
        return {}
    from langgraph.types import interrupt
    reply = interrupt({"type": "clarification", "question": ask})
    reply = str(reply or "").strip()
    scope = state.get("company_filter") or []
    picked = [t for t in scope if re.search(rf"\b{re.escape(t)}\b", reply, re.I)]
    update: Dict[str, Any] = {"clarified": True, "messages": [HumanMessage(content=f"{state['messages'][-1].content} (regarding: {reply})")]}
    if len(picked) == 1:
        update["company_filter"] = picked
    return update


# ---------------------------------------------------------------------------------------------------------------------
# Corrective retrieval
# ---------------------------------------------------------------------------------------------------------------------

def decide_after_grade(state):
    """grade_documents -> generate | rewrite | integrate_web_search, applying the user's retry count and web-fallback choice."""
    from rag.graph.edges import decide_to_generate
    verdict = decide_to_generate(state)
    if verdict != "integrate_web_search":
        return verdict
    if state.get("qdrant_error") or state.get("web_searched"):
        return "generate"
    retries = int(app_settings.get("retrieval", "corrective_retries", 1))
    docs, _notes = split_notes(state.get("documents") or [])
    if state.get("rewrite_count", 0) < retries and "filings" in app_settings.get("retrieval", "sources", ["filings"]):
        return "rewrite"
    return "integrate_web_search" if app_settings.get("retrieval", "web_fallback", True) and "web" in app_settings.get("retrieval", "sources", ["web"]) else "generate"


_REWRITE_SYSTEM = ("You rewrite a search query for a hybrid (keyword + semantic) index of company filings. The first search did not find enough. "
                   "Use the missing information to write ONE better query: spell out abbreviations, use the line-item names and section titles filings use "
                   "(e.g. 'Net revenues', 'Item 7 Management's Discussion', 'segment operating income'), keep every company, period and filing type from the "
                   "question. Output only the query.")


def rewrite_query(state):
    question = state["messages"][-1].content
    grading = state.get("financial_grading") or {}
    missing = grading.get("missing_data_summary") or "the passages did not contain the figures needed"
    prior = (state.get("sub_query_analysis") or {}).get("optimized_query") or question
    llm = app_settings.get_llm("rewriter")
    new = llm.invoke([SystemMessage(content=_REWRITE_SYSTEM), HumanMessage(content=f"Question: {question}\nPrevious query: {prior}\nMissing: {missing}")]).content.strip().strip('"')
    if not new or new.lower() == prior.lower():
        new = f"{question} {missing}"[:400]
    logger.info(f"[CORRECTIVE] retry {state.get('rewrite_count', 0) + 1}: {new!r}")
    sqa = {**(state.get("sub_query_analysis") or {}), "needs_sub_queries": False, "sub_queries": [], "optimized_query": new}
    return {"sub_query_analysis": sqa, "rewrite_count": state.get("rewrite_count", 0) + 1, "documents": []}


# ---------------------------------------------------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------------------------------------------------

def verify_answer(state):
    mode = app_settings.get("answers", "verify_mode", "warn")
    answer = state.get("Intermediate_message") or ""
    if mode == "off" or not answer or state.get("qdrant_error"):
        return {}
    docs = state.get("documents") or []
    # Only filing/note/web chunks the answer could have used. Macro answers are computed in code and carry their own data.
    if state.get("macro_mode") or not docs:
        return {}
    result = numbers.verify(answer, [getattr(d, "page_content", str(d)) for d in docs])
    out: Dict[str, Any] = {"verification": {**result, "mode": mode}}
    extra = numbers.note(result, mode)
    if extra:
        out["Intermediate_message"] = f"{extra}\n\n{answer}" if mode == "strict" else f"{answer}\n\n{extra}"
        logger.warning(f"[VERIFY] {len(result['issues'])} figure(s) not supported: {[i['text'] for i in result['issues']]}")
    return out
