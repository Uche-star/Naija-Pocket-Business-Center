# ada_response.py
# Naija Pocket Business Center
# Ada customer-facing intelligence layer
#
# IMPORTANT:
# - Ada is intelligence-first.
# - Selected service is context, not a rigid workflow.
# - No review_prompt.py is imported here.
# - Groq token/request limits are preserved.
# - Document standardization is intelligence-driven.
# - Raw Markdown must never reach the customer-facing pages.
# - Generation: one standardization pass.
# - Review: findings only; incoming standardized document remains canonical.
# - Correction: correction generation, then one standardization pass.
# - Compatibility exports get_ada_model() and is_configured() are required by ada_api.py.

import os
import re
import json
from typing import Any, Dict, List, Optional, Tuple

try:
    from groq import Groq
except Exception:
    Groq = None

try:
    from ada_ai_config import API_KEY, MODEL
except Exception:
    API_KEY = os.getenv("GROQ_API_KEY", "")
    MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

try:
    from billing_manager import BillingManager
except Exception:
    BillingManager = None

try:
    from ada_prompt_manager import AdaPromptManager
except Exception:
    AdaPromptManager = None


# ============================================================
# COMPATIBILITY FUNCTIONS
# ============================================================

def get_ada_model() -> str:
    """
    Public compatibility function required by ada_api.py.
    Returns the configured Ada/Groq model without making a request.
    """
    try:
        model = str(MODEL or "").strip()
        if model:
            return model
    except Exception:
        pass

    return os.getenv(
        "GROQ_MODEL",
        "llama-3.3-70b-versatile",
    ).strip()


def is_configured() -> bool:
    """
    Public compatibility function required by ada_api.py.
    Checks whether the Groq client can be configured with an API key.
    """
    if Groq is None:
        return False

    try:
        api_key = str(API_KEY or "").strip()
    except Exception:
        api_key = os.getenv("GROQ_API_KEY", "").strip()

    if not api_key:
        api_key = os.getenv("GROQ_API_KEY", "").strip()

    return bool(api_key)


# ============================================================
# TOKEN / REQUEST LIMITS
# DO NOT CHANGE THESE VALUES
# ============================================================

MAX_SYSTEM_PROMPT_CHARS = 5000
MAX_HISTORY_MESSAGES = 4
MAX_HISTORY_MESSAGE_CHARS = 900
MAX_USER_MESSAGE_CHARS = 4500
MAX_CONTEXT_CHARS = 1800
MAX_DOCUMENT_PAGES = 1000

GENERATION_REQUEST_CHARS = 8500
GENERATION_OUTPUT_TOKENS = 5000
MAX_GENERATION_PARTS = 20

REVIEW_REQUEST_CHARS = 12000
REVIEW_OUTPUT_TOKENS = 700

CORRECTION_REQUEST_CHARS = 10500
CORRECTION_OUTPUT_TOKENS = 4500

DEFAULT_PAGE_CHARS = 7000

END_OF_DOCUMENT_MARKER = "[END OF DOCUMENT]"
CONTINUE_MARKER = "[CONTINUE]"
CONTINUATION_TAIL_CHARS = 3000


# ============================================================
# BASIC HELPERS
# ============================================================

def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _limit_text(value: Any, limit: int) -> str:
    text = _safe_text(value)
    if len(text) <= limit:
        return text
    return text[:limit].rstrip()


