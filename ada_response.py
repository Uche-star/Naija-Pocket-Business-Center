"""
Naija Pocket Business Center
ADA RESPONSE ENGINE

INTELLIGENCE-FIRST DOCUMENT ENGINE
==================================

Ada is responsible for intelligence:

    - understanding the customer's request
    - understanding the selected service
    - determining what information is required
    - generating the requested work
    - reviewing the completed work
    - applying customer corrections

The application is responsible for document state:

    - storing the current document
    - storing document versions
    - maintaining page order
    - review state
    - approval
    - payment
    - delivery

IMPORTANT ARCHITECTURE RULE
---------------------------

PAGE NORMALIZATION IS NOT INTELLIGENCE.

normalize_document_pages()
document_to_pages()
assemble_document()

are purely document-structure operations.

They MUST NOT:

    - call Groq
    - call Ada
    - ask the model a question
    - determine whether a document is complete
    - replace document content
    - block intelligence
    - make service decisions

Intelligence remains inside the intelligence methods.

DOCUMENT RULE
-------------

A generated document is ONE COMPLETE DOCUMENT.

Internal generation requests are not separate documents.

The flow is:

    CUSTOMER REQUEST
          |
          v
    ADA INTELLIGENCE
          |
          v
    GENERATION PARTS
          |
          v
    COMPLETE DOCUMENT
          |
          v
    PAGE NORMALIZATION
          |
          v
    REVIEW

A generation part is never exposed as the final document.

REVIEW RULE
-----------

Review receives the actual current document.

Review findings are separate from document content.

A review response must never replace a page.

CORRECTION RULE
---------------

Corrections operate on the CURRENT complete document.

A correction must return a new complete document.

"""

from __future__ import annotations

import os
import re
import traceback
from typing import Any, Callable

try:
    from groq import Groq
except ImportError:
    Groq = None

from ada_prompt_manager import AdaPromptManager
from billing_manager import BillingManager


# ============================================================
# CONFIGURATION
# ============================================================

DEFAULT_MODEL = "openai/gpt-oss-20b"

MODEL = (
    os.getenv("GROQ_MODEL")
    or DEFAULT_MODEL
).strip()

API_KEY = (
    os.getenv("GROQ_API_KEY")
    or os.getenv("GROQ_APIKEY")
    or ""
).strip()

EXPOSE_ERRORS_TO_CLIENT = (
    os.getenv(
        "ADA_EXPOSE_ERRORS",
        "false",
    )
    .strip()
    .lower()
    in {
        "1",
        "true",
        "yes",
        "on",
    }
)


# ============================================================
# LIMITS
# ============================================================

MAX_SYSTEM_PROMPT_CHARS = 14000

MAX_HISTORY_MESSAGES = 12
MAX_HISTORY_MESSAGE_CHARS = 3000

MAX_USER_MESSAGE_CHARS = 10000
MAX_CONTEXT_CHARS = 9000

MAX_DOCUMENT_PAGES = 1000

# These are intelligence-request limits.
# They are NOT document-size limits.
GENERATION_REQUEST_CHARS = 12000
GENERATION_OUTPUT_TOKENS = 5000

COMPLETION_CHECK_OUTPUT_TOKENS = 30

MAX_GENERATION_PARTS = 40

REVIEW_REQUEST_CHARS = 12000
REVIEW_OUTPUT_TOKENS = 1500

CORRECTION_REQUEST_CHARS = 14000
CORRECTION_OUTPUT_TOKENS = 5000

DEFAULT_PAGE_CHARS = 7000


# ============================================================
# EVENTS
# ============================================================

REVIEW_EVENTS = {
    "review",
    "review_requested",
    "review_called",
    "open_review",
    "review_page",
    "review_document",
    "send_for_review",
    "send_review",
}

CORRECTION_EVENTS = {
    "review_correction",
    "document_correction",
    "revise_work",
    "correction",
}


# ============================================================
# ERROR
# ============================================================

class AdaResponseError(Exception):

    def __init__(
        self,
        message: str,
        *,
        stage: str,
        category: str = "APPLICATION",
        status_code: int | None = None,
        original: Exception | None = None,
    ):
        super().__init__(message)

        self.stage = stage
        self.category = category
        self.status_code = status_code
        self.original = original


# ============================================================
# GROQ CLIENT
# ============================================================

_client = None


def get_client():

    global _client

    if _client is not None:
        return _client

    if Groq is None:
        raise AdaResponseError(
            "The groq package is not installed.",
            stage="CLIENT_INITIALIZATION",
            category="CONFIGURATION",
        )

    if not API_KEY:
        raise AdaResponseError(
            "GROQ_API_KEY is missing.",
            stage="CLIENT_INITIALIZATION",
            category="CONFIGURATION",
        )

    try:

        _client = Groq(
            api_key=API_KEY
        )

        return _client

    except Exception as error:

        raise AdaResponseError(
            "Groq client initialization failed.",
            stage="CLIENT_INITIALIZATION",
            category="GROQ_CLIENT",
            original=error,
        ) from error


def get_ada_model() -> str:
    return MODEL


def is_configured() -> bool:

    return (
        Groq is not None
        and bool(API_KEY)
    )


# ============================================================
# TEXT HELPERS
# ============================================================

def safe_text(value: Any) -> str:

    if value is None:
        return ""

    if isinstance(value, str):
        return value.strip()

    return str(value).strip()


def compact_text(
    value: Any,
    maximum: int,
) -> str:

    text = safe_text(value)

    if not text:
        return ""

    if len(text) <= maximum:
        return text

    if maximum <= 100:
        return text[:maximum]

    marker = (
        "\n\n"
        "[INTERNAL CONTEXT COMPACTED]\n\n"
    )

    available = maximum - len(marker)

    if available <= 0:
        return text[:maximum]

    first = int(
        available * 0.60
    )

    last = available - first

    return (
        text[:first]
        + marker
        + text[-last:]
    )


