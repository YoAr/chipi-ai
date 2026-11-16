"""Language detection and skill tagging for postings (spec v2 §6.1 steps 7-8). Plain code.

Skill tagging is keyword matching against a tech/data vocabulary (EN + NL synonyms) plus
the freelancer's own skill names. `taxonomy_id` stays None until the vocabulary is linked
to ESCO skill URIs (spec: ESCO, verify); nothing here pretends to be an ESCO id.
"""

from __future__ import annotations

import re

# Canonical skill -> synonyms (lowercase). Matched on word boundaries.
VOCABULARY: dict[str, tuple[str, ...]] = {
    "Python": ("python",),
    "SQL": ("sql", "postgresql", "postgres", "mysql", "t-sql"),
    "dbt": ("dbt",),
    "Spark": ("spark", "pyspark", "databricks"),
    "Airflow": ("airflow",),
    "Kafka": ("kafka",),
    "Data engineering": ("data engineering", "data engineer", "data pipelines", "datapipelines", "etl", "elt"),
    "Data analysis": ("data analysis", "data analyst", "data-analist", "data analist", "data-analyse"),
    "Data science": ("data science", "data scientist"),
    "Machine learning": ("machine learning", "ml engineer", "mlops", "deep learning"),
    "LLM / generative AI": ("llm", "llms", "generative ai", "genai", "large language model", "rag"),
    "Power BI": ("power bi", "powerbi"),
    "Tableau": ("tableau",),
    "Looker": ("looker",),
    "Snowflake": ("snowflake",),
    "BigQuery": ("bigquery",),
    "AWS": ("aws", "amazon web services"),
    "Azure": ("azure",),
    # Not bare "gcp": in pharma/clinical postings it means Good Clinical Practice
    "GCP": ("google cloud", "google cloud platform"),
    "Kubernetes": ("kubernetes", "k8s"),
    "Docker": ("docker",),
    "Terraform": ("terraform",),
    "DevOps": ("devops", "ci/cd", "cicd"),
    "Backend development": ("backend", "back-end", "back end"),
    "Frontend development": ("frontend", "front-end", "front end"),
    "JavaScript": ("javascript",),
    "TypeScript": ("typescript",),
    "React": ("react", "react.js", "reactjs"),
    "Node.js": ("node.js", "nodejs"),
    "Java": ("java",),
    "Kotlin": ("kotlin",),
    "Go": ("golang",),
    "C#/.NET": ("c#", ".net", "dotnet"),
    "FastAPI": ("fastapi",),
    "Django": ("django",),
    "API design": ("api design", "rest api", "restful", "graphql"),
    "Security": ("security engineer", "cybersecurity", "information security", "informatiebeveiliging"),
    "Product management": ("product manager", "product owner", "productmanager"),
    "UX design": ("ux", "ui/ux", "user experience", "product designer"),
    "Project management": ("project manager", "projectmanager", "projectleider", "scrum master"),
}

_NL = {"de", "het", "een", "en", "van", "voor", "met", "wij", "je", "jij", "ons", "onze", "zijn",
       "bij", "naar", "ook", "als", "werk", "werken", "ervaring", "functie", "wat", "wie", "jouw"}
_EN = {"the", "and", "a", "of", "for", "with", "we", "you", "our", "are", "to", "is", "in",
       "your", "work", "experience", "role", "what", "who", "will", "team"}
_WORD = re.compile(r"[a-zà-ÿ]+")


def detect_language(text: str, hint: str | None = None) -> str:
    """'nl' or 'en'. A posting's own translation key (hint) wins when it is one of the two."""
    if hint in ("nl", "en"):
        return hint
    words = _WORD.findall((text or "").lower())
    nl = sum(w in _NL for w in words)
    en = sum(w in _EN for w in words)
    return "nl" if nl > en else "en"


def _pattern(term: str) -> re.Pattern:
    # Word boundaries that also work for terms like "c#", ".net" and "node.js"
    return re.compile(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])")


_COMPILED = {skill: [_pattern(t) for t in terms] for skill, terms in VOCABULARY.items()}


def tag_skills(text: str, extra_skills: list[str] | None = None) -> list[str]:
    """Canonical skill names found in the text, vocabulary first, then the freelancer's own."""
    low = (text or "").lower()
    found = [skill for skill, pats in _COMPILED.items() if any(p.search(low) for p in pats)]
    for skill in extra_skills or []:
        s = (skill or "").strip()
        if s and s not in found and _pattern(s.lower()).search(low):
            found.append(s)
    return found