def _clean_model_output(text: Any) -> str:
    """
    Removes transport-level wrappers only.
    It does NOT mechanically strip Markdown from documents.
    Document structure is handled intelligently by Ada.
    """
    text = _safe_text(text)

    if not text:
        return ""

    text = re.sub(
        r"^\s*```(?:text|markdown|md)?\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )

    text = re.sub(
        r"\s*```\s*$",
        "",
        text,
        flags=re.IGNORECASE,
    )

    return text.strip()


def _extract_response_text(response: Any) -> str:
    try:
        choices = getattr(response, "choices", None)
        if not choices:
            return ""

        message = getattr(choices[0], "message", None)
        if message is None:
            return ""

        content = getattr(message, "content", None)

        if isinstance(content, str):
            return content.strip()

        if content is None:
            return ""

        return str(content).strip()

    except Exception:
        return ""


def _normalise_pages(pages: Any) -> List[str]:
    if pages is None:
        return []

    if isinstance(pages, str):
        return [pages.strip()] if pages.strip() else []

    if isinstance(pages, dict):
        value = pages.get("pages")
        if isinstance(value, list):
            pages = value
        else:
            return []

    result: List[str] = []

    try:
        for page in pages:
            if isinstance(page, dict):
                text = (
                    page.get("text")
                    or page.get("content")
                    or page.get("body")
                    or ""
                )
            else:
                text = page

            text = _safe_text(text)

            if text:
                result.append(text)

    except Exception:
        return []

    return result[:MAX_DOCUMENT_PAGES]


def _pages_to_document(pages: List[str]) -> str:
    if not pages:
        return ""

    return "\n\n".join(
        page.strip()
        for page in pages
        if _safe_text(page)
    ).strip()


def _document_to_pages(
    document: str,
    page_chars: int = DEFAULT_PAGE_CHARS,
) -> List[str]:
    """
    Keeps existing explicit page boundaries where possible.
    Long pages are split only when necessary.
    """
    document = _safe_text(document)

    if not document:
        return []

    explicit = re.split(
        r"\n\s*(?:PAGE\s+\d+|---\s*PAGE\s+\d+\s*---)\s*\n",
        document,
        flags=re.IGNORECASE,
    )

    if len(explicit) == 1:
        explicit = [document]

    pages: List[str] = []

    for raw_page in explicit:
        page = raw_page.strip()

        if not page:
            continue

        if len(page) <= page_chars:
            pages.append(page)
            continue

        start = 0

        while start < len(page):
            end = min(start + page_chars, len(page))

            if end < len(page):
                split_at = page.rfind("\n\n", start, end)

                if split_at <= start:
                    split_at = page.rfind("\n", start, end)

                if split_at <= start:
                    split_at = end

                end = split_at

            chunk = page[start:end].strip()

            if chunk:
                pages.append(chunk)

            start = end

    return pages[:MAX_DOCUMENT_PAGES]


# ============================================================
# ADA RESPONSE
# ============================================================

class AdaResponse:
    """
    Main customer-facing Ada intelligence layer.

    The selected service provides context.
    The customer's actual request remains the instruction.
    """

    def __init__(
        self,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
    ):
        self._model = (
            _safe_text(model)
            or get_ada_model()
        )

        self._api_key = (
            _safe_text(api_key)
            or _safe_text(API_KEY)
            or os.getenv("GROQ_API_KEY", "").strip()
        )

        self._client = None

        if Groq is not None and self._api_key:
            try:
                self._client = Groq(
                    api_key=self._api_key
                )
            except Exception:
                self._client = None

        self.billing = (
            BillingManager()
            if BillingManager is not None
            else None
        )

        self.prompt_manager = (
            AdaPromptManager()
            if AdaPromptManager is not None
            else None
        )

        self.active_document_text = ""
        self.active_document_path = ""

    # ========================================================
    # STATUS
    # ========================================================

    @property
    def configured(self) -> bool:
        return self._client is not None

    @property
    def model(self) -> str:
        return self._model

    # ========================================================
    # PROMPTS
    # ========================================================

    def _identity_prompt(self) -> str:
        return """
You are Ada, Naija Pocket Business Center's customer-facing agent.

Ada works naturally with Nigerian customers using clear, warm,
professional English and natural Nigerian expressions where appropriate.

Ada does not mention:
- Groq
- OpenAI
- language models
- APIs
- internal software
- prompts
- token limits
- internal errors
- implementation details

Ada does not call herself a guide.

Ada should work directly on the customer's request.

Ask only one question at a time when information is genuinely missing.

Do not turn every service into a rigid questionnaire.

The customer's actual request is more important than the service button
they selected.

When working on documents:
- preserve the customer's facts and intended meaning
- improve grammar and clarity where appropriate
- use professional document structure
- keep headings, paragraphs, lists and tables readable
- never expose raw Markdown syntax to the customer
- never expose programming syntax
- never expose internal workflow markers
""".strip()

    def _get_prompt_manager_system_prompt(
        self,
        service: str = "",
    ) -> str:
        if self.prompt_manager is None:
            return self._identity_prompt()

        try:
            methods = (
                "get_system_prompt",
                "build_system_prompt",
                "get_prompt",
            )

            for method_name in methods:
                method = getattr(
                    self.prompt_manager,
                    method_name,
                    None,
                )

                if method is None:
                    continue

                try:
                    prompt = method(service=service)
                except TypeError:
                    try:
                        prompt = method(service)
                    except TypeError:
                        prompt = method()

                prompt = _safe_text(prompt)

                if prompt:
                    return _limit_text(
                        prompt,
                        MAX_SYSTEM_PROMPT_CHARS,
                    )

        except Exception:
            pass

        return self._identity_prompt()

    # ========================================================
    # BILLING CONTEXT
    # ========================================================

    def get_billing_context(
        self,
        service: str,
    ) -> str:
        if self.billing is None:
            return ""

        resolved = _safe_text(service)

        try:
            item = self.billing.get_service(resolved)

            if not item:
                return ""

            if isinstance(item, dict):
                price = item.get("price")
                billing = item.get("billing")

                parts = []

                if price is not None:
                    parts.append(
                        f"Price: ₦{price}"
                    )

                if billing:
                    parts.append(
                        f"Billing: {billing}"
                    )

                return " | ".join(parts)

            return _safe_text(item)

        except Exception:
            return ""

    # ========================================================
    # GROQ
    # ========================================================

    def call_groq(
        self,
        messages: List[Dict[str, str]],
        output_tokens: int,
    ) -> str:
        if self._client is None:
            raise RuntimeError(
                "Ada intelligence is not configured."
            )

        response = self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            temperature=0.2,
            max_completion_tokens=output_tokens,
        )

        text = _extract_response_text(response)

        if not text:
            raise RuntimeError(
                "Ada returned an empty response."
            )

        return _clean_model_output(text)

    # ========================================================
    # SERVICE NORMALISATION
    # ========================================================

    def normalize_service(
        self,
        service: Any,
    ) -> str:
        value = _safe_text(service)

        if not value:
            return "general service"

        value = re.sub(
            r"[_\-]+",
            " ",
            value,
        )

        value = re.sub(
            r"\s+",
            " ",
            value,
        )

        return value.strip()

    def find_service_in_message(
        self,
        message: str,
    ) -> str:
        text = _safe_text(message).lower()

        service_terms = [
            "cv",
            "resume",
            "cover letter",
            "assignment",
            "project",
            "typing",
            "printing",
            "translation",
            "grammar",
            "proofreading",
            "business proposal",
            "business plan",
            "letter",
            "document",
            "pdf",
        ]

        for term in service_terms:
            if term in text:
                return term

        return ""

    # ========================================================
    # PRICE RESPONSE
    # ========================================================

    def detect_price_request(
        self,
        message: str,
    ) -> bool:
        text = _safe_text(message).lower()

        price_terms = [
            "price",
            "cost",
            "how much",
            "fee",
            "charge",
            "payment",
            "pay",
            "₦",
            "naira",
        ]

        return any(
            term in text
            for term in price_terms
        )

    def generate_price_response(
        self,
        service: str,
    ) -> str:
        context = self.get_billing_context(service)

        if context:
            return (
                f"Ada, Naija Pocket Business Center's agent, "
                f"will begin working on your request immediately.\n\n"
                f"For {self.normalize_service(service)}, "
                f"the current service details are {context}."
            )

        return (
            "Ada, Naija Pocket Business Center's agent, "
            "will begin working on your request immediately.\n\n"
            "Please tell me what you need done, and I will work from "
            "your actual request."
        )

    # ========================================================
    # DOCUMENT STANDARDIZATION
    # ========================================================

    def _standardization_system_prompt(
        self,
        service: str,
    ) -> str:
        return f"""
You are Ada, Naija Pocket Business Center's professional document
standardization specialist.

Service context:
{self.normalize_service(service)}

Your task is to standardize the supplied document intelligently.

This is NOT a request to rewrite the customer's facts.

Preserve:
- names
- dates
- amounts
- companies
- addresses
- locations
- facts
- intended meaning
- requested information
- legitimate tables and lists

Improve:
- grammar
- spelling
- punctuation
- spacing
- paragraph separation
- heading hierarchy
- professional structure
- readability
- consistency
- presentation

IMPORTANT OUTPUT RULES:

Return ONLY the complete standardized document.

Do NOT return an explanation.

Do NOT use raw Markdown source syntax.

Do NOT use:
- **
- ##
- ###
- Markdown pipe tables
- ``` code fences
- Markdown horizontal rules such as ---
- Markdown link syntax
- raw Markdown bullet syntax

Headings should be represented as ordinary clean text on their own lines.

Lists should be readable ordinary numbered or bulleted content,
without exposing Markdown source syntax.

Tables should be represented as clean readable document tables or
clearly structured rows and columns without Markdown pipe syntax.

Keep sensible blank lines between sections.

Never collapse labels, headings and paragraphs together.

Never add facts that are not supported by the supplied document.

If the supplied document is already professional, preserve its wording
and only improve what is genuinely necessary.
""".strip()

    def intelligently_standardize_page(
        self,
        page: str,
        service: str,
    ) -> str:
        page = _safe_text(page)

        if not page:
            return ""

        system = self._standardization_system_prompt(service)

        user = f"""
Standardize the following document page.

SERVICE:
{self.normalize_service(service)}

DOCUMENT PAGE:
{page}
""".strip()

        messages = [
            {
                "role": "system",
                "content": _limit_text(
                    system,
                    MAX_SYSTEM_PROMPT_CHARS,
                ),
            },
            {
                "role": "user",
                "content": _limit_text(
                    user,
                    GENERATION_REQUEST_CHARS,
                ),
            },
        ]

        return self.call_groq(
            messages,
            GENERATION_OUTPUT_TOKENS,
        )

    def intelligently_standardize_pages(
        self,
        pages: List[str],
        service: str,
    ) -> List[str]:
        normalized = _normalise_pages(pages)

        if not normalized:
            return []

        standardized: List[str] = []

        for page in normalized:
            try:
                result = self.intelligently_standardize_page(
                    page,
                    service,
                )

                result = _clean_model_output(result)

                if result:
                    standardized.append(result)
                else:
                    standardized.append(page)

            except Exception:
                # Preserve the document rather than destroying it if a
                # single standardization call fails.
                standardized.append(page)

        return standardized[:MAX_DOCUMENT_PAGES]

    def intelligently_standardize_document(
        self,
        document: str,
        service: str,
    ) -> str:
        pages = _document_to_pages(document)

        if not pages:
            return ""

        standardized_pages = self.intelligently_standardize_pages(
            pages,
            service,
        )

        return _pages_to_document(
            standardized_pages
        )

    # ========================================================
    # GENERATION
    # ========================================================

    def _generation_system_prompt(
        self,
        service: str,
    ) -> str:
        billing = self.get_billing_context(service)

        billing_text = (
            f"\nService billing context: {billing}"
            if billing
            else ""
        )

        return f"""
{self._identity_prompt()}

You are now working on a document request.

Selected service:
{self.normalize_service(service)}
{billing_text}

Understand the customer's actual request before producing the document.

Create a complete professional document when enough information has
been provided.

Do not invent personal facts.

If information is genuinely required before the document can be
created, ask one clear question instead.

When producing the document, use clean document structure.
Do not expose raw Markdown source syntax.
""".strip()

    def generate_document(
        self,
        service: str,
        user_message: str,
        history: Optional[List[Dict[str, str]]] = None,
        context: str = "",
    ) -> Dict[str, Any]:
        service = self.normalize_service(service)

        user_message = _limit_text(
            user_message,
            MAX_USER_MESSAGE_CHARS,
        )

        context = _limit_text(
            context,
            MAX_CONTEXT_CHARS,
        )

        messages: List[Dict[str, str]] = [
            {
                "role": "system",
                "content": _limit_text(
                    self._generation_system_prompt(service),
                    MAX_SYSTEM_PROMPT_CHARS,
                ),
            }
        ]

        if history:
            safe_history = history[
                -MAX_HISTORY_MESSAGES:
            ]

            for item in safe_history:
                if not isinstance(item, dict):
                    continue

                role = item.get("role")

                if role not in (
                    "user",
                    "assistant",
                ):
                    continue

                content = _limit_text(
                    item.get("content", ""),
                    MAX_HISTORY_MESSAGE_CHARS,
                )

                if content:
                    messages.append(
                        {
                            "role": role,
                            "content": content,
                        }
                    )

        user_parts = []

        if context:
            user_parts.append(
                f"Relevant context:\n{context}"
            )

        user_parts.append(
            f"Customer request:\n{user_message}"
        )

        messages.append(
            {
                "role": "user",
                "content": _limit_text(
                    "\n\n".join(user_parts),
                    GENERATION_REQUEST_CHARS,
                ),
            }
        )

        raw = self.call_groq(
            messages,
            GENERATION_OUTPUT_TOKENS,
        )

        # ONE standardization pass after generation.
        standardized = self.intelligently_standardize_document(
            raw,
            service,
        )

        if not standardized:
            standardized = raw

        self.active_document_text = standardized

        return {
            "success": True,
            "service": service,
            "content": standardized,
            "document": standardized,
            "pages": _document_to_pages(
                standardized
            ),
        }

    # ========================================================
    # REVIEW
    # ========================================================

    def _review_system_prompt(
        self,
        service: str,
    ) -> str:
        return f"""
You are Ada reviewing a professional document for the customer.

Service:
{self.normalize_service(service)}

The document supplied to you has already been standardized.

Do NOT rewrite or return the document.

Return ONLY review findings.

Check:
- missing information
- contradictions
- obvious factual inconsistencies
- grammar or wording issues that still matter
- professional presentation issues
- unclear sections
- obvious calculation inconsistencies
- whether the customer's stated purpose is satisfied

Do not invent problems.

If the document is satisfactory, say that it is ready.

Your response MUST use exactly these markers:

[FINDINGS_START]
your concise findings here
[FINDINGS_END]

Do not place the document between the markers.
""".strip()

    def review_document(
        self,
        service: str,
        pages: Any,
        title: str = "",
        context: str = "",
    ) -> Dict[str, Any]:
        service = self.normalize_service(service)

        normalized_pages = _normalise_pages(pages)

        if not normalized_pages:
            return {
                "success": False,
                "status": "error",
                "findings": "There is no document available to review.",
                "pages": [],
            }

        # IMPORTANT:
        # Do NOT standardize again here.
        # The incoming pages are already the canonical standardized
        # document from generation or correction.
        document = _pages_to_document(
            normalized_pages
        )

        review_payload = f"""
SERVICE:
{service}

TITLE:
{_safe_text(title)}

CONTEXT:
{_limit_text(context, MAX_CONTEXT_CHARS)}

DOCUMENT TO REVIEW:
{document}
""".strip()

        messages = [
            {
                "role": "system",
                "content": _limit_text(
                    self._review_system_prompt(service),
                    MAX_SYSTEM_PROMPT_CHARS,
                ),
            },
            {
                "role": "user",
                "content": _limit_text(
                    review_payload,
                    REVIEW_REQUEST_CHARS,
                ),
            },
        ]

        try:
            raw = self.call_groq(
                messages,
                REVIEW_OUTPUT_TOKENS,
            )

            match = re.search(
                r"FINDINGS_START(.*?)FINDINGS_END",
                raw,
                flags=re.IGNORECASE | re.DOTALL,
            )

            if match:
                findings = match.group(1).strip()
            else:
                findings = raw.strip()

            return {
                "success": True,
                "status": "reviewed",
                "findings": findings,
                "pages": normalized_pages,
                "document": document,
                "title": _safe_text(title),
            }

        except Exception as exc:
            return {
                "success": False,
                "status": "error",
                "findings": (
                    "The document is available, but the review "
                    "could not be completed at this time."
                ),
                "error": str(exc),
                "pages": normalized_pages,
                "document": document,
                "title": _safe_text(title),
            }

    # ========================================================
    # CORRECTION
    # ========================================================

    def _correction_system_prompt(
        self,
        service: str,
    ) -> str:
        return f"""
You are Ada, Naija Pocket Business Center's document correction
specialist.

Service:
{self.normalize_service(service)}

Apply the customer's requested correction to the supplied document.

Rules:
- preserve all correct existing information
- preserve names, dates, amounts and facts unless the customer
  explicitly asks for a change
- make only the requested corrections plus necessary grammar,
  punctuation and structural corrections
- do not invent facts
- return the complete corrected document
- do not explain what you changed
- do not use raw Markdown source syntax
- do not use code fences
- do not use Markdown pipe tables
- do not use raw Markdown heading syntax
""".strip()

    def correct_document(
        self,
        service: str,
        pages: Any,
        correction: str,
        context: str = "",
    ) -> Dict[str, Any]:
        service = self.normalize_service(service)

        normalized_pages = _normalise_pages(pages)

        if not normalized_pages:
            return {
                "success": False,
                "status": "error",
                "message": "There is no document available for correction.",
                "pages": [],
            }

        correction = _limit_text(
            correction,
            MAX_USER_MESSAGE_CHARS,
        )

        document = _pages_to_document(
            normalized_pages
        )

        correction_payload = f"""
SERVICE:
{service}

CONTEXT:
{_limit_text(context, MAX_CONTEXT_CHARS)}

CURRENT STANDARDIZED DOCUMENT:
{document}

CUSTOMER'S CORRECTION:
{correction}

Return the complete corrected document.
""".strip()

        messages = [
            {
                "role": "system",
                "content": _limit_text(
                    self._correction_system_prompt(service),
                    MAX_SYSTEM_PROMPT_CHARS,
                ),
            },
            {
                "role": "user",
                "content": _limit_text(
                    correction_payload,
                    CORRECTION_REQUEST_CHARS,
                ),
            },
        ]

        try:
            corrected = self.call_groq(
                messages,
                CORRECTION_OUTPUT_TOKENS,
            )

            # ONE standardization pass after correction.
            standardized = self.intelligently_standardize_document(
                corrected,
                service,
            )

            if not standardized:
                standardized = corrected

            self.active_document_text = standardized

            return {
                "success": True,
                "status": "corrected",
                "document": standardized,
                "content": standardized,
                "pages": _document_to_pages(
                    standardized
                ),
            }

        except Exception as exc:
            return {
                "success": False,
                "status": "error",
                "message": (
                    "The correction could not be completed at this time."
                ),
                "error": str(exc),
                "pages": normalized_pages,
                "document": document,
            }

    # ========================================================
    # NORMAL CUSTOMER CHAT
    # ========================================================

    def process_message(
        self,
        message: str,
        service: str = "",
        history: Optional[List[Dict[str, str]]] = None,
        context: str = "",
    ) -> Dict[str, Any]:
        message = _safe_text(message)
        service = self.normalize_service(service)

        if not message:
            return {
                "success": True,
                "response": (
                    "Ada, Naija Pocket Business Center's agent, "
                    "will begin working on your request immediately. "
                    "Please tell me what you need done."
                ),
            }

        if self.detect_price_request(message):
            service_from_message = self.find_service_in_message(
                message
            )

            if service_from_message:
                service = self.normalize_service(
                    service_from_message
                )

            if service:
                return {
                    "success": True,
                    "response": self.generate_price_response(
                        service
                    ),
                }

        system = self._get_prompt_manager_system_prompt(
            service
        )

        billing = self.get_billing_context(
            service
        )

        if billing:
            system = (
                f"{system}\n\n"
                f"Current service billing context: {billing}"
            )

        messages: List[Dict[str, str]] = [
            {
                "role": "system",
                "content": _limit_text(
                    system,
                    MAX_SYSTEM_PROMPT_CHARS,
                ),
            }
        ]

        if history:
            for item in history[
                -MAX_HISTORY_MESSAGES:
            ]:
                if not isinstance(item, dict):
                    continue

                role = item.get("role")

                if role not in (
                    "user",
                    "assistant",
                ):
                    continue

                content = _limit_text(
                    item.get("content", ""),
                    MAX_HISTORY_MESSAGE_CHARS,
                )

                if content:
                    messages.append(
                        {
                            "role": role,
                            "content": content,
                        }
                    )

        user_parts = []

        if context:
            user_parts.append(
                f"Context:\n{_limit_text(context, MAX_CONTEXT_CHARS)}"
            )

        if service:
            user_parts.append(
                f"Selected service: {service}"
            )

        user_parts.append(
            f"Customer message:\n{_limit_text(message, MAX_USER_MESSAGE_CHARS)}"
        )

        messages.append(
            {
                "role": "user",
                "content": _limit_text(
                    "\n\n".join(user_parts),
                    GENERATION_REQUEST_CHARS,
                ),
            }
        )

        try:
            response = self.call_groq(
                messages,
                GENERATION_OUTPUT_TOKENS,
            )

            return {
                "success": True,
                "response": response,
                "content": response,
            }

        except Exception as exc:
            return {
                "success": False,
                "response": (
                    "Ada is temporarily unable to complete that "
                    "request. Please try again shortly."
                ),
                "error": str(exc),
            }

    # ========================================================
    # ALIASES / EVENT HELPERS
    # ========================================================

    def respond(
        self,
        message: str,
        service: str = "",
        history: Optional[List[Dict[str, str]]] = None,
        context: str = "",
    ) -> Dict[str, Any]:
        return self.process_message(
            message=message,
            service=service,
            history=history,
            context=context,
        )

    def handle_message(
        self,
        message: str,
        service: str = "",
        history: Optional[List[Dict[str, str]]] = None,
        context: str = "",
    ) -> Dict[str, Any]:
        return self.process_message(
            message=message,
            service=service,
            history=history,
            context=context,
        )

    def set_document_context(
        self,
        text: str = "",
        path: str = "",
    ) -> None:
        self.active_document_text = _safe_text(text)
        self.active_document_path = _safe_text(path)

    def clear_document_context(self) -> None:
        self.active_document_text = ""
        self.active_document_path = ""

    def health(self) -> Dict[str, Any]:
        return {
            "success": True,
            "configured": self.configured,
            "model": self._model,
            "intelligence": "AdaResponse",
            "architecture": "intelligence-first",
        }