def split_for_intelligence(
    text: str,
    maximum: int,
) -> list[str]:
    """
    Internal text splitting.

    This is a utility only.

    Returned pieces are NEVER treated as separate documents.
    """

    text = safe_text(text)

    if not text:
        return []

    if len(text) <= maximum:
        return [text]

    parts = []

    start = 0
    length = len(text)

    while start < length:

        remaining = length - start

        if remaining <= maximum:

            final_part = text[
                start:
            ].strip()

            if final_part:
                parts.append(
                    final_part
                )

            break

        end = start + maximum

        window = text[
            start:end
        ]

        positions = [
            window.rfind("\n\n"),
            window.rfind("\n"),
            window.rfind(". "),
            window.rfind("? "),
            window.rfind("! "),
            window.rfind("; "),
            window.rfind(" "),
        ]

        usable = [
            position
            for position in positions
            if position >= int(
                maximum * 0.55
            )
        ]

        if usable:

            boundary = max(
                usable
            )

        else:

            boundary = maximum

        part = text[
            start:
            start + boundary
        ].strip()

        if part:
            parts.append(
                part
            )

        next_start = (
            start + boundary
        )

        if next_start <= start:
            next_start = end

        start = next_start

    return parts


def last_nonempty_line(
    text: str,
) -> str:

    lines = [
        line.strip()
        for line in safe_text(
            text
        ).splitlines()
        if line.strip()
    ]

    if not lines:
        return ""

    return lines[-1]


def looks_truncated(
    text: str,
) -> bool:
    """
    Conservative truncation detector.

    It does not decide whether a document is correct.

    It only detects obvious signs that a model response ended
    abruptly.
    """

    text = safe_text(text)

    if not text:
        return True

    line = last_nonempty_line(
        text
    )

    if not line:
        return True

    # Obvious unfinished punctuation.
    if line.endswith(
        (
            ",",
            ":",
            ";",
            "—",
            "-",
            "(",
            "[",
            "{",
            "/",
        )
    ):
        return True

    # Obvious unfinished list markers.
    if re.match(
        r"^(?:[-*•]|\d+[.)])\s*$",
        line,
    ):
        return True

    # Obvious sentence fragment.
    words = re.findall(
        r"\b[\w'-]+\b",
        line,
    )

    if len(words) == 1 and len(text) > 300:
        return True

    # Markdown/table structures that are visibly unfinished.
    if line.endswith("|"):
        return True

    return False


