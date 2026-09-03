from __future__ import annotations

import re

import sympy
from sympy import Eq, Symbol, solve
from sympy.parsing.sympy_parser import (
    implicit_multiplication_application,
    parse_expr,
    standard_transformations,
)

from app.core.llm import generate
from app.core.llm_budget import consume_llm_budget
from app.core.logging import get_logger

logger = get_logger(__name__)

# Math Lab (Phase 12): a scoped-down "step-by-step equation solving" tool
# -- single-variable equations only ("x"), not the plan's fuller
# geometry/graphing toolset. That narrowing is a deliberate security
# choice, not just a time one: sympy's expression parser is eval-based,
# and eval-ing arbitrary untrusted text is a real code-execution risk,
# not a hypothetical one. Two independent layers here instead of trusting
# either alone:
#   1. A character whitelist restricted to digits, the single letter "x",
#      and +-*/^().= -- no other identifier can ever form, so there is no
#      module or attribute name (os, __import__, ...) for an expression
#      to reference even if the parser's internals mishandled it.
#   2. A global_dict on every parse call that carries sympy's own public
#      namespace (Integer, Symbol, Add, Pow, ... -- everything the
#      generated code actually needs) but with "__builtins__" pinned to
#      an empty dict. Python's eval/exec only auto-injects the real
#      builtins into a namespace that's missing that key entirely --
#      one that's already present, even empty, is left alone. So even a
#      parser bug that reached Python's real eval() would still have no
#      builtins (no __import__, no open, no exec) to call.
# A fuller "any equation, any variable" solver is a real follow-up, but
# it needs its own dedicated sandboxing design, not an afterthought here.

_SAFE_PATTERN = re.compile(r"^[0-9x+\-*/^().\s=]+$")
_MAX_LENGTH = 200
_TRANSFORMATIONS = standard_transformations + (implicit_multiplication_application,)
_X = Symbol("x")
_SAFE_GLOBALS: dict = {"__builtins__": {}}
_SAFE_GLOBALS.update({name: value for name, value in vars(sympy).items() if not name.startswith("_")})


class InvalidEquation(Exception):
    pass


def _validate(text: str) -> None:
    if not text.strip():
        raise InvalidEquation("Enter an equation first.")
    if len(text) > _MAX_LENGTH:
        raise InvalidEquation("That equation is too long.")
    if not _SAFE_PATTERN.match(text):
        raise InvalidEquation('Only numbers, "x", and + - * / ^ ( ) = are allowed.')


def _parse_side(text: str):
    try:
        return parse_expr(
            text.replace("^", "**"),
            local_dict={"x": _X},
            global_dict=_SAFE_GLOBALS,
            transformations=_TRANSFORMATIONS,
        )
    except Exception as exc:
        raise InvalidEquation("Couldn't parse that -- check the syntax.") from exc


def solve_equation(text: str) -> dict:
    """Raises InvalidEquation with a message safe to show the student
    directly. Never raises a raw sympy/parser exception outward."""
    _validate(text)

    sides = text.split("=")
    if len(sides) != 2:
        raise InvalidEquation('Enter an equation with exactly one "=", like 2x + 5 = 15.')

    lhs = _parse_side(sides[0])
    rhs = _parse_side(sides[1])
    equation = Eq(lhs, rhs)

    try:
        solutions = solve(equation, _X)
    except Exception as exc:
        raise InvalidEquation("Couldn't solve that equation.") from exc

    if not solutions:
        raise InvalidEquation("That equation has no solution for x (or is always true/false).")

    return {
        "equation_display": f"{lhs} = {rhs}",
        "solutions": [str(s) for s in solutions],
    }


_STEPS_PROMPT = """The equation {equation} has already been solved for x: x = {solutions}.
That answer is verified and correct -- do not change it. Write a short (3-5 step)
explanation of how to get from the equation to that answer, one step per line, no
markdown fences, no commentary before or after the steps.
"""


async def generate_steps(
    pool, user_id: int, equation_display: str, solutions: list[str], *, ai_available: bool
) -> str | None:
    """Best-effort -- the solution itself already came from sympy above,
    so a failure or missing AI provider here just means no walkthrough
    text, never a wrong answer shown to the student."""
    if not ai_available:
        return None
    try:
        await consume_llm_budget(pool, user_id)
    except Exception:
        return None
    try:
        prompt = _STEPS_PROMPT.format(equation=equation_display, solutions=", ".join(solutions))
        return await generate(prompt, temperature=0.3, cacheable=True)
    except Exception:
        logger.warning("Math Lab step generation failed", exc_info=True)
        return None