# ============================================================
# OPTIONAL MODULE-LEVEL CONVENIENCE
# ============================================================

_default_ada: Optional[AdaResponse] = None


def get_ada_response() -> AdaResponse:
    global _default_ada

    if _default_ada is None:
        _default_ada = AdaResponse()

    return _default_ada


def process_message(
    message: str,
    service: str = "",
    history: Optional[List[Dict[str, str]]] = None,
    context: str = "",
) -> Dict[str, Any]:
    return get_ada_response().process_message(
        message=message,
        service=service,
        history=history,
        context=context,
    )


def generate_document(
    service: str,
    user_message: str,
    history: Optional[List[Dict[str, str]]] = None,
    context: str = "",
) -> Dict[str, Any]:
    return get_ada_response().generate_document(
        service=service,
        user_message=user_message,
        history=history,
        context=context,
    )


def review_document(
    service: str,
    pages: Any,
    title: str = "",
    context: str = "",
) -> Dict[str, Any]:
    return get_ada_response().review_document(
        service=service,
        pages=pages,
        title=title,
        context=context,
    )


def correct_document(
    service: str,
    pages: Any,
    correction: str,
    context: str = "",
) -> Dict[str, Any]:
    return get_ada_response().correct_document(
        service=service,
        pages=pages,
        correction=correction,
        context=context,
    )