def clean_generation_response(
    text: str,
) -> str:

    text = safe_text(
        text
    )

    if not text:
        return ""

    # Remove accidental continuation labels.
    text = re.sub(
        r"^\s*(?:PART|CONTINUATION)\s+\d+\s*:?\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )

    # Remove accidental model completion markers.
    text = re.sub(
        r"\n\s*\[?(?:END OF DOCUMENT|DOCUMENT COMPLETE)\]?\s*$",
        "",
        text,
        flags=re.IGNORECASE,
    )

    return text.strip()


# ============================================================
# ERROR CLASSIFICATION
# ============================================================

def classify_error(
    error: Exception,
) -> str:

    status = getattr(
        error,
        "status_code",
        None,
    )

    message = safe_text(
        error
    ).lower()

    if status == 429:
        return "GROQ_RATE_LIMIT"

    if status == 401:
        return "GROQ_AUTHENTICATION"

    if status == 403:
        return "GROQ_PERMISSION"

    if status == 404:
        return "GROQ_NOT_FOUND"

    if status == 400:
        return "GROQ_BAD_REQUEST"

    if status == 413:
        return "GROQ_REQUEST_TOO_LARGE"

    if status and status >= 500:
        return "GROQ_SERVER_ERROR"

    if "timeout" in message:
        return "NETWORK"

    if "connection" in message:
        return "NETWORK"

    return "GROQ_REQUEST_ERROR"


def log_error(
    title: str,
    error: Exception,
    *,
    stage: str,
):

    print()
    print("=" * 78)
    print("ADA ERROR:", title)
    print("=" * 78)
    print("Stage:", stage)
    print(
        "Category:",
        classify_error(error),
    )
    print(
        "Model:",
        MODEL,
    )
    print(
        "API key configured:",
        bool(API_KEY),
    )
    print(
        "Error:",
        compact_text(
            error,
            1500,
        ),
    )

    traceback.print_exc()

    print("=" * 78)
    print()


def client_error_message(
    error: Exception,
) -> str:

    if not EXPOSE_ERRORS_TO_CLIENT:

        return (
            "I could not process your request right now. "
            "Please try again."
        )

    return (
        "Technical error detected.\n\n"
        f"Category: {classify_error(error)}\n"
        f"Error: {compact_text(error, 1200)}"
    )


# ============================================================
# ADA RESPONSE
# ============================================================

class AdaResponse:

    def __init__(
        self,
        service: str | None = None,
    ):

        self.service = safe_text(
            service
        ) or None

        self.prompt_manager = (
            AdaPromptManager()
        )

        self.billing = (
            BillingManager()
        )

        self.history: list[
            dict[str, str]
        ] = []

    # ========================================================
    # SERVICE
    # ========================================================

    def set_service(
        self,
        service: str | None,
    ):

        service = safe_text(
            service
        )

        if service:
            self.service = service

    def normalize_service(
        self,
        service: str | None,
    ) -> str | None:
        """
        Service normalization only.

        This does NOT call intelligence.
        """

        service = safe_text(
            service
        )

        if not service:
            return self.service

        try:

            normalized = (
                self.billing
                .normalize_service(
                    service
                )
            )

            return (
                safe_text(
                    normalized
                )
                or service
            )

        except Exception as error:

            log_error(
                "SERVICE NORMALIZATION FAILED",
                error,
                stage="SERVICE_NORMALIZATION",
            )

            return service

    # ========================================================
    # BILLING
    # ========================================================

    def get_billing_context(
        self,
        service: str | None,
    ) -> str:

        service = self.normalize_service(
            service
        )

        if not service:
            return ""

        try:

            item = (
                self.billing
                .get_service(
                    service
                )
            )

        except Exception as error:

            log_error(
                "BILLING LOOKUP FAILED",
                error,
                stage="BILLING_LOOKUP",
            )

            return (
                "OFFICIAL BILLING FACTS\n"
                "Billing information unavailable.\n"
                "Do not invent pricing."
            )

        if not item:

            return (
                "OFFICIAL BILLING FACTS\n"
                "No billing record found.\n"
                "Do not invent pricing."
            )

        price = item.get(
            "price",
            0,
        )

        billing_type = item.get(
            "billing"
        )

        if billing_type == "fixed":

            pricing = (
                f"Official price: ₦{price:,}\n"
                "Billing type: fixed"
            )

        elif billing_type == "per_page":

            pricing = (
                f"Official price: ₦{price:,} per page\n"
                "Billing type: per_page"
            )

        elif billing_type == "quotation":

            pricing = (
                "Official price: quotation required\n"
                "Billing type: quotation"
            )

        else:

            pricing = (
                f"Official price: ₦{price:,}\n"
                f"Billing type: {billing_type}"
            )

        return (
            "OFFICIAL BILLING FACTS\n"
            f"Service: {service}\n"
            f"{pricing}\n"
            "BillingManager is authoritative."
        )

    # ========================================================
    # INTELLIGENCE RULES
    # ========================================================

    def intelligence_rules(self) -> str:

        return """
You are Ada, the intelligent customer-facing assistant
of Naija Pocket Business Center.

You understand the customer's meaning rather than relying on
keyword matching.

The selected service gives you context. It does not replace
your reasoning.

SERVICE INTELLIGENCE
--------------------

Determine what information is genuinely relevant to the
selected service and the customer's actual request.

Do NOT impose a universal page-count requirement.

Different services require different information.

Examples:

- A seminar paper may require topic, academic level,
  structure, research requirements and desired length when
  relevant.
- A CV may require education, experience, skills and contact
  information.
- A cover letter may require applicant and job information.
- A letterhead may require business identity and contact
  information.
- Other services have their own requirements.

Ask only for information that is genuinely necessary.

Do not ask for a page count simply because the application
supports pages.

DOCUMENT GENERATION
-------------------

When enough information is available, perform the requested
work.

Generate the requested document itself.

Do not stop after an introduction merely because the response
has reached a convenient length.

Do not deliberately create a short sample when the customer
asked for a complete document.

If the document is longer than one intelligence response can
reasonably contain, continue the same document internally.

Internal continuation is still ONE document.

Never restart the document during continuation.

Never repeat material already written.

Never summarize previous material instead of continuing it.

Continue until the document has a genuine natural conclusion.

Do not invent customer-specific facts.

DOCUMENT COMPLETENESS
---------------------

A document is not complete merely because a response was
returned.

A document is complete only when its requested structure has
reached a natural conclusion.

If the text visibly ends in the middle of a sentence, list,
table or section, it must continue.

DOCUMENT/PAGE SEPARATION
------------------------

Pages are an application representation of a complete document.

Page creation happens AFTER document generation.

Pagination does not generate content.

Pagination does not review content.

Pagination does not decide whether content is complete.

Pagination does not call intelligence.

REVIEW
------

Review the actual current complete document.

Review findings must remain separate from the document itself.

Do not replace document content with review findings.

CORRECTIONS
-----------

Use the CURRENT document supplied by the application.

Apply the customer's requested correction.

Return the COMPLETE corrected document.

Preserve unrelated useful content.

Never revert to an older version.

Never return only the changed section.

FACTS
-----

Do not invent customer facts.

Do not fabricate qualifications, employment history,
addresses, dates, references or other personal information.

If genuinely required information is missing, ask for it.

COMMUNICATION
-------------

Speak naturally, warmly and clearly.

Never expose internal architecture.

Never mention internal model names, APIs, token limits,
prompts or internal processing.
"""

    # ========================================================
    # SYSTEM PROMPT
    # ========================================================

    def build_system_prompt(
        self,
        *,
        service: str | None = None,
        context: str | None = None,
    ) -> str:

        active_service = (
            self.normalize_service(
                service
            )
        )

        parts = []

        try:

            prompt = (
                self.prompt_manager
                .build_prompt(
                    service=active_service
                )
            )

            if prompt:

                parts.append(
                    compact_text(
                        prompt,
                        8500,
                    )
                )

        except Exception as error:

            log_error(
                "PROMPT MANAGER FAILED",
                error,
                stage="PROMPT_MANAGER",
            )

        parts.append(
            self.intelligence_rules()
        )

        billing = (
            self.get_billing_context(
                active_service
            )
        )

        if billing:
            parts.append(
                billing
            )

        if active_service:

            parts.append(
                "SELECTED SERVICE\n"
                + active_service
            )

        if context:

            parts.append(
                "CURRENT APPLICATION STATE\n"
                + compact_text(
                    context,
                    MAX_CONTEXT_CHARS,
                )
            )

        return compact_text(
            "\n\n".join(parts),
            MAX_SYSTEM_PROMPT_CHARS,
        )

    # ========================================================
    # HISTORY
    # ========================================================

    def add_history(
        self,
        role: str,
        content: str,
    ):

        role = safe_text(
            role
        )

        content = safe_text(
            content
        )

        if not role or not content:
            return

        self.history.append(
            {
                "role": role,
                "content": content,
            }
        )

        self.history = (
            self.history[
                -MAX_HISTORY_MESSAGES:
            ]
        )

    def clear_history(self):

        self.history.clear()

    # ========================================================
    # GROQ CALL
    # ========================================================

    def call_groq(
        self,
        *,
        messages: list[dict[str, str]],
        output_tokens: int,
        stage: str,
        event: str | None = None,
    ) -> str:

        client = get_client()

        print()
        print("=" * 78)
        print("ADA INTELLIGENCE")
        print("=" * 78)
        print(
            "Stage:",
            stage,
        )
        print(
            "Model:",
            MODEL,
        )
        print(
            "Messages:",
            len(messages),
        )
        print(
            "Output tokens:",
            output_tokens,
        )

        if event:
            print(
                "Event:",
                event,
            )

        print("=" * 78)

        try:

            response = (
                client
                .chat
                .completions
                .create(
                    model=MODEL,
                    messages=messages,
                    temperature=0.2,
                    max_completion_tokens=(
                        output_tokens
                    ),
                )
            )

        except Exception as error:

            log_error(
                "INTELLIGENCE REQUEST FAILED",
                error,
                stage=stage,
            )

            raise AdaResponseError(
                str(error),
                stage=stage,
                category=classify_error(
                    error
                ),
                status_code=getattr(
                    error,
                    "status_code",
                    None,
                ),
                original=error,
            ) from error

        if not response:

            raise AdaResponseError(
                "No intelligence response returned.",
                stage=stage,
                category="EMPTY_RESPONSE",
            )

        if not response.choices:

            raise AdaResponseError(
                "No intelligence choice returned.",
                stage=stage,
                category="EMPTY_RESPONSE",
            )

        content = safe_text(
            response
            .choices[0]
            .message
            .content
        )

        if not content:

            raise AdaResponseError(
                "Intelligence returned empty content.",
                stage=stage,
                category="EMPTY_RESPONSE",
            )

        print(
            "Ada intelligence response received."
        )

        return content

    # ========================================================
    # GENERATION PROMPT
    # ========================================================

    def build_generation_prompt(
        self,
        *,
        service: str,
        customer_request: str,
        supplied_material: str = "",
        previous_document: str = "",
        continuation: bool = False,
    ) -> str:

        if continuation:

            instruction = """
CONTINUE THE SAME DOCUMENT.

The existing document below is the document already written.

Continue from its exact logical endpoint.

Rules:

1. Do NOT restart the document.
2. Do NOT repeat previous sections.
3. Do NOT summarize the previous document.
4. Do NOT create a new document.
5. Continue with the next missing section or content.
6. Preserve the established structure and writing style.
7. Continue until the requested work can naturally conclude.
8. Return ONLY new document content.
"""

        else:

            instruction = """
CREATE THE REQUESTED DOCUMENT.

Produce the actual requested work.

Do not return an outline describing what you would write.

Do not stop after the introduction.

Do not deliberately provide a sample when the customer
requested a complete document.

Follow the natural structure required by the service and
customer request.
"""

        previous = safe_text(
            previous_document
        )

        if previous:

            # Preserve the beginning and end of the current
            # document so continuation has structural context.
            previous_context = compact_text(
                previous,
                10000,
            )

        else:

            previous_context = (
                "(No previous document content. "
                "This is the first generation request.)"
            )

        return (
            "DOCUMENT GENERATION\n\n"

            "SERVICE:\n"
            f"{service}\n\n"

            "CUSTOMER REQUEST:\n"
            f"{compact_text(customer_request, 7000)}\n\n"

            "SUPPLIED MATERIAL:\n"
            f"{compact_text(supplied_material, 7000)}\n\n"

            "CURRENT DOCUMENT ALREADY WRITTEN:\n"
            f"{previous_context}\n\n"

            + instruction
            +

            "\n\n"
            "IMPORTANT:\n"
            "Do not invent customer-specific facts."
        )

    # ========================================================
    # COMPLETION CHECK
    # ========================================================

    def document_completion_check(
        self,
        *,
        service: str,
        customer_request: str,
        document: str,
        system_prompt: str,
    ) -> str:
        """
        Intelligence-only completion decision.

        This method is deliberately separate from pagination.
        """

        document = safe_text(
            document
        )

        prompt = (
            "DOCUMENT COMPLETION CHECK\n\n"

            "You are checking whether the CURRENT document "
            "has genuinely reached a natural conclusion.\n\n"

            f"SERVICE:\n{service}\n\n"

            "CUSTOMER REQUEST:\n"
            f"{compact_text(customer_request, 6000)}\n\n"

            "CURRENT DOCUMENT:\n"
            f"{compact_text(document, 11000)}\n\n"

            "Return EXACTLY one of these words:\n\n"
            "COMPLETE\n"
            "CONTINUE\n\n"

            "Return CONTINUE if any of the following is true:\n"
            "- the document ends mid-sentence\n"
            "- a section is obviously unfinished\n"
            "- a numbered structure is unfinished\n"
            "- a requested component is missing\n"
            "- the document promises later sections that are absent\n"
            "- the customer requested substantial work that has "
            "not yet been completed\n"
            "- the ending is clearly abrupt or incomplete\n\n"

            "Return COMPLETE only when the document itself has "
            "reached a natural conclusion."
        )

        result = self.call_groq(
            messages=[
                {
                    "role":
                        "system",
                    "content":
                        system_prompt,
                },
                {
                    "role":
                        "user",
                    "content":
                        prompt,
                },
            ],
            output_tokens=(
                COMPLETION_CHECK_OUTPUT_TOKENS
            ),
            stage="DOCUMENT_COMPLETION_CHECK",
        )

        result = safe_text(
            result
        ).upper()

        # Exact decision only.
        if result.startswith(
            "CONTINUE"
        ):
            return "CONTINUE"

        if result.startswith(
            "COMPLETE"
        ):
            return "COMPLETE"

        # Conservative fallback.
        return "CONTINUE"

    # ========================================================
    # COMPLETE DOCUMENT GENERATION
    # ========================================================

    def generate_document(
        self,
        *,
        service: str | None,
        customer_request: str,
        supplied_material: str = "",
        context: str | None = None,
        progress_callback: Callable[
            [dict[str, Any]],
            None
        ] | None = None,
    ) -> dict[str, Any]:
        """
        Generate one complete document.

        Multiple intelligence calls may happen internally.

        They are assembled into ONE document before pagination.
        """

        active_service = (
            self.normalize_service(
                service
            )
            or safe_text(service)
            or self.service
            or "General Business Center Service"
        )

        customer_request = safe_text(
            customer_request
        )

        supplied_material = safe_text(
            supplied_material
        )

        if not customer_request:

            raise AdaResponseError(
                "No customer request was supplied.",
                stage="GENERATION_INTAKE",
                category="EMPTY_REQUEST",
            )

        system_prompt = (
            self.build_system_prompt(
                service=active_service,
                context=context,
            )
        )

        document_parts: list[str] = []

        for part_number in range(
            1,
            MAX_GENERATION_PARTS + 1,
        ):

            current_document = (
                "\n\n".join(
                    document_parts
                ).strip()
            )

            prompt = (
                self.build_generation_prompt(
                    service=active_service,
                    customer_request=customer_request,
                    supplied_material=supplied_material,
                    previous_document=current_document,
                    continuation=bool(
                        document_parts
                    ),
                )
            )

            generated = self.call_groq(
                messages=[
                    {
                        "role":
                            "system",
                        "content":
                            system_prompt,
                    },
                    {
                        "role":
                            "user",
                        "content":
                            compact_text(
                                prompt,
                                GENERATION_REQUEST_CHARS,
                            ),
                    },
                ],
                output_tokens=(
                    GENERATION_OUTPUT_TOKENS
                ),
                stage=(
                    "DOCUMENT_GENERATION"
                ),
                event=(
                    "continuation"
                    if document_parts
                    else "initial_generation"
                ),
            )

            generated = clean_generation_response(
                generated
            )

            if not generated:

                raise AdaResponseError(
                    "Generation returned empty content.",
                    stage="DOCUMENT_GENERATION",
                    category="EMPTY_DOCUMENT_PART",
                )

            document_parts.append(
                generated
            )

            current_document = (
                "\n\n".join(
                    document_parts
                ).strip()
            )

            if progress_callback:

                progress_callback(
                    {
                        "type":
                            "generation_progress",

                        "part":
                            part_number,

                        "status":
                            "writing",

                        "content_length":
                            len(
                                current_document
                            ),
                    }
                )

            # ------------------------------------------------
            # An obviously truncated response must continue.
            # Do not ask completion intelligence to approve it.
            # ------------------------------------------------

            if looks_truncated(
                generated
            ):

                print(
                    "Generation response appears "
                    "truncated; continuing."
                )

                continue

            # ------------------------------------------------
            # Ask intelligence whether the COMPLETE assembled
            # document is actually finished.
            # ------------------------------------------------

            decision = (
                self.document_completion_check(
                    service=active_service,
                    customer_request=customer_request,
                    document=current_document,
                    system_prompt=system_prompt,
                )
            )

            print(
                "Document completion decision:",
                decision,
            )

            if decision == "COMPLETE":

                break

        else:

            raise AdaResponseError(
                (
                    "Document generation exceeded the "
                    "maximum continuation limit before "
                    "reaching a safe natural conclusion."
                ),
                stage="DOCUMENT_GENERATION",
                category="GENERATION_INCOMPLETE",
            )

        document_text = (
            "\n\n".join(
                document_parts
            ).strip()
        )

        if not document_text:

            raise AdaResponseError(
                "No complete document was generated.",
                stage="DOCUMENT_GENERATION",
                category="EMPTY_DOCUMENT",
            )

        # ----------------------------------------------------
        # IMPORTANT:
        #
        # ONLY NOW does pagination happen.
        #
        # Pagination does not call intelligence.
        # ----------------------------------------------------

        pages = self.document_to_pages(
            document_text
        )

        if not pages:

            raise AdaResponseError(
                "The completed document could not be paginated.",
                stage="DOCUMENT_PAGINATION",
                category="EMPTY_PAGES",
            )

        result = {
            "type":
                "document_completed",

            "status":
                "completed",

            "service":
                active_service,

            "document_text":
                document_text,

            "pages":
                pages,

            "total_pages":
                len(pages),
        }

        if progress_callback:

            progress_callback(
                result
            )

        return result

    # ========================================================
    # DOCUMENT → PAGES
    # ========================================================

    @staticmethod
    def document_to_pages(
        document_text: str,
    ) -> list[dict[str, Any]]:
        """
        PURE DOCUMENT PAGINATION.

        IMPORTANT:

        This function contains NO intelligence.

        It does not call Groq.

        It does not call Ada.

        It does not decide whether a document is complete.

        It only converts an already-complete document into
        application page objects.
        """

        document_text = safe_text(
            document_text
        )

        if not document_text:
            return []

        # ----------------------------------------------------
        # Explicit PAGE markers.
        # ----------------------------------------------------

        pattern = re.compile(
            r"(?:^|\n)"
            r"(?:={2,}\s*)?"
            r"PAGE\s+(\d+)"
            r"(?:\s*={2,})?"
            r"\s*(?:\n|$)",
            re.IGNORECASE,
        )

        matches = list(
            pattern.finditer(
                document_text
            )
        )

        if matches:

            pages = []

            for index, match in enumerate(
                matches
            ):

                start = match.end()

                if (
                    index + 1
                    < len(matches)
                ):

                    end = matches[
                        index + 1
                    ].start()

                else:

                    end = len(
                        document_text
                    )

                content = document_text[
                    start:end
                ].strip()

                if content:

                    try:

                        page_number = int(
                            match.group(1)
                        )

                    except Exception:

                        page_number = (
                            index + 1
                        )

                    pages.append(
                        {
                            "page_number":
                                page_number,

                            "content":
                                content,

                            "status":
                                "ready",
                        }
                    )

            if pages:

                return pages

        # ----------------------------------------------------
        # No explicit page markers.
        #
        # Split only for UI/document representation.
        # ----------------------------------------------------

        parts = split_for_intelligence(
            document_text,
            DEFAULT_PAGE_CHARS,
        )

        pages = []

        for index, part in enumerate(
            parts,
            start=1,
        ):

            if not part:
                continue

            pages.append(
                {
                    "page_number":
                        index,

                    "content":
                        part,

                    "status":
                        "ready",
                }
            )

        return pages[:MAX_DOCUMENT_PAGES]

    # ========================================================
    # NORMALIZE EXISTING PAGES
    # ========================================================

    @staticmethod
    def normalize_document_pages(
        pages: Any,
    ) -> list[dict[str, Any]]:
        """
        PURE PAGE NORMALIZATION.

        THIS FUNCTION DOES NOT INVOKE INTELLIGENCE.

        It only converts existing application page data into a
        consistent structure.

        It cannot block Ada intelligence because it never calls
        the intelligence engine.
        """

        if pages is None:
            return []

        if isinstance(
            pages,
            str,
        ):

            return AdaResponse.document_to_pages(
                pages
            )

        if not isinstance(
            pages,
            list,
        ):

            pages = [pages]

        result = []

        for index, item in enumerate(
            pages,
            start=1,
        ):

            if isinstance(
                item,
                str,
            ):

                content = safe_text(
                    item
                )

                if content:

                    result.append(
                        {
                            "page_number":
                                index,

                            "content":
                                content,

                            "status":
                                "ready",
                        }
                    )

                continue

            if isinstance(
                item,
                dict,
            ):

                content = safe_text(
                    item.get("content")
                    or item.get("text")
                    or item.get("page_content")
                    or item.get("document_text")
                )

                if not content:
                    continue

                try:

                    page_number = int(
                        item.get(
                            "page_number",
                            index,
                        )
                    )

                except Exception:

                    page_number = index

                result.append(
                    {
                        "page_number":
                            page_number,

                        "content":
                            content,

                        "status":
                            item.get(
                                "status",
                                "ready",
                            ),
                    }
                )

        result.sort(
            key=lambda item: int(
                item[
                    "page_number"
                ]
            )
        )

        # ----------------------------------------------------
        # Re-number only when page numbers are unusable.
        # Content is NEVER changed.
        # ----------------------------------------------------

        if result:

            seen = set()

            duplicate_numbers = False

            for item in result:

                number = item[
                    "page_number"
                ]

                if number in seen:

                    duplicate_numbers = True
                    break

                seen.add(
                    number
                )

            if duplicate_numbers:

                for index, item in enumerate(
                    result,
                    start=1,
                ):

                    item[
                        "page_number"
                    ] = index

        return result[:MAX_DOCUMENT_PAGES]

    # ========================================================
    # REVIEW
    # ========================================================

    def review_document_pages(
        self,
        *,
        pages: list[Any],
        service: str | None = None,
        context: str | None = None,
        customer_request: str = "",
        event: str = "review",
        progress_callback: Callable[
            [dict[str, Any]],
            None
        ] | None = None,
    ) -> dict[str, Any]:
        """
        Review the CURRENT complete document.

        Original page content is preserved.

        Review findings are separate.
        """

        normalized = (
            self.normalize_document_pages(
                pages
            )
        )

        if not normalized:

            raise AdaResponseError(
                "No complete document was supplied for review.",
                stage="REVIEW_INTAKE",
                category="EMPTY_DOCUMENT",
            )

        total_pages = len(
            normalized
        )

        complete_document = (
            self.assemble_document(
                normalized
            )
        )

        if not complete_document:

            raise AdaResponseError(
                "The supplied pages contain no document content.",
                stage="REVIEW_INTAKE",
                category="EMPTY_DOCUMENT",
            )

        system_prompt = (
            self.build_system_prompt(
                service=service,
                context=context,
            )
        )

        page_results = []

        for position, page in enumerate(
            normalized,
            start=1,
        ):

            page_number = page[
                "page_number"
            ]

            content = page[
                "content"
            ]

            if progress_callback:

                progress_callback(
                    {
                        "type":
                            "page_started",

                        "page_number":
                            page_number,

                        "position":
                            position,

                        "total_pages":
                            total_pages,

                        "content":
                            content,

                        "status":
                            "processing",
                    }
                )

            prompt = (
                "DOCUMENT REVIEW\n\n"

                f"SERVICE:\n"
                f"{safe_text(service)}\n\n"

                "CUSTOMER REQUEST:\n"
                f"{compact_text(customer_request, 5000)}\n\n"

                f"PAGE {page_number} OF {total_pages}\n\n"

                "CURRENT PAGE:\n"
                f"{content}\n\n"

                "COMPLETE DOCUMENT CONTEXT:\n"
                f"{compact_text(complete_document, 10000)}\n\n"

                "Review this page intelligently.\n\n"

                "Check for genuine problems in:\n"
                "- correctness\n"
                "- completeness\n"
                "- relevance\n"
                "- grammar\n"
                "- clarity\n"
                "- consistency\n"
                "- structure\n"
                "- formatting\n"
                "- compliance with the customer's request\n\n"

                "Do not invent problems.\n"
                "Do not rewrite the page.\n"
                "Do not replace the page.\n"
                "Return review findings only."
            )

            review = self.call_groq(
                messages=[
                    {
                        "role":
                            "system",
                        "content":
                            system_prompt,
                    },
                    {
                        "role":
                            "user",
                        "content":
                            compact_text(
                                prompt,
                                REVIEW_REQUEST_CHARS,
                            ),
                    },
                ],
                output_tokens=(
                    REVIEW_OUTPUT_TOKENS
                ),
                stage="DOCUMENT_REVIEW",
                event=event,
            )

            card = {
                "type":
                    "review_card",

                "page_number":
                    page_number,

                "position":
                    position,

                "total_pages":
                    total_pages,

                # ORIGINAL PAGE CONTENT
                "content":
                    content,

                # SEPARATE REVIEW FINDINGS
                "review":
                    safe_text(
                        review
                    ),

                "status":
                    "reviewed",
            }

            page_results.append(
                card
            )

            if progress_callback:

                progress_callback(
                    card
                )

        assembled_review = (
            self.assemble_review(
                page_results
            )
        )

        result = {
            "type":
                "review_completed",

            "status":
                "completed",

            "total_pages":
                total_pages,

            "pages":
                page_results,

            "assembled_review":
                assembled_review,
        }

        if progress_callback:

            progress_callback(
                result
            )

        return result

    # ========================================================
    # REVIEW ASSEMBLY
    # ========================================================

    @staticmethod
    def assemble_review(
        pages: list[
            dict[str, Any]
        ],
    ) -> str:

        ordered = sorted(
            pages,
            key=lambda item: int(
                item.get(
                    "page_number",
                    0,
                )
            ),
        )

        parts = []

        for page in ordered:

            review = safe_text(
                page.get(
                    "review"
                )
            )

            if not review:
                continue

            parts.append(
                "PAGE "
                + str(
                    page.get(
                        "page_number"
                    )
                )
                + "\n\n"
                + review
            )

        if not parts:

            return (
                "The complete document was reviewed "
                "and no review findings were returned."
            )

        return (
            "COMPLETE DOCUMENT REVIEW\n\n"
            + "\n\n".join(
                parts
            )
        )

    # ========================================================
    # CORRECTION
    # ========================================================

    def correct_document(
        self,
        *,
        document_pages: list[Any],
        correction: str,
        service: str | None,
        context: str | None,
        progress_callback: Callable[
            [dict[str, Any]],
            None
        ] | None = None,
    ) -> dict[str, Any]:
        """
        Correct the CURRENT complete document.

        Correction is an intelligence operation.

        Pagination happens only after correction is complete.
        """

        pages = (
            self.normalize_document_pages(
                document_pages
            )
        )

        if not pages:

            raise AdaResponseError(
                "There is no document to correct.",
                stage="CORRECTION_INTAKE",
                category="EMPTY_DOCUMENT",
            )

        correction = safe_text(
            correction
        )

        if not correction:

            raise AdaResponseError(
                "Correction instruction is empty.",
                stage="CORRECTION_INTAKE",
                category="EMPTY_CORRECTION",
            )

        current_document = (
            self.assemble_document(
                pages
            )
        )

        if not current_document:

            raise AdaResponseError(
                "The current document is empty.",
                stage="CORRECTION_INTAKE",
                category="EMPTY_DOCUMENT",
            )

        active_service = (
            self.normalize_service(
                service
            )
            or safe_text(service)
            or self.service
            or "General Business Center Service"
        )

        system_prompt = (
            self.build_system_prompt(
                service=active_service,
                context=context,
            )
        )

        prompt = (
            "CURRENT DOCUMENT CORRECTION\n\n"

            f"SERVICE:\n"
            f"{active_service}\n\n"

            "CUSTOMER'S CORRECTION:\n"
            f"{compact_text(correction, 7000)}\n\n"

            "CURRENT COMPLETE DOCUMENT:\n"
            f"{compact_text(current_document, 12000)}\n\n"

            "TASK\n"
            "====\n"

            "Apply the customer's correction to the CURRENT "
            "document.\n\n"

            "Return the COMPLETE corrected document.\n\n"

            "Do not return only the changed section.\n"
            "Do not return a summary.\n"
            "Do not return an explanation.\n"
            "Do not remove unrelated useful content.\n"
            "Do not revert to an older version.\n"
            "Do not invent customer facts.\n"
            "Preserve useful structure and content.\n"
        )

        corrected = self.call_groq(
            messages=[
                {
                    "role":
                        "system",
                    "content":
                        system_prompt,
                },
                {
                    "role":
                        "user",
                    "content":
                        compact_text(
                            prompt,
                            CORRECTION_REQUEST_CHARS,
                        ),
                },
            ],
            output_tokens=(
                CORRECTION_OUTPUT_TOKENS
            ),
            stage="DOCUMENT_CORRECTION",
            event="document_correction",
        )

        corrected = clean_generation_response(
            corrected
        )

        if not corrected:

            # Never replace a valid document with empty content.
            corrected = current_document

        # ----------------------------------------------------
        # If correction visibly stopped abruptly, continue it
        # as the SAME corrected document.
        # ----------------------------------------------------

        correction_parts = [
            corrected
        ]

        for part_number in range(
            1,
            MAX_GENERATION_PARTS + 1,
        ):

            assembled = (
                "\n\n".join(
                    correction_parts
                ).strip()
            )

            if not looks_truncated(
                correction_parts[-1]
            ):

                decision = (
                    self.document_completion_check(
                        service=active_service,
                        customer_request=(
                            correction
                        ),
                        document=assembled,
                        system_prompt=system_prompt,
                    )
                )

                if decision == "COMPLETE":
                    break

            continuation_prompt = (
                "CONTINUE THE CORRECTED DOCUMENT.\n\n"

                "The text below is the CURRENT corrected "
                "document already written.\n\n"

                f"{assembled}\n\n"

                "Continue exactly from the logical endpoint.\n"
                "Do not restart.\n"
                "Do not repeat existing content.\n"
                "Return only the missing continuation.\n"
                "Preserve the correction already applied.\n"
            )

            continuation = self.call_groq(
                messages=[
                    {
                        "role":
                            "system",
                        "content":
                            system_prompt,
                    },
                    {
                        "role":
                            "user",
                        "content":
                            compact_text(
                                continuation_prompt,
                                CORRECTION_REQUEST_CHARS,
                            ),
                    },
                ],
                output_tokens=(
                    CORRECTION_OUTPUT_TOKENS
                ),
                stage="DOCUMENT_CORRECTION_CONTINUATION",
                event="document_correction_continuation",
            )

            continuation = clean_generation_response(
                continuation
            )

            if not continuation:
                break

            correction_parts.append(
                continuation
            )

        corrected = (
            "\n\n".join(
                correction_parts
            ).strip()
        )

        corrected_pages = (
            self.document_to_pages(
                corrected
            )
        )

        if not corrected_pages:

            raise AdaResponseError(
                "Corrected document could not be paginated.",
                stage="DOCUMENT_CORRECTION",
                category="EMPTY_CORRECTED_DOCUMENT",
            )

        result = {
            "type":
                "correction_completed",

            "status":
                "completed",

            "total_pages":
                len(corrected_pages),

            "pages":
                corrected_pages,

            "document_text":
                corrected,
        }

        if progress_callback:

            progress_callback(
                result
            )

        return result

    # ========================================================
    # DOCUMENT ASSEMBLY
    # ========================================================

    @staticmethod
    def assemble_document(
        pages: list[
            dict[str, Any]
        ],
    ) -> str:
        """
        PURE DOCUMENT ASSEMBLY.

        No intelligence is called here.

        Pages are joined in page order to reconstruct the
        current document.
        """

        if not pages:
            return ""

        ordered = sorted(
            pages,
            key=lambda item: int(
                item.get(
                    "page_number",
                    0,
                )
            ),
        )

        parts = []

        for page in ordered:

            content = safe_text(
                page.get(
                    "content"
                )
            )

            if content:
                parts.append(
                    content
                )

        return "\n\n".join(
            parts
        ).strip()

    # ========================================================
    # NORMAL CHAT
    # ========================================================

    def respond(
        self,
        message: str,
        service: str | None = None,
        event: str | None = None,
        context: str | None = None,
    ) -> str:

        message = safe_text(
            message
        )

        if not message:

            return (
                "Please tell me what you "
                "would like me to help you with."
            )

        if service:
            self.set_service(
                service
            )

        active_service = (
            self.normalize_service(
                self.service
            )
        )

        system_prompt = (
            self.build_system_prompt(
                service=active_service,
                context=context,
            )
        )

        messages = [
            {
                "role":
                    "system",
                "content":
                    system_prompt,
            }
        ]

        for item in self.history[
            -MAX_HISTORY_MESSAGES:
        ]:

            messages.append(
                {
                    "role":
                        item["role"],

                    "content":
                        compact_text(
                            item["content"],
                            MAX_HISTORY_MESSAGE_CHARS,
                        ),
                }
            )

        if event:

            messages.append(
                {
                    "role":
                        "system",

                    "content":
                        (
                            "CURRENT APPLICATION EVENT\n"
                            + safe_text(event)
                        ),
                }
            )

        messages.append(
            {
                "role":
                    "user",

                "content":
                    compact_text(
                        message,
                        MAX_USER_MESSAGE_CHARS,
                    ),
            }
        )

        response = self.call_groq(
            messages=messages,
            output_tokens=700,
            stage="NORMAL_RESPONSE",
            event=event,
        )

        self.add_history(
            "user",
            message,
        )

        self.add_history(
            "assistant",
            response,
        )

        return response

    # ========================================================
    # EVENT HELPERS
    # ========================================================

    @staticmethod
    def is_review_event(
        event: str | None,
    ) -> bool:

        return (
            safe_text(
                event
            ).lower()
            in REVIEW_EVENTS
        )

    @staticmethod
    def is_correction_event(
        event: str | None,
    ) -> bool:

        return (
            safe_text(
                event
            ).lower()
            in CORRECTION_EVENTS
        )


# ============================================================
# DIAGNOSTIC
# ============================================================

if __name__ == "__main__":

    print()
    print("=" * 78)
    print("NAIJA POCKET BUSINESS CENTER")
    print("ADA RESPONSE INTELLIGENCE ENGINE")
    print("=" * 78)

    print(
        "Model:",
        MODEL,
    )

    print(
        "Groq package:",
        "READY"
        if Groq is not None
        else "MISSING",
    )

    print(
        "API key:",
        "CONFIGURED"
        if API_KEY
        else "MISSING",
    )

    print(
        "Intelligence-first service requirements:",
        "ENABLED",
    )

    print(
        "Global page-count requirement:",
        "DISABLED",
    )

    print(
        "Complete document generation:",
        "ENABLED",
    )

    print(
        "Generation continuation:",
        "ENABLED",
    )

    print(
        "Strict completion verification:",
        "ENABLED",
    )

    print(
        "Document-to-page handoff:",
        "ENABLED",
    )

    print(
        "Pagination intelligence calls:",
        "NONE",
    )

    print(
        "Original page preservation:",
        "ENABLED",
    )

    print(
        "Separate review findings:",
        "ENABLED",
    )

    print(
        "Complete-document correction:",
        "ENABLED",
    )

    print(
        "Keyword-only service logic:",
        "DISABLED",
    )

    print(
        "Document ownership:",
        "APPLICATION",
    )

    print("=" * 78)
