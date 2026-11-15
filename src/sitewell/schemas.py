"""The shapes the model's answers must have. Checked by Claude Code and again here."""

from __future__ import annotations

from typing import Any

ACTION_TYPES = ["snippet", "title", "description", "internal-link", "schema", "text-edit", "new-page", "technical", "hand-task"]

_str = {"type": "string"}
_strs = {"type": "array", "items": {"type": "string"}}

DECISION: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["week_summary", "pause", "actions", "not_doing", "plan_revision"],
    "properties": {
        "week_summary": _str,
        "pause": {
            "type": "object",
            "additionalProperties": False,
            "required": ["recommended", "reasons"],
            "properties": {"recommended": {"type": "boolean"}, "reasons": _strs},
        },
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "type", "url", "query", "evidence", "why", "expected", "instruction", "risk"],
                "properties": {
                    "id": _str,
                    "type": {"type": "string", "enum": ACTION_TYPES},
                    "url": _str,
                    "query": _str,
                    "evidence": _strs,
                    "why": _str,
                    "expected": _str,
                    "instruction": _str,
                    "risk": {"type": "string", "enum": ["low", "medium", "high"]},
                },
            },
        },
        "not_doing": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["what", "why"],
                "properties": {"what": _str, "why": _str},
            },
        },
        "plan_revision": _str,
    },
}

_link = {
    "type": "object",
    "additionalProperties": False,
    "required": ["from_url", "to_url", "anchor", "sentence"],
    "properties": {"from_url": _str, "to_url": _str, "anchor": _str, "sentence": _str},
}
_edit = {
    "type": "object",
    "additionalProperties": False,
    "required": ["section", "before", "after"],
    "properties": {"section": _str, "before": _str, "after": _str},
}
_source = {"type": "object", "additionalProperties": False, "required": ["title", "url"], "properties": {"title": _str, "url": _str}}

DRAFTS: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["drafts"],
    "properties": {
        "drafts": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["action_id", "summary", "new_title", "new_meta_description", "links", "schema_jsonld", "text_edits", "article", "hand_task", "notes"],
                "properties": {
                    "action_id": _str,
                    "summary": {"type": "string", "description": "One short English line saying what this draft changes."},
                    "new_title": {"type": "string", "description": "The page's new <title> text, exactly as it should be served. Empty unless the action changes the title."},
                    "new_meta_description": {"type": "string", "description": "The page's new meta description text, exactly as it should be served. Empty unless the action changes the description."},
                    "links": {"type": "array", "items": _link},
                    "schema_jsonld": _str,
                    "text_edits": {"type": "array", "items": _edit},
                    "article": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["slug", "title", "description", "h1", "body_markdown", "sources"],
                        "properties": {
                            "slug": _str, "title": _str, "description": _str, "h1": _str, "body_markdown": _str,
                            "sources": {"type": "array", "items": _source},
                        },
                    },
                    "hand_task": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["who", "steps", "minutes"],
                        "properties": {"who": _str, "steps": _strs, "minutes": {"type": "integer"}},
                    },
                    "notes": _str,
                },
            },
        }
    },
}

CHECKS: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["checks"],
    "properties": {
        "checks": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["action_id", "new_medical_claims", "language_issues", "overlaps", "breaks_never_rules", "needs_doctor"],
                "properties": {
                    "action_id": _str,
                    "new_medical_claims": _strs,
                    "language_issues": _strs,
                    "overlaps": _strs,
                    "breaks_never_rules": {"type": "array", "items": _str, "description": "Only real breaches of a rule under '## Never' in rules.md, each naming the rule. Empty when there are none."},
                    "needs_doctor": {"type": "array", "items": _str, "description": "Things in the draft that rules.md lists under '## Needs the doctor'. Empty when there are none."},
                },
            },
        }
    },
}


def validate(doc: Any, schema: dict[str, Any]) -> list[str]:
    """Problems with `doc` against `schema`, as readable lines."""
    from jsonschema import Draft202012Validator

    v = Draft202012Validator(schema)
    return [f"{'/'.join(str(p) for p in e.absolute_path) or '(top)'}: {e.message}" for e in v.iter_errors(doc)][:20]
