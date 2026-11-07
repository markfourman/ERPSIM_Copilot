"""The chat layer: an AI model reads live numbers through the engine's tools and answers in plain words.

Default: Google Gemini (free tier, key from Google AI Studio). Option: Claude (paid API).
Advisory only. It never touches SAP; people type every action.
"""
from __future__ import annotations

import json
from typing import Callable

from .engine import TOOLS, snapshot

SYSTEM = """You are the ERPsim Co-pilot for a team playing the SAP ERPsim Manufacturing (muesli) game.
You advise; you never operate SAP and never say you did. The humans type every SAP action.

How to answer
- Each question comes with a live snapshot. Call the tools for anything more (production, prices, stock, money,
  marketing, round 4). Never invent numbers.
- Lead with the decision in one line. Then the exact inputs to type: transaction (MD61, MD01, ME59N, CO41, VK32, ZFB50, ZADS),
  material code (e.g. EE-F16), quantity or price per channel (10 / 12 / 14). Then one or two reasons with numbers. Then any risk.
- Be brief: a game day lasts about 45 seconds. Under ~120 words unless asked for detail. Plain language, no jargon.
- Live data beats memory. If they disagree, say so in one line.
- If a needed table is missing (for example marketing spend), say which OData table to load.
- Production orders should be whole 48,000 batches. The MD61 number to type = stock + queued + whole batches.
- Never suggest a price move bigger than the tool's suggestion or below the floor, unless the team's memory has a rule
  for it (then say which rule).

The team's own memory (their strategy, rules and lessons) follows. Use it as their playbook.
<memory>
{memory}
</memory>

Summaries of the team's past games:
<history>
{history}
</history>
"""

SCHEMAS = {
    "price_advice": {"product": {"type": "string", "description": "Optional product name or code, e.g. F16"}},
    "capacity_vs_loan": {"amount": {"type": "number", "description": "Euros to compare, default 1000000"}},
}

RATE_LIMIT_MSG = ("The free AI limit for this minute is used up. Wait about a minute and ask again. "
                  "Every other tab keeps working.")


def _jsonable(data):
    return json.loads(json.dumps(data, default=str))


class Copilot:
    def __init__(self, settings, get_state: Callable, memory_text: str, history_text: str):
        self.settings = settings
        self.provider = settings.provider
        self.model = settings.chat_model()
        self.get_state = get_state
        self.system = SYSTEM.format(memory=memory_text, history=history_text)
        self.history: list[tuple[str, str]] = []
        if self.provider == "claude":
            from anthropic import Anthropic  # optional: pip install anthropic

            self.client = Anthropic()
            self.tools = [{"name": n, "description": d,
                           "input_schema": {"type": "object", "properties": SCHEMAS.get(n, {})}} for n, (_, d) in TOOLS.items()]
        else:
            from google import genai
            from google.genai import types

            self.types = types
            self.client = genai.Client()  # reads GEMINI_API_KEY (or GOOGLE_API_KEY) from the environment
            decls = []
            for n, (_, d) in TOOLS.items():
                decl = {"name": n, "description": d}
                if SCHEMAS.get(n):
                    decl["parameters"] = {"type": "object", "properties": SCHEMAS[n]}
                decls.append(decl)
            self.config = types.GenerateContentConfig(system_instruction=self.system,
                                                      tools=[types.Tool(function_declarations=decls)])

    # ------------------------------------------------------------------ shared
    def _run_tool(self, state, name: str, args: dict):
        fn, _ = TOOLS.get(name, (None, None))
        if not fn:
            return {"error": f"unknown tool {name}"}
        allowed = SCHEMAS.get(name, {})
        args = {k: v for k, v in (args or {}).items() if k in allowed}
        try:
            return _jsonable(fn(state, **args))
        except Exception as e:  # report tool errors to the model instead of crashing
            return {"error": str(e)}

    def ask(self, question: str, max_steps: int = 6) -> dict:
        state = self.get_state()                       # fresh data for every question
        live = json.dumps(_jsonable(snapshot(state)))
        turn = f"{question}\n\n<live_snapshot>{live}</live_snapshot>"
        try:
            if self.provider == "claude":
                answer, calls = self._ask_claude(state, turn, max_steps)
            else:
                answer, calls = self._ask_gemini(state, turn, max_steps)
        except Exception as e:
            msg = str(e)
            if "429" in msg or "RESOURCE_EXHAUSTED" in msg or "rate limit" in msg.lower():
                return {"answer": RATE_LIMIT_MSG, "tool_calls": []}
            return {"answer": f"The AI service returned an error: {msg[:300]}", "tool_calls": []}
        self.history.append((question, answer))
        return {"answer": answer, "tool_calls": calls}

    # ------------------------------------------------------------------ Gemini
    def _ask_gemini(self, state, turn: str, max_steps: int):
        T = self.types
        contents = []
        for q, a in self.history[-3:]:                 # short memory of the chat itself
            contents += [T.Content(role="user", parts=[T.Part(text=q)]), T.Content(role="model", parts=[T.Part(text=a)])]
        contents.append(T.Content(role="user", parts=[T.Part(text=turn)]))
        calls = []
        for _ in range(max_steps):
            resp = self.client.models.generate_content(model=self.model, contents=contents, config=self.config)
            fcs = resp.function_calls or []
            if not fcs:
                return (getattr(resp, "text", None) or "").strip() or "(no answer)", calls
            contents.append(resp.candidates[0].content)  # keep the model's turn as-is (includes its call details)
            parts = []
            for fc in fcs:
                data = self._run_tool(state, fc.name, dict(fc.args or {}))
                calls.append(fc.name)
                text = json.dumps(data)
                payload = data if len(text) <= 20000 else {"result_text": text[:20000]}
                parts.append(T.Part.from_function_response(name=fc.name, response={"result": payload}))
            contents.append(T.Content(role="user", parts=parts))
        return "I needed too many steps. Ask a narrower question.", calls

    # ------------------------------------------------------------------ Claude (optional)
    def _ask_claude(self, state, turn: str, max_steps: int):
        messages = []
        for q, a in self.history[-3:]:
            messages += [{"role": "user", "content": q}, {"role": "assistant", "content": a}]
        messages.append({"role": "user", "content": turn})
        calls = []
        for _ in range(max_steps):
            resp = self.client.messages.create(model=self.model, max_tokens=1200, system=self.system,
                                               tools=self.tools, messages=messages)
            messages.append({"role": "assistant", "content": resp.content})
            if resp.stop_reason != "tool_use":
                return "".join(b.text for b in resp.content if b.type == "text").strip(), calls
            results = []
            for block in resp.content:
                if block.type == "tool_use":
                    calls.append(block.name)
                    data = self._run_tool(state, block.name, block.input)
                    results.append({"type": "tool_result", "tool_use_id": block.id, "content": json.dumps(data)[:20000]})
            messages.append({"role": "user", "content": results})
        return "I needed too many steps. Ask a narrower question.", calls
