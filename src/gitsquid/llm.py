from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from .config import Settings
from .models import ChangeSource
from .safety import clean_text_input

MAX_OUTPUT_TOKENS = 32_000
FALLBACK_MODELS = ("claude-opus-5", "claude-fable-5")

SYSTEM_PROMPT = """You are the proposal engine of GitSquid, a repository-local Git assistant.

You receive a task and a few excerpts from one repository. You reply with exactly two things:

1. A short rationale: at most six lines of plain prose, explaining what you change and why.
2. One unified diff in a fenced ```diff block, and nothing after it.

Rules for the diff:
- It must apply with `git apply -p1` against the excerpts shown, using `a/` and `b/` path prefixes.
- Include a `diff --git a/<path> b/<path>` header and `@@` hunks with correct line numbers and
  three lines of context where the file allows it.
- Paths are relative to the repository root. Never touch `.git/`, `.gitsquid/`, `.env`, keys, or
  anything outside the repository.
- Change the least that completes the task. No unrelated reformatting, no placeholder bodies,
  no `TODO` stubs standing in for the work.
- For a new file, diff against `/dev/null` and use `new file mode 100644`.

You only see the excerpts provided. If they are not enough to write a correct patch, say so in
the rationale and emit no diff block rather than guessing at code you cannot see."""

_FENCED = re.compile(r"```(?:diff|patch)?\s*\n(.*?)```", re.DOTALL)


class ProposalError(Exception):
    pass


@dataclass(frozen=True)
class ProposalRequest:
    task: str
    context: str
    files: list[str]
    repo_name: str


@dataclass(frozen=True)
class Proposal:
    diff: str
    rationale: str
    source: ChangeSource
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


class ProposalBackend(Protocol):
    name: str

    def propose(self, request: ProposalRequest) -> Proposal: ...


def build_user_prompt(request: ProposalRequest) -> str:
    files = "\n".join(f"- {path}" for path in request.files) or "- (no matching file found)"
    return (
        f"Repository: {request.repo_name}\n\n"
        f"Task:\n{request.task}\n\n"
        f"Files these excerpts come from:\n{files}\n\n"
        f"Repository excerpts:\n{request.context}\n"
    )


def split_response(text: str) -> tuple[str, str]:
    """Return (rationale, diff) from a model reply."""
    match = _FENCED.search(text)
    if not match:
        return text.strip(), ""
    diff = match.group(1).strip("\n")
    rationale = text[: match.start()].strip()
    return rationale, diff + "\n" if diff else ""


class PatchFileBackend:
    """Degraded mode: the user supplies the diff, GitSquid does everything else."""

    name = "patch-file"

    def __init__(self, diff: str, rationale: str = "") -> None:
        self._diff = diff
        self._rationale = rationale or "Patch supplied by the user, no model was called."

    def propose(self, request: ProposalRequest) -> Proposal:
        if not self._diff.strip():
            raise ProposalError("The patch file is empty.")
        return Proposal(
            diff=self._diff,
            rationale=self._rationale,
            source=ChangeSource.PATCH_FILE,
        )


class AnthropicBackend:
    name = "anthropic"

    def __init__(self, settings: Settings, client=None) -> None:
        if not settings.api_key and client is None:
            raise ProposalError(
                "No ANTHROPIC_API_KEY. Copy .env.example to .env and set one, "
                "or pass --patch-file to supply a diff yourself."
            )
        self._settings = settings
        self._client = client or self._make_client(settings)

    @staticmethod
    def _make_client(settings: Settings):
        try:
            import anthropic
        except ImportError as exc:
            raise ProposalError("The `anthropic` package is not installed.") from exc
        return anthropic.Anthropic(api_key=settings.api_key)

    def _request_kwargs(self, prompt: str) -> dict:
        kwargs: dict = {
            "model": self._settings.model,
            "max_tokens": MAX_OUTPUT_TOKENS,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": prompt}],
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": self._settings.effort},
        }
        if self._settings.model in FALLBACK_MODELS:
            kwargs["betas"] = ["server-side-fallback-2026-07-01"]
            kwargs["fallbacks"] = "default"
        return kwargs

    def _send(self, kwargs: dict):
        with self._client.beta.messages.stream(**kwargs) as stream:
            return stream.get_final_message()

    def propose(self, request: ProposalRequest) -> Proposal:
        prompt = build_user_prompt(request)
        kwargs = self._request_kwargs(prompt)
        try:
            message = self._send(kwargs)
        except TypeError:
            kwargs.pop("betas", None)
            kwargs.pop("fallbacks", None)
            message = self._send(kwargs)
        except Exception as exc:  # SDK raises typed errors; surface them as one CLI error
            raise ProposalError(f"{type(exc).__name__}: {exc}") from exc

        if getattr(message, "stop_reason", None) == "refusal":
            details = getattr(message, "stop_details", None)
            category = getattr(details, "category", None) or "unspecified"
            raise ProposalError(
                f"The model declined this request (category: {category}). Rephrase the task, "
                "or supply the diff yourself with --patch-file."
            )

        text = "".join(
            block.text for block in message.content if getattr(block, "type", "") == "text"
        )
        rationale, diff = split_response(text)
        if not diff.strip():
            raise ProposalError(
                "The model returned no diff. Its answer was:\n"
                + clean_text_input(rationale or "(empty)", max_len=2000, field="rationale")
            )
        usage = getattr(message, "usage", None)
        return Proposal(
            diff=diff,
            rationale=rationale,
            source=ChangeSource.MODEL,
            model=getattr(message, "model", self._settings.model),
            input_tokens=getattr(usage, "input_tokens", None),
            output_tokens=getattr(usage, "output_tokens", None),
        )
